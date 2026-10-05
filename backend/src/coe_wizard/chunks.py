"""Splits the scraped corpus into citeable chunks with stable IDs."""

import hashlib
import json
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .scraper import load_manifest

TARGET_CHARS = 1500
HEADING_RE = re.compile(r"^#{1,6}\s")


@dataclass(frozen=True)
class Chunk:
    id: str
    doc_id: str
    page_id: str
    doc_title: str
    page_title: str
    url: str
    text: str


def normalize(text: str) -> str:
    return " ".join(text.split()).lower()


def chunk_id(doc_id: str, page_id: str, text: str) -> str:
    """Stable across rebuilds as long as the chunk's text doesn't change."""
    return hashlib.sha256(f"{doc_id}/{page_id}\n{normalize(text)}".encode()).hexdigest()[:16]


def split_markdown(md: str, target: int = TARGET_CHARS) -> list[str]:
    """Split on headings, then pack paragraphs up to roughly `target` characters."""
    sections: list[list[str]] = [[]]
    for line in md.splitlines():
        if HEADING_RE.match(line) and any(s.strip() for s in sections[-1]):
            sections.append([])
        sections[-1].append(line)

    out: list[str] = []
    for section in sections:
        paragraphs = [p.strip() for p in "\n".join(section).split("\n\n") if p.strip()]
        buf = ""
        for para in paragraphs:
            if buf and len(buf) + len(para) + 2 > target:
                out.append(buf)
                buf = ""
            buf = f"{buf}\n\n{para}" if buf else para
        if buf:
            out.append(buf)
    return out


def chunk_corpus(corpus_dir: Path) -> Iterator[Chunk]:
    manifest = load_manifest(corpus_dir / "manifest.json")
    for doc_id, doc in sorted(manifest.get("docs", {}).items()):
        if doc.get("deleted"):
            continue
        for page_id, page in sorted(doc.get("pages", {}).items()):
            if page.get("deleted"):
                continue
            path = corpus_dir / page["path"]
            if not path.exists():
                continue
            for text in split_markdown(path.read_text()):
                yield Chunk(
                    id=chunk_id(doc_id, page_id, text),
                    doc_id=doc_id,
                    page_id=page_id,
                    doc_title=doc.get("name", ""),
                    page_title=page.get("name", ""),
                    url=page["url"],
                    text=text,
                )


def write_chunks(path: Path, chunks: Iterable[Chunk]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    seen: set[str] = set()
    with tmp.open("w") as f:
        for c in chunks:
            if c.id in seen:  # identical text repeated on a page
                continue
            seen.add(c.id)
            f.write(json.dumps(asdict(c)) + "\n")
    os.replace(tmp, path)
    return len(seen)


def read_chunks(path: Path) -> dict[str, Chunk]:
    if not path.exists():
        return {}
    out: dict[str, Chunk] = {}
    with path.open() as f:
        for line in f:
            if line.strip():
                data: dict[str, Any] = json.loads(line)
                out[data["id"]] = Chunk(**data)
    return out
