import json
from pathlib import Path
from typing import Any

import httpx
import respx

from coe_wizard.clickup import API_BASE, ClickUpClient, RateLimiter
from coe_wizard.scraper import scrape_space

WS, SPACE = "w1", "s1"


async def _nosleep(_s: float) -> None:
    return None


def client() -> ClickUpClient:
    return ClickUpClient("tok", limiter=RateLimiter(10_000, sleep=_nosleep), sleep=_nosleep)


def mock_space(docs: dict[str, dict[str, Any]], pages: dict[str, list[dict[str, Any]]]) -> dict:
    respx.get(f"{API_BASE}/v2/space/{SPACE}/folder").mock(
        return_value=httpx.Response(200, json={"folders": [{"id": "f1", "lists": [{"id": "l1"}]}]})
    )
    respx.get(f"{API_BASE}/v2/space/{SPACE}/list").mock(
        return_value=httpx.Response(200, json={"lists": [{"id": "l2"}]})
    )

    def docs_for(request: httpx.Request) -> httpx.Response:
        parent = request.url.params["parent_id"]
        return httpx.Response(
            200, json={"docs": [d for d in docs.values() if d["parent"] == parent]}
        )

    respx.get(f"{API_BASE}/v3/workspaces/{WS}/docs").mock(side_effect=docs_for)
    calls: dict[str, int] = {}

    def pages_for(request: httpx.Request, doc_id: str) -> httpx.Response:
        calls[doc_id] = calls.get(doc_id, 0) + 1
        return httpx.Response(200, json=pages[doc_id])

    respx.get(url__regex=rf"{API_BASE}/v3/workspaces/{WS}/docs/(?P<doc_id>[^/]+)/pages").mock(
        side_effect=pages_for
    )
    return calls


@respx.mock
async def test_scrapes_docs_from_every_container_and_nested_pages(tmp_path: Path) -> None:
    docs = {
        "d1": {"id": "d1", "name": "Space doc", "parent": SPACE, "date_updated": "1"},
        "d2": {"id": "d2", "name": "List doc", "parent": "l1", "date_updated": "1"},
    }
    pages = {
        "d1": [
            {
                "id": "p1",
                "name": "Intro",
                "content": "# Hi",
                "pages": [{"id": "p2", "name": "Child", "content": "child text"}],
            }
        ],
        "d2": [{"id": "p3", "name": "Only", "content": "list doc"}],
    }
    mock_space(docs, pages)
    stats = await scrape_space(client(), WS, SPACE, tmp_path)
    assert stats.pages_written == 3
    assert (tmp_path / "d1" / "p2.md").read_text() == "child text"
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["docs"]["d1"]["pages"]["p2"]["url"] == (
        f"https://app.clickup.com/{WS}/v/dc/d1/p2"
    )


@respx.mock
async def test_incremental_skips_unchanged_docs_and_retires_deleted(tmp_path: Path) -> None:
    docs = {
        "d1": {"id": "d1", "name": "A", "parent": SPACE, "date_updated": "1"},
        "d2": {"id": "d2", "name": "B", "parent": SPACE, "date_updated": "1"},
    }
    pages = {
        "d1": [{"id": "p1", "content": "a"}],
        "d2": [{"id": "p2", "content": "b"}, {"id": "p3", "content": "c"}],
    }
    calls = mock_space(docs, pages)
    await scrape_space(client(), WS, SPACE, tmp_path)

    del docs["d1"]
    docs["d2"]["date_updated"] = "2"
    pages["d2"] = [{"id": "p2", "content": "b"}]  # p3 removed, p2 unchanged
    stats = await scrape_space(client(), WS, SPACE, tmp_path)

    assert calls == {"d1": 1, "d2": 2}
    assert stats.pages_unchanged == 1 and stats.pages_deleted == 1 and stats.docs_deleted == 1
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["docs"]["d1"]["deleted"] is True
    assert manifest["docs"]["d2"]["pages"]["p3"]["deleted"] is True

    stats = await scrape_space(client(), WS, SPACE, tmp_path)
    assert stats.docs_skipped == 1 and calls["d2"] == 2
