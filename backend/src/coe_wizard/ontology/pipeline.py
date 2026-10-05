"""Map-reduce pipeline that proposes the wizard DAG from corpus chunks.

map:       per chunk, extract candidate development steps (title, description, checklist)
reduce:    merge candidates into nodes in batches, repeating until one batch covers everything;
           existing node ids are offered to the model so rebuilds keep ids stable
edges:     infer prerequisite edges between nodes; cycles are broken by dropping the
           lowest-confidence edge
summarize: per node, write a summary over its source chunks, citing them as [S#] markers that
           are rewritten to [n] and turned into citations
"""

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..chunks import Chunk
from ..llm import LLMBackend
from .model import (
    ChecklistItem,
    Citation,
    Edge,
    Node,
    Ontology,
    checklist_item_id,
    slugify,
    would_create_cycle,
)

REDUCE_BATCH = 40
SUMMARY_CHAR_BUDGET = 12000
EXCERPT_CHARS = 400

_STR_LIST = {"type": "array", "items": {"type": "string"}}

MAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "checklist": _STR_LIST,
                },
                "required": ["title", "description", "checklist"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["steps"],
    "additionalProperties": False,
}

REDUCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "checklist": _STR_LIST,
                    "members": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["id", "title", "description", "checklist", "members"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["nodes"],
    "additionalProperties": False,
}

EDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["from", "to", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["edges"],
    "additionalProperties": False,
}

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

MAP_PROMPT = """You are building a language- and platform-agnostic guide that walks a team through \
building an application (e.g. procuring data, system design, security review, deployment).

From the excerpt below, extract the distinct development steps or phases it describes or implies. \
For each give a short imperative title, a one-to-two sentence description, and concrete checklist \
items a team should verify for that step. Return an empty list if the excerpt describes no \
actionable step.

Excerpt (from "{doc}" / "{page}"):
---
{text}
---"""

REDUCE_PROMPT = """Merge these candidate development steps into a de-duplicated set of wizard \
nodes. Each node is one distinct step in building an application. Combine candidates describing \
the same step and merge their checklists (remove duplicates, keep items concrete). `members` lists \
the candidate numbers merged into the node; every candidate should belong to exactly one node.

If a node matches one of these existing nodes, reuse its id exactly:
{existing}

Otherwise make `id` a short lowercase-hyphenated slug of the title.

Candidates:
{candidates}"""

EDGE_PROMPT = """These are the steps of a guide for building an application. Identify direct \
prerequisite relationships: an edge from A to B means A should be done before B. Only include \
direct, meaningful prerequisites (no transitive shortcuts), and give each a confidence from 0 to \
1. Use the ids exactly as given.

Steps:
{nodes}"""

SUMMARY_PROMPT = """Write a concise, prescriptive Markdown summary (150-400 words) of the step \
"{title}" for a guide on building applications. It should be language- and platform-agnostic and \
educational. Base it only on the sources below, and cite them inline with markers like [S1] or \
[S2][S3] right after the statements they support. Don't include a heading or a checklist.

Step description: {description}

Sources:
{sources}"""


@dataclass
class Candidate:
    title: str
    description: str
    checklist: list[str]
    chunk_ids: list[str] = field(default_factory=list)
    node_id: str | None = None  # assigned once the candidate has been through a reduce pass


@dataclass
class BuiltNode:
    id: str
    title: str
    description: str
    checklist: list[str]
    chunk_ids: list[str]


Progress = Callable[[str], None]


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = " ".join(item.split()).lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


async def map_chunks(
    llm: LLMBackend, chunks: Sequence[Chunk], progress: Progress
) -> list[Candidate]:
    out: list[Candidate] = []
    for i, chunk in enumerate(chunks, 1):
        progress(f"map {i}/{len(chunks)}")
        prompt = MAP_PROMPT.format(doc=chunk.doc_title, page=chunk.page_title, text=chunk.text)
        result = await llm.complete(prompt, MAP_SCHEMA)
        for step in result.get("steps", []):
            if step.get("title", "").strip():
                out.append(
                    Candidate(
                        title=step["title"].strip(),
                        description=step.get("description", "").strip(),
                        checklist=_dedupe(step.get("checklist", [])),
                        chunk_ids=[chunk.id],
                    )
                )
    return out


async def _reduce_batch(
    llm: LLMBackend, batch: Sequence[Candidate], existing: str
) -> list[Candidate]:
    listing = "\n".join(
        json.dumps(
            {"n": i, "title": c.title, "description": c.description, "checklist": c.checklist}
        )
        for i, c in enumerate(batch)
    )
    result = await llm.complete(
        REDUCE_PROMPT.format(existing=existing or "(none)", candidates=listing), REDUCE_SCHEMA
    )
    merged: list[Candidate] = []
    for node in result.get("nodes", []):
        members = [m for m in node.get("members", []) if 0 <= m < len(batch)]
        chunk_ids = _dedupe([cid for m in members for cid in batch[m].chunk_ids])
        title = node.get("title", "").strip()
        if not title:
            continue
        merged.append(
            Candidate(
                title=title,
                description=node.get("description", "").strip(),
                checklist=_dedupe(node.get("checklist", [])),
                chunk_ids=chunk_ids,
                node_id=slugify(node.get("id") or title),
            )
        )
    return merged


async def reduce_candidates(
    llm: LLMBackend,
    candidates: list[Candidate],
    existing_nodes: Sequence[Node],
    progress: Progress,
) -> list[BuiltNode]:
    existing = "\n".join(f"- {n.id}: {n.title}" for n in existing_nodes)
    current = candidates
    for round_no in range(1, 20):
        batches = [current[i : i + REDUCE_BATCH] for i in range(0, len(current), REDUCE_BATCH)]
        merged: list[Candidate] = []
        for b, batch in enumerate(batches, 1):
            progress(f"reduce round {round_no}: batch {b}/{len(batches)}")
            merged.extend(await _reduce_batch(llm, batch, existing))
        done = len(batches) <= 1 or len(merged) >= len(current)
        current = merged
        if done:
            break

    nodes: list[BuiltNode] = []
    taken: set[str] = set()
    for c in current:
        title = c.title
        base = c.node_id or slugify(title)
        unique, i = base, 2
        while unique in taken:
            unique, i = f"{base}-{i}", i + 1
        taken.add(unique)
        nodes.append(BuiltNode(unique, title, c.description, c.checklist, c.chunk_ids))
    return nodes


async def infer_edges(
    llm: LLMBackend, nodes: Sequence[BuiltNode], progress: Progress
) -> list[tuple[str, str]]:
    progress("inferring edges")
    listing = "\n".join(
        json.dumps({"id": n.id, "title": n.title, "description": n.description}) for n in nodes
    )
    result = await llm.complete(EDGE_PROMPT.format(nodes=listing), EDGE_SCHEMA)
    ids = {n.id for n in nodes}
    proposed = sorted(
        (e for e in result.get("edges", []) if e.get("from") in ids and e.get("to") in ids),
        key=lambda e: -float(e.get("confidence", 0)),
    )
    kept: list[tuple[str, str]] = []
    for e in proposed:
        key = (e["from"], e["to"])
        if key in kept or would_create_cycle(kept, key):
            continue
        kept.append(key)
    return kept


_MARKER = re.compile(r"\[S(\d+)\]")


def render_citations(summary: str, sources: Sequence[Chunk]) -> tuple[str, list[Citation]]:
    """Rewrite [S#] markers to [n] in first-use order and build the matching citation list."""
    order: list[int] = []

    def repl(match: re.Match[str]) -> str:
        idx = int(match.group(1)) - 1
        if not 0 <= idx < len(sources):
            return ""
        if idx not in order:
            order.append(idx)
        return f"[{order.index(idx) + 1}]"

    text = _MARKER.sub(repl, summary)
    citations = [citation_for(chunk=sources[idx], n=i) for i, idx in enumerate(order, 1)]
    return text, citations


def citation_for(chunk: Chunk, n: int) -> Citation:
    excerpt = (
        chunk.text
        if len(chunk.text) <= EXCERPT_CHARS
        else (chunk.text[:EXCERPT_CHARS].rsplit(" ", 1)[0] + " …")
    )
    return Citation(
        n=n,
        chunk_id=chunk.id,
        excerpt=excerpt,
        url=chunk.url,
        doc_title=f"{chunk.doc_title} / {chunk.page_title}".strip(" /"),
    )


async def summarize(
    llm: LLMBackend, node: BuiltNode, chunks: dict[str, Chunk]
) -> tuple[str, list[Citation]]:
    sources: list[Chunk] = []
    used = 0
    for cid in node.chunk_ids:
        chunk = chunks.get(cid)
        if chunk is None or used + len(chunk.text) > SUMMARY_CHAR_BUDGET:
            continue
        sources.append(chunk)
        used += len(chunk.text)
    if not sources:
        return node.description, []
    listing = "\n\n".join(f"[S{i}] ({c.doc_title}):\n{c.text}" for i, c in enumerate(sources, 1))
    result = await llm.complete(
        SUMMARY_PROMPT.format(title=node.title, description=node.description, sources=listing),
        SUMMARY_SCHEMA,
    )
    return render_citations(result.get("summary", "").strip(), sources)


async def build_ontology(
    llm: LLMBackend,
    chunks: dict[str, Chunk],
    existing: Ontology,
    progress: Progress = lambda _m: None,
) -> Ontology:
    ordered = list(chunks.values())
    candidates = await map_chunks(llm, ordered, progress)
    progress(f"{len(candidates)} candidate steps")
    if not candidates:
        return Ontology()
    built = await reduce_candidates(llm, candidates, existing.nodes, progress)
    progress(f"{len(built)} nodes")
    edges = await infer_edges(llm, built, progress)

    nodes: list[Node] = []
    for i, b in enumerate(built, 1):
        progress(f"summarize {i}/{len(built)}: {b.title}")
        summary, citations = await summarize(llm, b, chunks)
        nodes.append(
            Node(
                id=b.id,
                title=b.title,
                summary=summary,
                checklist=[ChecklistItem(id=checklist_item_id(t), text=t) for t in b.checklist],
                citations=citations,
            )
        )
    return Ontology(nodes=nodes, edges=[Edge.of(s, t) for s, t in edges])
