"""Pulls ClickUp Docs from one space into the local, gitignored corpus."""

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .clickup import ClickUpClient

# ClickUp v3 parent_type values for the containers a space's docs can hang off.
PARENT_SPACE, PARENT_FOLDER, PARENT_LIST = "SPACE", "FOLDER", "LIST"


def page_url(workspace_id: str, doc_id: str, page_id: str) -> str:
    return f"https://app.clickup.com/{workspace_id}/v/dc/{doc_id}/{page_id}"


@dataclass
class ScrapeStats:
    docs_seen: int = 0
    docs_skipped: int = 0
    pages_written: int = 0
    pages_unchanged: int = 0
    docs_deleted: int = 0
    pages_deleted: int = 0
    messages: list[str] = field(default_factory=list)


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"docs": {}}
    return json.loads(path.read_text())


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, path)


def _flatten_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    stack = list(reversed(pages))
    while stack:
        page = stack.pop()
        out.append(page)
        stack.extend(reversed(page.get("pages") or []))
    return out


async def scrape_space(
    client: ClickUpClient,
    workspace_id: str,
    space_id: str,
    corpus_dir: Path,
    progress: Callable[[str], None] = lambda _msg: None,
) -> ScrapeStats:
    stats = ScrapeStats()

    def log(msg: str) -> None:
        stats.messages.append(msg)
        progress(msg)

    manifest_path = corpus_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    known_docs: dict[str, Any] = manifest.setdefault("docs", {})

    # The v3 parent filter only matches direct parents, so walk every container in the space.
    parents: list[tuple[str, str]] = [(space_id, PARENT_SPACE)]
    log("Listing folders and lists")
    for folder in await client.space_folders(space_id):
        parents.append((str(folder["id"]), PARENT_FOLDER))
        parents.extend((str(lst["id"]), PARENT_LIST) for lst in folder.get("lists") or [])
    parents.extend((str(lst["id"]), PARENT_LIST) for lst in await client.space_lists(space_id))

    found: dict[str, dict[str, Any]] = {}
    for parent_id, parent_type in parents:
        async for doc in client.docs(workspace_id, parent_id, parent_type):
            found.setdefault(str(doc["id"]), doc)
    log(f"Found {len(found)} docs")

    for doc_id, doc in found.items():
        stats.docs_seen += 1
        entry: dict[str, Any] = known_docs.get(doc_id) or {"pages": {}}
        doc_updated = doc.get("date_updated")
        if (
            doc_updated is not None
            and entry.get("date_updated") == doc_updated
            and not entry.get("deleted")
        ):
            stats.docs_skipped += 1
            continue

        log(f"Fetching doc {doc.get('name', doc_id)}")
        pages = _flatten_pages(await client.doc_pages(workspace_id, doc_id))
        seen_pages: set[str] = set()
        known_pages: dict[str, Any] = entry.setdefault("pages", {})
        for page in pages:
            page_id = str(page["id"])
            seen_pages.add(page_id)
            content = page.get("content") or ""
            digest = hashlib.sha256(content.encode()).hexdigest()
            rel = f"{doc_id}/{page_id}.md"
            prev = known_pages.get(page_id)
            if prev and prev.get("content_hash") == digest and not prev.get("deleted"):
                stats.pages_unchanged += 1
            else:
                target = corpus_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                stats.pages_written += 1
            known_pages[page_id] = {
                "name": page.get("name") or "Untitled",
                "date_updated": page.get("date_updated"),
                "content_hash": digest,
                "path": rel,
                "url": page_url(workspace_id, doc_id, page_id),
                "deleted": False,
            }
        for page_id, meta in known_pages.items():
            if page_id not in seen_pages and not meta.get("deleted"):
                meta["deleted"] = True
                stats.pages_deleted += 1
        entry.update(
            {
                "name": doc.get("name") or "Untitled",
                "date_updated": doc_updated,
                "deleted": False,
            }
        )
        known_docs[doc_id] = entry
        # Persist after every doc so an interrupted scrape keeps its progress.
        manifest["last_sync"] = time.time()
        write_json_atomic(manifest_path, manifest)

    for doc_id, entry in known_docs.items():
        if doc_id not in found and not entry.get("deleted"):
            entry["deleted"] = True
            for meta in entry.get("pages", {}).values():
                meta["deleted"] = True
            stats.docs_deleted += 1
            log(f"Doc {entry.get('name', doc_id)} no longer exists; retired")

    manifest["last_sync"] = time.time()
    write_json_atomic(manifest_path, manifest)
    log(
        f"Done: {stats.pages_written} pages written, {stats.pages_unchanged} unchanged, "
        f"{stats.docs_skipped} docs unchanged, {stats.docs_deleted} docs retired"
    )
    return stats
