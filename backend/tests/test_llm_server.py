import asyncio
import hashlib
import sys
from pathlib import Path

import httpx
import pytest
import respx

from coe_wizard.llm_server import (
    Artifact,
    LlamaServerManager,
    LLMUnavailable,
    ServerConfig,
    fetch_verified,
    pid_alive,
)

# Stands in for llama-server: answers /health on the --port it's given.
STUB = f"""#!{sys.executable}
import http.server, sys
port = int(sys.argv[sys.argv.index("--port") + 1])
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
"""


def make_cfg(tmp_path: Path, idle_timeout: float = 0) -> ServerConfig:
    cfg = ServerConfig(cache_dir=tmp_path, idle_timeout=idle_timeout, load_timeout=20)
    cfg.binary.parent.mkdir(parents=True)
    cfg.binary.write_text(STUB)
    cfg.binary.chmod(0o755)
    model = cfg.model
    model.path.parent.mkdir(parents=True)
    model.path.write_bytes(b"gguf")
    model.marker.write_text(model.sha256)
    return cfg


async def test_leases_share_one_server_and_the_last_release_stops_it(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    app, cli = LlamaServerManager(cfg), LlamaServerManager(cfg)  # e.g. the app and the build
    a = await app.acquire()
    b = await cli.acquire()
    assert a.url == b.url
    async with httpx.AsyncClient() as http:
        assert (await http.get(f"{a.url}/health")).status_code == 200
    pid = int(cfg.cache_dir.joinpath("llama.lock").read_text().split('"pid": ')[1].split(",")[0])
    await cli.release(b.id)
    assert pid_alive(pid)  # the app still holds a lease
    await app.release(a.id)
    assert not pid_alive(pid) and app.running_url() is None


async def test_idle_timeout_keeps_the_server_warm(tmp_path: Path) -> None:
    mgr = LlamaServerManager(make_cfg(tmp_path, idle_timeout=0.3))
    lease = await mgr.acquire()
    await mgr.release(lease.id)
    assert mgr.running_url() == lease.url
    again = await mgr.acquire()  # reused, and the pending idle stop is cancelled
    assert again.url == lease.url
    await mgr.release(again.id)
    await asyncio.sleep(0.6)
    assert mgr.running_url() is None


async def test_missing_model_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(LLMUnavailable, match="isn't downloaded"):
        await LlamaServerManager(ServerConfig(cache_dir=tmp_path)).acquire()


@respx.mock
async def test_download_resumes_and_verifies(tmp_path: Path) -> None:
    payload = b"0123456789" * 1000
    art = Artifact(
        "https://example.test/m.gguf", tmp_path / "m.gguf", hashlib.sha256(payload).hexdigest()
    )
    part = tmp_path / "m.gguf.part"
    part.write_bytes(payload[:4000])
    route = respx.get(art.url).mock(return_value=httpx.Response(206, content=payload[4000:]))
    seen: list[int] = []
    async with httpx.AsyncClient() as http:
        await fetch_verified(art, lambda _n, done, _t: seen.append(done), http)
    assert route.calls.last.request.headers["Range"] == "bytes=4000-"
    assert art.path.read_bytes() == payload and art.verified() and not part.exists()
    assert seen[-1] == len(payload)


@respx.mock
async def test_sha_mismatch_is_rejected(tmp_path: Path) -> None:
    art = Artifact("https://example.test/m.gguf", tmp_path / "m.gguf", "0" * 64)
    respx.get(art.url).mock(return_value=httpx.Response(200, content=b"tampered"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMUnavailable, match="SHA256 mismatch"):
            await fetch_verified(art, lambda *_: None, http)
    assert not art.path.exists() and not (tmp_path / "m.gguf.part").exists()
