"""Merging a freshly built ontology into the curated one.

Rules:
- First build (nothing curated yet): the built graph is taken as-is, all accepted.
- Fields listed in a node's `locked_fields` are never overwritten. Summaries are LLM-owned and
  always refresh.
- Nodes/edges the LLM produces that don't exist yet arrive as `proposed`, unless the curator
  previously rejected or deleted them.
- Stale proposals (no longer produced by the LLM) are dropped. Accepted items are never removed
  by a rebuild.
"""

import re
from collections.abc import Sequence

from .model import Citation, Edge, Node, Ontology, would_create_cycle

_MARKER = re.compile(r"\[(\d+)\]")


def remap_markers(summary: str, fresh: Sequence[Citation], kept: Sequence[Citation]) -> str:
    """Point a fresh summary's [n] markers at the curator's locked citations, matching by chunk.
    Markers whose chunk isn't among the locked citations are dropped."""
    fresh_chunk = {c.n: c.chunk_id for c in fresh}
    kept_n = {c.chunk_id: c.n for c in kept}

    def repl(m: re.Match[str]) -> str:
        n = kept_n.get(fresh_chunk.get(int(m.group(1)), ""))
        return f"[{n}]" if n is not None else ""

    return _MARKER.sub(repl, summary)


def merge_ontology(existing: Ontology, built: Ontology) -> Ontology:
    if not existing.nodes:
        out = built.model_copy(deep=True)
        out.rejected = existing.rejected.model_copy(deep=True)
        for n in out.nodes:
            n.status = "accepted"
        for e in out.edges:
            e.status = "accepted"
        return out

    built_nodes = {n.id: n for n in built.nodes}
    rejected_nodes = set(existing.rejected.nodes)
    rejected_edges = {tuple(e) for e in existing.rejected.edges}

    nodes: list[Node] = []
    for cur in existing.nodes:
        fresh = built_nodes.get(cur.id)
        if fresh is None:
            if cur.status == "proposed":
                continue
            nodes.append(cur.model_copy(deep=True))
            continue
        merged = cur.model_copy(deep=True)
        if "title" not in cur.locked_fields:
            merged.title = fresh.title
        if "checklist" not in cur.locked_fields:
            merged.checklist = [c.model_copy() for c in fresh.checklist]
        if "citations" not in cur.locked_fields:
            merged.citations = [c.model_copy() for c in fresh.citations]
            merged.summary = fresh.summary
        else:
            merged.summary = remap_markers(fresh.summary, fresh.citations, cur.citations)
        nodes.append(merged)

    known = {n.id for n in nodes}
    for fresh in built.nodes:
        if fresh.id in known or fresh.id in rejected_nodes:
            continue
        added = fresh.model_copy(deep=True)
        added.status = "proposed"
        added.locked_fields = []
        nodes.append(added)
        known.add(added.id)

    built_edges = {e.key for e in built.edges}
    edges: list[Edge] = []
    for cur in existing.edges:
        if cur.source not in known or cur.target not in known:
            continue
        if cur.status == "proposed" and cur.key not in built_edges:
            continue
        edges.append(cur.model_copy())

    have = {e.key for e in edges}
    for fresh in built.edges:
        key = fresh.key
        if key in have or key in rejected_edges:
            continue
        if fresh.source not in known or fresh.target not in known:
            continue
        if would_create_cycle(have, key):
            continue
        edges.append(Edge.of(key[0], key[1], status="proposed"))
        have.add(key)

    return Ontology(nodes=nodes, edges=edges, rejected=existing.rejected.model_copy(deep=True))
