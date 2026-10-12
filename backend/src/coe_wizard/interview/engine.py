"""LLM steps of the interview: adaptive follow-up questions, and tailoring the step graph.

Tailoring a project:
  select    facet-filter the accepted global steps (universal or matching the answers), then ask
            the LLM, in batches, which of those this project needs
  retrieve  KB chunks whose facet tags match the answers (e.g. oncology terminology passages)
  generate  project-specific steps the selected ones don't cover, citing the retrieved passages;
            a step no passage supports is kept but flagged ungrounded ("Not from KB")
  summarize as in the build pipeline (cited), or from general knowledge for ungrounded steps
  edges     prerequisites of each generated step among the project's combined steps
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..chunks import Chunk
from ..facets import is_universal, matches
from ..llm import LLMBackend
from ..ontology.model import (
    ChecklistItem,
    Edge,
    Node,
    Ontology,
    checklist_item_id,
    would_create_cycle,
)
from ..ontology.pipeline import (
    PREREQ_PROMPT,
    SUMMARY_CHAR_BUDGET,
    BuiltNode,
    prereq_schema,
    summarize,
)
from .definition import InterviewDef, Section
from .overlay import (
    Answer,
    FollowUp,
    Overlay,
    Proposal,
    compose,
    facet_answers,
    generated_id,
    visible_global,
)

SELECT_BATCH = 12
MAX_GENERATED = 5
SOURCE_CHARS = 1500

Progress = Callable[[str], None]

FOLLOWUP_PROMPT = """You are interviewing a team about the application they want to build, so \
that a step-by-step guide built from a knowledge base can be tailored to them.

Current section: {section}

Answers so far:
{profile}

The knowledge base is tagged with these topics:
{facets}

Knowledge-base documents:
{docs}

Ask up to {n} short follow-up questions for the "{section}" section whose answers would change \
which steps or knowledge-base resources apply to this project. Only ask about things the \
knowledge base covers, and don't repeat anything already answered. Use kind "single" or "multi" \
with 2-6 options when the possible answers are known, otherwise kind "text" with no options. \
Return an empty list if nothing more is needed."""

SELECT_PROMPT = """A step-by-step guide for building applications is being tailored to one \
project.

Project profile:
{profile}

For each candidate step below, decide whether this project needs it. Keep steps that apply to \
most projects (e.g. a security review) unless the profile clearly rules them out.

Candidate steps:
{steps}"""

GENERATE_PROMPT = """A step-by-step guide for building applications is being tailored to one \
project.

Project profile:
{profile}

The guide already includes these steps:
{steps}

Knowledge-base passages relevant to this project:
{sources}

Propose up to {max} additional steps specific to this project that the existing steps don't \
cover, for example acquiring the domain-specific terminologies, ontologies, standards or \
datasets named in the passages. Give each a short imperative title, a one-to-two sentence \
description and concrete checklist items. Cite the passages each step is based on in `sources`; \
leave `sources` empty only for a step no passage supports (it will be flagged as not from the \
knowledge base). If a step is really covered by an existing step, set `covered_by` to that \
step's id, otherwise "none". Return an empty list if nothing is missing."""

GENERAL_SUMMARY_PROMPT = """Write a concise, prescriptive Markdown summary (100-250 words) of the \
step "{title}" for a guide on building applications, for this project:
{profile}

Step description: {description}

No knowledge-base source covers this step, so write from general knowledge, stay accurate and \
don't cite anything. Don't include a heading or a checklist."""

_STR_LIST = {"type": "array", "items": {"type": "string"}}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def followup_schema(n: int) -> dict[str, Any]:
    item = _object(
        {
            "text": {"type": "string"},
            "kind": {"type": "string", "enum": ["text", "single", "multi"]},
            "options": _STR_LIST,
        }
    )
    return _object({"followups": {"type": "array", "items": item, "maxItems": n}})


def select_schema(ids: Sequence[str]) -> dict[str, Any]:
    item = _object({"id": {"type": "string", "enum": list(ids)}, "include": {"type": "boolean"}})
    return _object({"decisions": {"type": "array", "items": item}})


def generate_schema(source_labels: Sequence[str], step_ids: Sequence[str]) -> dict[str, Any]:
    sources: dict[str, Any] = (
        {"type": "array", "items": {"type": "string", "enum": list(source_labels)}}
        if source_labels
        else {"type": "array", "maxItems": 0, "items": {"type": "string"}}
    )
    item = _object(
        {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "checklist": _STR_LIST,
            "sources": sources,
            "covered_by": {"type": "string", "enum": ["none", *step_ids]},
        }
    )
    return _object({"steps": {"type": "array", "items": item, "maxItems": MAX_GENERATED}})


GENERAL_SUMMARY_SCHEMA = _object({"summary": {"type": "string"}})


def _labels(defn: InterviewDef, qid: str, value: Answer) -> str:
    q = defn.question(qid)
    values = [value] if isinstance(value, str) else list(value)
    if q is None or q.kind == "text":
        return ", ".join(values)
    return ", ".join((o.label if (o := q.option(v)) else v) for v in values)


def profile_text(
    defn: InterviewDef, answers: Mapping[str, Answer], followups: Sequence[FollowUp]
) -> str:
    lines: list[str] = []
    for s in defn.sections:
        for q in defn.accepted_questions(s.id):
            value = answers.get(q.id)
            if value:
                lines.append(f"- {q.text} {_labels(defn, q.id, value)}")
        for f in followups:
            if f.section == s.id and f.answer:
                answer = f.answer if isinstance(f.answer, str) else ", ".join(f.answer)
                lines.append(f"- {f.text} {answer}")
    return "\n".join(lines) or "(no answers)"


def kb_overview(defn: InterviewDef, chunks: Mapping[str, Chunk]) -> tuple[str, str]:
    facets = "\n".join(
        f"- {q.text} {', '.join(o.label for o in q.accepted_options())}"
        for q in defn.accepted_questions()
        if q.facet
    )
    docs = sorted({c.doc_title for c in chunks.values() if c.doc_title})
    return facets or "(none)", "\n".join(f"- {d}" for d in docs) or "(none)"


async def generate_followups(
    llm: LLMBackend,
    defn: InterviewDef,
    section: Section,
    answers: Mapping[str, Answer],
    followups: Sequence[FollowUp],
    chunks: Mapping[str, Chunk],
) -> list[FollowUp]:
    if section.max_followups <= 0:
        return []
    facets, docs = kb_overview(defn, chunks)
    result = await llm.complete(
        FOLLOWUP_PROMPT.format(
            section=section.title,
            profile=profile_text(defn, answers, followups),
            facets=facets,
            docs=docs,
            n=section.max_followups,
        ),
        followup_schema(section.max_followups),
    )
    out: list[FollowUp] = []
    for i, f in enumerate((result.get("followups") or [])[: section.max_followups], 1):
        text = str(f.get("text", "")).strip()
        options = list(dict.fromkeys(o.strip() for o in f.get("options") or [] if o.strip()))
        kind = f.get("kind") if f.get("kind") in ("single", "multi") and options else "text"
        if text:
            out.append(
                FollowUp(
                    id=f"fu.{section.id}.{i}",
                    section=section.id,
                    text=text,
                    kind=kind,
                    options=options if kind != "text" else [],
                )
            )
    return out


def retrieve(chunks: Mapping[str, Chunk], answers: Mapping[str, Sequence[str]]) -> list[Chunk]:
    """Facet-tagged chunks matching the answers, best-matching first, within the char budget."""

    def score(c: Chunk) -> int:
        return sum(len(set(v) & set(answers.get(f, []))) for f, v in c.facets.items())

    hits = [c for c in chunks.values() if not is_universal(c.facets) and matches(c.facets, answers)]
    hits.sort(key=score, reverse=True)
    out: list[Chunk] = []
    used = 0
    for c in hits:
        size = min(len(c.text), SOURCE_CHARS)
        if used + size > SUMMARY_CHAR_BUDGET:
            break
        out.append(c)
        used += size
    return out


def _listing(nodes: Sequence[Node]) -> str:
    return "\n".join(json.dumps({"id": n.id, "title": n.title}) for n in nodes) or "(none)"


async def select_steps(
    llm: LLMBackend, candidates: Sequence[Node], profile: str, progress: Progress
) -> set[str]:
    include = {n.id for n in candidates}  # undecided candidates are kept
    for i in range(0, len(candidates), SELECT_BATCH):
        batch = candidates[i : i + SELECT_BATCH]
        progress(f"Choosing steps {i + 1}-{i + len(batch)} of {len(candidates)}")
        result = await llm.complete(
            SELECT_PROMPT.format(profile=profile, steps=_listing(batch)),
            select_schema([n.id for n in batch]),
        )
        for d in result.get("decisions") or []:
            if d.get("id") in include and d.get("include") is False:
                include.discard(d["id"])
    return include


@dataclass
class Generated:
    node: Node
    description: str
    chunk_ids: list[str]


async def generate_steps(
    llm: LLMBackend,
    profile: str,
    selected: Sequence[Node],
    sources: Sequence[Chunk],
    taken: set[str],
    progress: Progress,
) -> list[Generated]:
    progress(f"Looking for project-specific steps in {len(sources)} relevant passages")
    labels = [f"S{i}" for i in range(1, len(sources) + 1)]
    listing = "\n\n".join(
        f"[{label}] ({c.doc_title}):\n{c.text[:SOURCE_CHARS]}"
        for label, c in zip(labels, sources, strict=True)
    )
    result = await llm.complete(
        GENERATE_PROMPT.format(
            profile=profile,
            steps=_listing(selected),
            sources=listing or "(none)",
            max=MAX_GENERATED,
        ),
        generate_schema(labels, [n.id for n in selected]),
    )
    out: list[Generated] = []
    for step in (result.get("steps") or [])[:MAX_GENERATED]:
        title = str(step.get("title", "")).strip()
        if not title or step.get("covered_by", "none") != "none":
            continue
        chunk_ids = [sources[labels.index(s)].id for s in step.get("sources") or [] if s in labels]
        node_id = generated_id(title, taken)
        taken.add(node_id)
        checklist = list(dict.fromkeys(t.strip() for t in step.get("checklist") or [] if t.strip()))
        node = Node(
            id=node_id,
            title=title,
            checklist=[ChecklistItem(id=checklist_item_id(t), text=t) for t in checklist],
            grounded=bool(chunk_ids),
        )
        out.append(Generated(node, str(step.get("description", "")).strip(), chunk_ids))
    return out


async def summarize_generated(
    llm: LLMBackend, g: Generated, chunks: dict[str, Chunk], profile: str
) -> None:
    if g.chunk_ids:
        built = BuiltNode(g.node.id, g.node.title, g.description, [], g.chunk_ids)
        g.node.summary, g.node.citations = await summarize(llm, built, chunks)
        g.node.grounded = bool(g.node.citations)
        if g.node.grounded:
            return
    result = await llm.complete(
        GENERAL_SUMMARY_PROMPT.format(
            title=g.node.title, profile=profile, description=g.description
        ),
        GENERAL_SUMMARY_SCHEMA,
    )
    g.node.summary = str(result.get("summary", "")).strip() or g.description
    g.node.citations = []
    g.node.grounded = False


async def generated_edges(
    llm: LLMBackend,
    onto: Ontology,
    base: Overlay,
    generated: Sequence[Node],
    facet_ans: Mapping[str, Sequence[str]],
    progress: Progress,
) -> list[Edge]:
    """Prerequisites of each generated step among the project's combined steps."""
    graph, _ = compose(onto, base.model_copy(update={"nodes": list(generated)}), "", facet_ans)
    keys = {e.key for e in graph.edges}
    listing = _listing(graph.nodes)
    proposed: list[tuple[float, str, str]] = []
    for i, node in enumerate(generated, 1):
        others = [n.id for n in graph.nodes if n.id != node.id]
        if not others:
            continue
        progress(f"Placing project step {i}/{len(generated)}: {node.title}")
        result = await llm.complete(
            PREREQ_PROMPT.format(steps=listing, id=node.id, title=node.title),
            prereq_schema(others),
        )
        for p in result.get("prerequisites") or []:
            if p.get("id") in others:
                proposed.append((float(p.get("confidence", 0)), p["id"], node.id))
    proposed.sort(key=lambda e: -e[0])
    out: list[Edge] = []
    for _, source, target in proposed:
        key = (source, target)
        if key in keys or would_create_cycle(keys, key):
            continue
        keys.add(key)
        out.append(Edge.of(source, target))
    return out


async def tailor(
    llm: LLMBackend,
    defn: InterviewDef,
    onto: Ontology,
    chunks: dict[str, Chunk],
    answers: Mapping[str, Answer],
    followups: Sequence[FollowUp],
    progress: Progress = lambda _m: None,
) -> Overlay:
    """A fresh overlay for these answers (no proposals): what a new project starts with."""
    profile = profile_text(defn, answers, followups)
    facet_ans = facet_answers(defn, answers)
    accepted = [n for n in onto.nodes if n.status == "accepted"]
    candidates = [n for n in accepted if is_universal(n.facets) or matches(n.facets, facet_ans)]
    include = await select_steps(llm, candidates, profile, progress)
    base = Overlay(
        answers=dict(answers),
        followups=list(followups),
        included=[n.id for n in candidates if n.id in include],
        excluded=[n.id for n in candidates if n.id not in include],
        seen=[n.id for n in accepted],
    )
    selected = [n for n in candidates if n.id in include]
    sources = retrieve(chunks, facet_ans)
    generated = await generate_steps(llm, profile, selected, sources, set(), progress)
    for i, g in enumerate(generated, 1):
        progress(f"Writing project step {i}/{len(generated)}: {g.node.title}")
        await summarize_generated(llm, g, chunks, profile)
    nodes = [g.node for g in generated]
    edges = await generated_edges(llm, onto, base, nodes, facet_ans, progress)
    return base.model_copy(update={"nodes": nodes, "edges": edges})


def untailored(onto: Ontology) -> Overlay:
    """The overlay equivalent of an untailored project: every accepted global step."""
    ids = [n.id for n in onto.nodes if n.status == "accepted"]
    return Overlay(included=ids, seen=ids)


def retailor_diff(defn: InterviewDef, onto: Ontology, current: Overlay, fresh: Overlay) -> Overlay:
    """Apply a re-run interview to a project as proposals, so nothing the user sees changes
    until they accept it. The current view is pinned (explicitly included/excluded) because the
    new answers would otherwise change facet matching immediately."""
    old_visible = visible_global(onto, current, facet_answers(defn, current.answers))
    new_visible = visible_global(onto, fresh, facet_answers(defn, fresh.answers))
    accepted = [n.id for n in onto.nodes if n.status == "accepted"]
    old_set, new_set = set(old_visible), set(new_visible)

    proposals: list[Proposal] = [
        Proposal(kind="exclude", node_id=i) for i in old_visible if i not in new_set
    ]
    proposals += [Proposal(kind="include", node_id=i) for i in new_visible if i not in old_set]

    current_ids = {n.id for n in current.nodes}
    current_titles = {n.title.strip().lower(): n.id for n in current.nodes}
    renamed: dict[str, str] = {}
    for n in fresh.nodes:
        same = current_titles.get(n.title.strip().lower())
        if same is not None:
            renamed[n.id] = same
            continue
        node_id = generated_id(n.title, current_ids | {p.node_id or "" for p in proposals})
        renamed[n.id] = node_id
        node = n.model_copy(deep=True, update={"id": node_id})
        proposals.append(Proposal(kind="add_node", node_id=node_id, node=node))
    kept = set(renamed.values())
    for n in current.nodes:
        if n.id not in kept and not n.locked_fields and n.id not in current.promoted:
            proposals.append(Proposal(kind="remove_node", node_id=n.id))

    current_edges = {e.key for e in current.edges}
    for e in fresh.edges:
        key = (renamed.get(e.source, e.source), renamed.get(e.target, e.target))
        if key not in current_edges:
            proposals.append(Proposal(kind="add_edge", source=key[0], target=key[1]))

    rejected = set(current.rejected)
    proposals = [p for p in proposals if p.key not in rejected]
    return current.model_copy(
        update={
            "answers": dict(fresh.answers),
            "followups": list(fresh.followups),
            "included": list(dict.fromkeys(old_visible)),
            "excluded": [i for i in accepted if i not in old_set],
            "seen": list(dict.fromkeys([*current.seen, *accepted])),
            "proposals": proposals,
        }
    )
