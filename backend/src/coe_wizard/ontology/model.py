"""Ontology schema: the curated DAG of wizard steps."""

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal["accepted", "proposed"]
LockableField = Literal["title", "checklist", "citations"]
LOCKABLE_FIELDS: tuple[LockableField, ...] = ("title", "checklist", "citations")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "node"


def checklist_item_id(text: str) -> str:
    """Stable for identical text, so progress survives rebuilds that keep the wording."""
    return hashlib.sha256(" ".join(text.split()).lower().encode()).hexdigest()[:10]


class ChecklistItem(BaseModel):
    id: str
    text: str


class Citation(BaseModel):
    # The [n] marker this citation answers to in the summary. Stable under edits, so removing a
    # citation leaves a dangling marker rather than silently renumbering the others.
    n: int
    chunk_id: str
    excerpt: str
    url: str
    doc_title: str = ""


class Node(BaseModel):
    id: str
    title: str
    summary: str = ""
    checklist: list[ChecklistItem] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    status: Status = "accepted"
    locked_fields: list[LockableField] = Field(default_factory=list)

    def lock(self, field: LockableField) -> None:
        if field not in self.locked_fields:
            self.locked_fields.append(field)


class Edge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="from")
    target: str = Field(alias="to")
    status: Status = "accepted"
    locked: bool = False

    @classmethod
    def of(
        cls, source: str, target: str, status: Status = "accepted", locked: bool = False
    ) -> Edge:
        return cls.model_validate(
            {"from": source, "to": target, "status": status, "locked": locked}
        )

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.target)


class Rejected(BaseModel):
    """Things the curator removed, so rebuilds don't propose them again."""

    nodes: list[str] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(default_factory=list)


class Ontology(BaseModel):
    version: int = 1
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    rejected: Rejected = Field(default_factory=Rejected)

    @model_validator(mode="after")
    def _check_graph(self) -> Ontology:
        ids = [n.id for n in self.nodes]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"Duplicate node ids: {sorted(dupes)}")
        known = set(ids)
        for e in self.edges:
            if e.source not in known or e.target not in known:
                raise ValueError(f"Edge {e.source} -> {e.target} references an unknown node")
            if e.source == e.target:
                raise ValueError(f"Edge {e.source} -> {e.target} is a self-loop")
        cycle = find_cycle(known, (e.key for e in self.edges))
        if cycle:
            raise ValueError(f"Graph has a cycle: {' -> '.join(cycle)}")
        return self

    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def edge(self, source: str, target: str) -> Edge | None:
        return next((e for e in self.edges if e.key == (source, target)), None)

    def unique_id(self, base: str) -> str:
        taken = {n.id for n in self.nodes} | set(self.rejected.nodes)
        candidate, i = base, 2
        while candidate in taken:
            candidate, i = f"{base}-{i}", i + 1
        return candidate


def find_cycle(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> list[str] | None:
    adj: dict[str, list[str]] = defaultdict(list)
    for s, t in edges:
        adj[s].append(t)
    white, grey, black = 0, 1, 2
    color: dict[str, int] = defaultdict(int)
    parent: dict[str, str] = {}
    for start in nodes:
        if color[start] != white:
            continue
        stack = [(start, iter(adj[start]))]
        color[start] = grey
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                color[node] = black
                stack.pop()
            elif color[nxt] == grey:
                path = [nxt, node]
                while path[-1] != nxt:
                    path.append(parent[path[-1]])
                return list(reversed(path))
            elif color[nxt] == white:
                parent[nxt] = node
                color[nxt] = grey
                stack.append((nxt, iter(adj[nxt])))
    return None


def would_create_cycle(edges: Iterable[tuple[str, str]], new: tuple[str, str]) -> bool:
    """True if adding `new` (source -> target) closes a cycle: target already reaches source."""
    source, target = new
    if source == target:
        return True
    adj: dict[str, list[str]] = defaultdict(list)
    for s, t in edges:
        adj[s].append(t)
    stack, seen = [target], {target}
    while stack:
        cur = stack.pop()
        if cur == source:
            return True
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False
