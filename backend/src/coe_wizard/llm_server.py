"""Provisioning and sharing of the local llama-server.

One llama-server is shared by the running app (interviews) and `build_ontology.sh`, so the ~18 GB
model is never loaded twice. Coordination is a JSON lock file in .cache/ (guarded with fcntl):

    {"pid": <llama-server pid>, "port": <port>, "leases": {"<holder pid>:<id>": <started at>}}

Whoever needs the model takes a lease, starting llama-server if none is running (in its own
session, so it outlives whoever spawned it). Releasing the last live lease stops the server, after
an idle timeout in the app (so consecutive interview steps stay fast) or immediately from the CLI.
Leases held by dead processes are ignored.

Downloads are pinned and SHA256-verified once; a `.verified` marker avoids re-hashing 18 GB on
every start.
"""

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import tarfile
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from .llm import LlamaServerBackend, LLMBackend, LLMError

LLAMA_TAG = "b11430"
LLAMA_ASSET = f"llama-{LLAMA_TAG}-bin-ubuntu-vulkan-x64.tar.gz"
LLAMA_SHA256 = "99652a5a753b4045a885c5c3f5565c05a59d0391ac2cb207a327c7d0f72917f2"
LLAMA_URL_DL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{LLAMA_ASSET}"

MODEL_FILE = "Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"
MODEL_SHA256 = "6c997b8af17debdfb01d890214400ccbab00db6acc0ba8da5de1cc906c4774d0"
MODEL_URL = (
    f"https://huggingface.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF/resolve/main/{MODEL_FILE}"
)

# Shown on the consent prompt and used for the free-space check.
APPROX_DOWNLOAD_BYTES = 18_800_000_000
FREE_SPACE_MARGIN = 1_000_000_000

LLMState = Literal["missing", "downloading", "stopped", "starting", "ready", "failed"]


class LLMUnavailable(LLMError):
    """The model can't be used right now (not downloaded, failed to start, ...)."""


@dataclass(frozen=True)
class Artifact:
    url: str
    path: Path
    sha256: str

    @property
    def marker(self) -> Path:
        return self.path.with_name(self.path.name + ".verified")

    def verified(self) -> bool:
        try:
            return self.path.exists() and self.marker.read_text().strip() == self.sha256
        except FileNotFoundError:
            return False


@dataclass(frozen=True)
class ServerConfig:
    cache_dir: Path
    device: str = "Vulkan0"
    n_cpu_moe: int = 20
    ctx_size: int = 32768
    idle_timeout: float = 600
    load_timeout: float = 600

    @classmethod
    def from_env(cls, cache_dir: Path, idle_timeout: float) -> ServerConfig:
        """The same knobs build_ontology.sh has always read."""
        return cls(
            cache_dir=cache_dir,
            device=os.environ.get("LLAMA_DEVICE", "Vulkan0"),
            n_cpu_moe=int(os.environ.get("N_CPU_MOE", "20")),
            ctx_size=int(os.environ.get("CTX_SIZE", "32768")),
            idle_timeout=idle_timeout,
        )

    @property
    def llama_archive(self) -> Artifact:
        return Artifact(LLAMA_URL_DL, self.cache_dir / LLAMA_ASSET, LLAMA_SHA256)

    @property
    def model(self) -> Artifact:
        return Artifact(MODEL_URL, self.cache_dir / "models" / MODEL_FILE, MODEL_SHA256)

    @property
    def binary(self) -> Path:
        return self.cache_dir / f"llama-{LLAMA_TAG}" / "llama-server"

    @property
    def log_path(self) -> Path:
        return self.cache_dir / "llama-server.log"

    def files_ready(self) -> bool:
        return self.binary.exists() and self.model.verified()


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # An unreaped child still answers kill(0); treat zombies as dead.
    with contextlib.suppress(OSError):
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    return True


def _holder_alive(lease_key: str) -> bool:
    pid, _, _ = lease_key.partition(":")
    return pid.isdigit() and pid_alive(int(pid))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- Download ------------------------------------------------------------------------------

DownloadProgress = Callable[[str, int, int], None]  # file name, bytes done, bytes total


def _hash_file(path: Path) -> hashlib._Hash:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h


async def fetch_verified(
    art: Artifact, progress: DownloadProgress, http: httpx.AsyncClient
) -> None:
    """Resumable download to `<path>.part`, verified against the pinned SHA256 before use."""
    if art.verified():
        return
    art.path.parent.mkdir(parents=True, exist_ok=True)
    if art.path.exists():
        if (await asyncio.to_thread(_hash_file, art.path)).hexdigest() == art.sha256:
            art.marker.write_text(art.sha256)
            return
        art.path.unlink()
    part = art.path.with_name(art.path.name + ".part")
    done = part.stat().st_size if part.exists() else 0
    digest = await asyncio.to_thread(_hash_file, part) if done else hashlib.sha256()
    headers = {"Range": f"bytes={done}-"} if done else {}
    async with http.stream("GET", art.url, headers=headers) as resp:
        if resp.status_code == 416:  # the part file is already complete
            pass
        elif resp.status_code == 200 and done:  # server ignored the Range: start over
            done, digest = 0, hashlib.sha256()
            part.unlink()
        elif resp.status_code not in (200, 206):
            raise LLMUnavailable(f"Download of {art.path.name} failed: HTTP {resp.status_code}")
        if resp.status_code != 416:
            total = done + int(resp.headers.get("Content-Length", 0))
            with part.open("ab") as f:
                async for block in resp.aiter_bytes(1 << 20):
                    f.write(block)
                    digest.update(block)
                    done += len(block)
                    progress(art.path.name, done, total)
    if digest.hexdigest() != art.sha256:
        part.unlink(missing_ok=True)
        raise LLMUnavailable(f"SHA256 mismatch for {art.path.name}; refusing to use it")
    part.replace(art.path)
    art.marker.write_text(art.sha256)


async def provision(
    cfg: ServerConfig, progress: DownloadProgress, http: httpx.AsyncClient | None = None
) -> None:
    """Download and unpack the pinned llama.cpp build and model into the cache dir."""
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    missing = [a for a in (cfg.llama_archive, cfg.model) if not a.verified()]
    if missing:
        free = shutil.disk_usage(cfg.cache_dir).free
        partial = sum(
            p.stat().st_size
            for a in missing
            if (p := a.path.with_name(a.path.name + ".part")).exists()
        )
        needed = (APPROX_DOWNLOAD_BYTES if cfg.model in missing else 200_000_000) - partial
        if free < needed + FREE_SPACE_MARGIN:
            raise LLMUnavailable(
                f"Not enough disk space in {cfg.cache_dir}: need about "
                f"{(needed + FREE_SPACE_MARGIN) / 1e9:.1f} GB, have {free / 1e9:.1f} GB"
            )
    client = http or httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(60, connect=15))
    try:
        await fetch_verified(cfg.llama_archive, progress, client)
        if not cfg.binary.exists():
            with tarfile.open(cfg.llama_archive.path) as tar:
                await asyncio.to_thread(tar.extractall, cfg.cache_dir, filter="data")
        await fetch_verified(cfg.model, progress, client)
    finally:
        if http is None:
            await client.aclose()


# --- Shared server -------------------------------------------------------------------------


@dataclass(frozen=True)
class Lease:
    id: str
    url: str


class LlamaServerManager:
    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self.lock_path = cfg.cache_dir / "llama.lock"
        self._proc: subprocess.Popen[bytes] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._own: set[str] = set()
        self.starting = 0

    @contextlib.contextmanager
    def _state(self) -> Iterator[dict[str, Any]]:
        self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                raw = fh.read()
                try:
                    state: dict[str, Any] = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    state = {}
                if not pid_alive(state.get("pid")):
                    state = {}
                state["leases"] = {
                    k: v for k, v in state.get("leases", {}).items() if _holder_alive(k)
                }
                yield state
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps(state) if state.get("pid") else "")
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def running_url(self) -> str | None:
        with self._state() as st:
            return f"http://127.0.0.1:{st['port']}" if st.get("pid") else None

    def _spawn(self) -> tuple[int, int]:
        if self._proc is not None:
            self._proc.poll()  # reap a previous server we started
        port = free_port()
        cmd = [
            str(self.cfg.binary),
            "-m", str(self.cfg.model.path),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--device", self.cfg.device,
            "-ngl", "999",
            "--n-cpu-moe", str(self.cfg.n_cpu_moe),
            "-c", str(self.cfg.ctx_size),
        ]  # fmt: skip
        with self.cfg.log_path.open("wb") as log:
            self._proc = subprocess.Popen(
                cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
        return self._proc.pid, port

    async def acquire(self) -> Lease:
        if not self.cfg.files_ready():
            raise LLMUnavailable("The model isn't downloaded yet")
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None
        lease_id = f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
        with self._state() as st:
            if not st.get("pid"):
                pid, port = self._spawn()
                st.update(pid=pid, port=port)
            st["leases"][lease_id] = time.time()
            pid, url = st["pid"], f"http://127.0.0.1:{st['port']}"
        self._own.add(lease_id)
        try:
            await self._wait_healthy(url, pid)
        except BaseException:
            await self.release(lease_id)
            raise
        return Lease(lease_id, url)

    async def _wait_healthy(self, url: str, pid: int) -> None:
        self.starting += 1
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                deadline = time.monotonic() + self.cfg.load_timeout
                while time.monotonic() < deadline:
                    with contextlib.suppress(httpx.TransportError):
                        if (await http.get(f"{url}/health")).status_code == 200:
                            return
                    if not pid_alive(pid):
                        raise LLMUnavailable(
                            f"llama-server exited while loading; see {self.cfg.log_path}:\n"
                            + self._log_tail()
                        )
                    await asyncio.sleep(1)
            raise LLMUnavailable("llama-server never became healthy")
        finally:
            self.starting -= 1

    def _log_tail(self, lines: int = 15) -> str:
        try:
            return "\n".join(self.cfg.log_path.read_text(errors="replace").splitlines()[-lines:])
        except FileNotFoundError:
            return ""

    async def release(self, lease_id: str) -> None:
        self._own.discard(lease_id)
        with self._state() as st:
            st["leases"].pop(lease_id, None)
            busy = bool(st["leases"])
        if busy:
            return
        if self.cfg.idle_timeout <= 0:
            self.stop_if_idle()
        else:
            self._idle_task = asyncio.create_task(self._idle_stop())

    async def _idle_stop(self) -> None:
        await asyncio.sleep(self.cfg.idle_timeout)
        self.stop_if_idle()

    def stop_if_idle(self) -> None:
        with self._state() as st:
            if st["leases"] or not st.get("pid"):
                return
            pid = st["pid"]
            st.clear()
        self._terminate(pid)

    def _terminate(self, pid: int) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 15
        while pid_alive(pid) and time.monotonic() < deadline:
            if self._proc is not None and self._proc.pid == pid:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self._proc.wait(0.2)
            else:
                time.sleep(0.2)
        if pid_alive(pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)

    async def shutdown(self) -> None:
        """Drop this process's leases and stop the server if nobody else needs it."""
        if self._idle_task is not None:
            self._idle_task.cancel()
        with self._state() as st:
            for lease_id in self._own:
                st["leases"].pop(lease_id, None)
        self._own.clear()
        self.stop_if_idle()


# --- Providers: what the app and CLI use -----------------------------------------------------


class LLMProvider(Protocol):
    def status(self) -> dict[str, Any]: ...

    def start_download(self) -> bool: ...

    def cancel_download(self) -> bool: ...

    def warm(self) -> None: ...

    def lease(self) -> contextlib.AbstractAsyncContextManager[LLMBackend]: ...

    async def aclose(self) -> None: ...


def _status(
    state: LLMState, error: str | None = None, download: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "state": state,
        "error": error,
        "download": download,
        "approx_bytes": APPROX_DOWNLOAD_BYTES,
    }


class ManagedProvider:
    """The app's view of the shared local server: consented download, warm-up, leases."""

    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self.manager = LlamaServerManager(cfg)
        self.error: str | None = None
        self._download: asyncio.Task[None] | None = None
        self._progress: dict[str, Any] | None = None
        self._warm: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        if self._download is not None and not self._download.done():
            return _status("downloading", None, self._progress)
        if not self.cfg.files_ready():
            return _status("missing", self.error)
        if self.manager.starting:
            return _status("starting")
        if self.manager.running_url():
            return _status("ready")
        return _status("failed" if self.error else "stopped", self.error)

    def start_download(self) -> bool:
        if self._download is not None and not self._download.done():
            return False
        self.error = None
        self._progress = {"file": "", "done": 0, "total": 0}

        def progress(name: str, done: int, total: int) -> None:
            self._progress = {"file": name, "done": done, "total": total}

        async def run() -> None:
            try:
                await provision(self.cfg, progress)
            except asyncio.CancelledError:
                self.error = "Download cancelled; it resumes where it stopped"
                raise
            except Exception as exc:  # surfaced through status()
                self.error = str(exc)

        self._download = asyncio.create_task(run())
        return True

    def cancel_download(self) -> bool:
        if self._download is None or self._download.done():
            return False
        self._download.cancel()
        return True

    def warm(self) -> None:
        """Start loading the model in the background; the idle timer keeps it loaded."""
        if not self.cfg.files_ready() or (self._warm is not None and not self._warm.done()):
            return

        async def run() -> None:
            try:
                async with self.lease():
                    pass
            except Exception as exc:  # surfaced through status()
                self.error = str(exc)

        self._warm = asyncio.create_task(run())

    @contextlib.asynccontextmanager
    async def lease(self) -> AsyncIterator[LLMBackend]:
        try:
            lease = await self.manager.acquire()
        except LLMError as exc:
            self.error = str(exc)
            raise
        self.error = None
        backend = LlamaServerBackend(lease.url)
        try:
            yield backend
        finally:
            await backend.aclose()
            await self.manager.release(lease.id)

    async def aclose(self) -> None:
        for task in (self._download, self._warm):
            if task is not None:
                task.cancel()
        await self.manager.shutdown()


class ExternalProvider:
    """LLAMA_URL: an already-running OpenAI-compatible server the app just connects to."""

    def __init__(self, url: str) -> None:
        self.url = url

    def status(self) -> dict[str, Any]:
        return _status("ready")

    def start_download(self) -> bool:
        return False

    def cancel_download(self) -> bool:
        return False

    def warm(self) -> None:
        return None

    @contextlib.asynccontextmanager
    async def lease(self) -> AsyncIterator[LLMBackend]:
        backend = LlamaServerBackend(self.url)
        try:
            yield backend
        finally:
            await backend.aclose()

    async def aclose(self) -> None:
        return None
