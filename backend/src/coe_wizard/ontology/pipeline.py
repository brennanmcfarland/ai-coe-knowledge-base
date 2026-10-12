"""Map-reduce pipeline that proposes the wizard DAG from corpus chunks.

map:         per chunk, extract candidate development steps (title, description, checklist)
place:       add candidates one at a time to a running step doc (mirrored to a JSONL file): the
             model picks the existing step it matches (then rewrites that one step to fold it in)
             or "new"; existing node ids are offered so rebuilds keep ids stable
consolidate: repeated passes over the doc, asking per step whether it duplicates another, until
             a pass merges nothing
edges:       per node, ask for its direct prerequisites; cycles are broken by dropping the
             lowest-confidence edge
summarize:   per node, write a summary over its source chunks, citing them as [S#] markers that
             are rewritten to [n] and turned into citations

Every call asks for one small object, with ids constrained to an enum of the valid ones, so no
reply has to restate the whole ontology (long replies get cut off at max_tokens).
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..chunks import Chunk
from ..facets import union
from ..llm import LLMBackend
from .model import (
    ChecklistItem,
    Citation,
    Edge,
    Facets,
    Node,
    Ontology,
    checklist_item_id,
    slugify,
    would_create_cycle,
)

CONSOLIDATE_PASSES = 3
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

def map_schema(facets: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """MAP_SCHEMA plus, when there are facets, per-chunk facet tags constrained to known values
    and free-text proposals for values the facet lists lack."""
    if not facets:
        return MAP_SCHEMA
    tags = _object({f: {"type": "array", "items": _enum(v)} for f, v in facets.items() if v})
    proposal = _object({"facet": _enum(list(facets)), "value": {"type": "string"}})
    return _object(
        {
            "steps": MAP_SCHEMA["properties"]["steps"],
            "facets": tags,
            "new_values": {"type": "array", "items": proposal},
        }
    )


MERGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "checklist": _STR_LIST,
    },
    "required": ["title", "description", "checklist"],
    "additionalProperties": False,
}

# Sentinels for "no match" in the enum-constrained choices; never handed out as step ids.
NEW = "new"
NONE = "none"


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _enum(values: Sequence[str]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def place_schema(step_ids: Sequence[str], reusable_ids: Sequence[str]) -> dict[str, Any]:
    props = {"match": _enum([NEW, *step_ids])}
    if reusable_ids:
        props["reuse_id"] = _enum([NONE, *reusable_ids])
    return _object(props)


def duplicate_schema(step_ids: Sequence[str]) -> dict[str, Any]:
    return _object({"duplicate_of": _enum([NONE, *step_ids])})


def prereq_schema(step_ids: Sequence[str]) -> dict[str, Any]:
    item = _object({"id": _enum(step_ids), "confidence": {"type": "number"}})
    return _object({"prerequisites": {"type": "array", "items": item}})


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

MAP_FACETS = """

Also tag the excerpt with the facet values below that it is specifically about, so the guide can \
be tailored to each project. Leave a facet empty when the excerpt is general or doesn't say. If \
the excerpt is specifically about something a facet's list lacks (e.g. a disease area), add it \
to `new_values` with a short human-readable label.
{facets}"""

QUESTIONS_SCHEMA_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "kind": {"type": "string", "enum": ["single", "multi"]},
        "options": _STR_LIST,
    },
    "required": ["text", "kind", "options"],
    "additionalProperties": False,
}

QUESTIONS_PROMPT = """A guide that walks teams through building applications is tailored to each \
project by an interview. These are its current questions:
{questions}

These are the knowledge-base documents the guide is built from:
{docs}

Propose up to 3 new multiple-choice questions whose answers would change which knowledge-base \
material applies to a project (for example a domain or data type the documents cover but no \
question asks about). Put each in one of these sections: {sections}. Don't repeat or rephrase \
existing questions. Return an empty list if none are needed."""

PLACE_PROMPT = """You are maintaining the list of distinct steps in a guide that walks a team \
through building an application. Decide where the candidate step below belongs.

Set `match` to the id of the step that describes the same step as the candidate, or "new" if \
none does. Prefer an existing step when the candidate is a narrower part of it.{reuse}

Steps so far:
{steps}

Candidate:
{candidate}"""

PLACE_REUSE = """

If `match` is "new" and the candidate is the same step as one of these nodes from a previous \
build, set `reuse_id` to its id; otherwise set it to "none":
{existing}"""

DUPLICATE_PROMPT = """These are the steps of a guide for building an application:
{steps}

Does the step below describe the same step as one of them? Set `duplicate_of` to that step's id, \
or "none" if it is distinct.

Step:
{step}"""

MERGE_PROMPT = """Fold new material into one step of a guide for building applications. Keep the \
step's scope. Keep the title a short imperative and the description one or two sentences; merge \
the checklists, removing duplicates and keeping items concrete.

Step:
{step}

New material:
{other}"""

PREREQ_PROMPT = """These are the steps of a guide for building an application:
{steps}

List the direct prerequisites of the step "{id}" ({title}): steps that should be done \
immediately before it. Omit transitive prerequisites and steps that are merely related, and give \
each a confidence from 0 to 1. Return an empty list if it has none."""

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
    node_id: str | None = None  # assigned once the candidate is placed in the step doc


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


@dataclass
class MapResult:
    candidates: list[Candidate] = field(default_factory=list)
    chunk_facets: dict[str, Facets] = field(default_factory=dict)
    new_values: dict[str, list[str]] = field(default_factory=dict)


def _facet_listing(facets: Mapping[str, Sequence[str]]) -> str:
    return "\n".join(f"- {f}: {', '.join(v)}" for f, v in facets.items() if v)


async def map_chunks(
    llm: LLMBackend,
    chunks: Sequence[Chunk],
    progress: Progress,
    facets: Mapping[str, Sequence[str]] | None = None,
) -> MapResult:
    facets = {f: list(v) for f, v in (facets or {}).items() if v}
    schema = map_schema(facets)
    suffix = MAP_FACETS.format(facets=_facet_listing(facets)) if facets else ""
    res = MapResult()
    out = res.candidates
    for i, chunk in enumerate(chunks, 1):
        progress(f"map {i}/{len(chunks)}")
        prompt = MAP_PROMPT.format(doc=chunk.doc_title, page=chunk.page_title, text=chunk.text)
        result = await llm.complete(prompt + suffix, schema)
        if facets:
            tags = result.get("facets") or {}
            res.chunk_facets[chunk.id] = {
                f: _dedupe([v for v in tags.get(f, []) if v in allowed])
                for f, allowed in facets.items()
                if any(v in allowed for v in tags.get(f, []))
            }
            for nv in result.get("new_values") or []:
                if nv.get("facet") in facets and str(nv.get("value", "")).strip():
                    have = res.new_values.setdefault(nv["facet"], [])
                    have[:] = _dedupe([*have, nv["value"]])
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
    return res


class StepDoc:
    """The running list of steps the reduce passes edit one item at a time.

    Rewritten to `path` (JSONL, one step per line) after every change, so a long build can be
    watched, and what it had reached is still there if it dies.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.steps: list[Candidate] = []
        self._path = path
        self._taken = {NEW, NONE}

    def ids(self, exclude: Candidate | None = None) -> list[str]:
        return [s.node_id for s in self.steps if s is not exclude and s.node_id]

    def get(self, node_id: str | None) -> Candidate | None:
        return next((s for s in self.steps if s.node_id == node_id), None)

    def claim(self, base: str) -> str:
        unique, i = base, 2
        while unique in self._taken:
            unique, i = f"{base}-{i}", i + 1
        self._taken.add(unique)
        return unique

    def add(self, step: Candidate, base_id: str) -> None:
        step.node_id = self.claim(base_id)
        self.steps.append(step)
        self.save()

    def remove(self, step: Candidate) -> None:
        self.steps = [s for s in self.steps if s is not step]
        self.save()

    def listing(self, exclude: Candidate | None = None) -> str:
        return "\n".join(_brief(s) for s in self.steps if s is not exclude) or "(none)"

    def save(self) -> None:
        if self._path is None:
            return
        tmp = self._path.with_suffix(".tmp")
        lines = (json.dumps({**_full(s), "chunk_ids": s.chunk_ids}) + "\n" for s in self.steps)
        tmp.write_text("".join(lines))
        tmp.replace(self._path)


def _brief(step: Candidate) -> str:
    return json.dumps({"id": step.node_id, "title": step.title, "description": step.description})


def _full(step: Candidate) -> dict[str, Any]:
    out = {"title": step.title, "description": step.description, "checklist": step.checklist}
    return {"id": step.node_id, **out} if step.node_id else out


async def _merge_into(llm: LLMBackend, step: Candidate, other: Candidate) -> None:
    """Rewrite `step` to fold in `other`. Only this one step is in the model's output."""
    result = await llm.complete(
        MERGE_PROMPT.format(step=json.dumps(_full(step)), other=json.dumps(_full(other))),
        MERGE_SCHEMA,
    )
    step.title = result.get("title", "").strip() or step.title
    step.description = result.get("description", "").strip() or step.description
    step.checklist = _dedupe(result.get("checklist") or [*step.checklist, *other.checklist])
    step.chunk_ids = _dedupe([*step.chunk_ids, *other.chunk_ids])


async def place_candidates(
    llm: LLMBackend,
    doc: StepDoc,
    candidates: Sequence[Candidate],
    existing_nodes: Sequence[Node],
    progress: Progress,
) -> None:
    existing_titles = {n.id: n.title for n in existing_nodes}
    for i, cand in enumerate(candidates, 1):
        reusable = [nid for nid in existing_titles if nid not in doc.ids()]
        if not doc.steps and not reusable:
            doc.add(cand, slugify(cand.title))
            progress(f"place {i}/{len(candidates)}: {cand.title} -> new {cand.node_id}")
            continue
        reuse = ""
        if reusable:
            existing = "\n".join(f"- {nid}: {existing_titles[nid]}" for nid in reusable)
            reuse = PLACE_REUSE.format(existing=existing)
        result = await llm.complete(
            PLACE_PROMPT.format(
                steps=doc.listing(), candidate=json.dumps(_full(cand)), reuse=reuse
            ),
            place_schema(doc.ids(), reusable),
        )
        target = doc.get(result.get("match"))
        if target is not None:
            await _merge_into(llm, target, cand)
            doc.save()
            progress(f"place {i}/{len(candidates)}: {cand.title} -> merged into {target.node_id}")
            continue
        reuse_id = result.get("reuse_id")
        doc.add(cand, reuse_id if reuse_id in reusable else slugify(cand.title))
        progress(f"place {i}/{len(candidates)}: {cand.title} -> new {cand.node_id}")


async def consolidate(llm: LLMBackend, doc: StepDoc, progress: Progress) -> None:
    """Merge steps that placement left as near-duplicates, until a pass finds none."""
    for pass_no in range(1, CONSOLIDATE_PASSES + 1):
        merges = 0
        i = 0
        while i < len(doc.steps) and len(doc.steps) > 1:
            step = doc.steps[i]
            progress(f"consolidate pass {pass_no}: {i + 1}/{len(doc.steps)}")
            result = await llm.complete(
                DUPLICATE_PROMPT.format(steps=doc.listing(exclude=step), step=_brief(step)),
                duplicate_schema(doc.ids(exclude=step)),
            )
            target = doc.get(result.get("duplicate_of"))
            if target is None or target is step:
                i += 1
                continue
            await _merge_into(llm, target, step)
            doc.remove(step)  # the next step slides into index i
            merges += 1
            progress(f"  merged {step.node_id} into {target.node_id}")
        if not merges:
            break


async def reduce_candidates(
    llm: LLMBackend,
    candidates: Sequence[Candidate],
    existing_nodes: Sequence[Node],
    progress: Progress,
    doc_path: Path | None = None,
) -> list[BuiltNode]:
    doc = StepDoc(doc_path)
    await place_candidates(llm, doc, candidates, existing_nodes, progress)
    await consolidate(llm, doc, progress)
    return [
        BuiltNode(s.node_id or slugify(s.title), s.title, s.description, s.checklist, s.chunk_ids)
        for s in doc.steps
    ]


async def infer_edges(
    llm: LLMBackend, nodes: Sequence[BuiltNode], progress: Progress
) -> list[tuple[str, str]]:
    listing = "\n".join(
        json.dumps({"id": n.id, "title": n.title, "description": n.description}) for n in nodes
    )
    proposed: list[tuple[float, str, str]] = []
    for i, node in enumerate(nodes, 1):
        others = [n.id for n in nodes if n.id != node.id]
        if not others:
            continue
        progress(f"prerequisites {i}/{len(nodes)}: {node.title}")
        result = await llm.complete(
            PREREQ_PROMPT.format(steps=listing, id=node.id, title=node.title),
            prereq_schema(others),
        )
        for p in result.get("prerequisites", []):
            if p.get("id") in others:
                proposed.append((float(p.get("confidence", 0)), p["id"], node.id))
    proposed.sort(key=lambda e: -e[0])
    kept: list[tuple[str, str]] = []
    for _, source, target in proposed:
        key = (source, target)
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


@dataclass
class BuildResult:
    ontology: Ontology
    # Facet tags per chunk id (only for chunks mapped in this build).
    chunk_facets: dict[str, Facets] = field(default_factory=dict)
    # Proposed new facet values (labels) per facet id.
    new_values: dict[str, list[str]] = field(default_factory=dict)


async def propose_questions(
    llm: LLMBackend,
    questions: Sequence[str],
    sections: Sequence[str],
    doc_titles: Sequence[str],
) -> list[dict[str, Any]]:
    """Ask once per build for new facet questions the corpus suggests (see interview.yaml)."""
    if not sections:
        return []
    schema = _object(
        {
            "questions": {
                "type": "array",
                "items": {
                    **QUESTIONS_SCHEMA_ITEM,
                    "properties": {
                        **QUESTIONS_SCHEMA_ITEM["properties"],
                        "section": _enum(sections),
                    },
                    "required": [*QUESTIONS_SCHEMA_ITEM["required"], "section"],
                },
            }
        }
    )
    result = await llm.complete(
        QUESTIONS_PROMPT.format(
            questions="\n".join(f"- {q}" for q in questions) or "(none)",
            docs="\n".join(f"- {d}" for d in doc_titles) or "(none)",
            sections=", ".join(sections),
        ),
        schema,
    )
    return list(result.get("questions") or [])[:3]


async def build_ontology(
    llm: LLMBackend,
    chunks: dict[str, Chunk],
    existing: Ontology,
    progress: Progress = lambda _m: None,
    doc_path: Path | None = None,
    facets: Mapping[str, Sequence[str]] | None = None,
) -> BuildResult:
    ordered = list(chunks.values())
    mapped = await map_chunks(llm, ordered, progress, facets)
    candidates = mapped.candidates
    progress(f"{len(candidates)} candidate steps")
    if not candidates:
        return BuildResult(Ontology(), mapped.chunk_facets, mapped.new_values)

    def chunk_tags(cid: str) -> Facets:
        if cid in mapped.chunk_facets:
            return mapped.chunk_facets[cid]
        return chunks[cid].facets if cid in chunks else {}

    built = await reduce_candidates(llm, candidates, existing.nodes, progress, doc_path)
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
                facets=union([chunk_tags(c) for c in b.chunk_ids]),
            )
        )
    onto = Ontology(nodes=nodes, edges=[Edge.of(s, t) for s, t in edges])
    return BuildResult(onto, mapped.chunk_facets, mapped.new_values)
