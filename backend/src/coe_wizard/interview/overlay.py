"""Per-project tailoring, stored in data/projects/<project id>.yaml next to the global ontology.

A project's graph is composed from the global ontology and its overlay:

    visible global = accepted global steps that are explicitly `included`, or universal (no facet
                     tags), or whose facets match the project's facet answers;
                     minus `excluded`, minus global copies of steps this project promoted
    + generated    = the project's own steps (ids start with "gen."), with edges to anything
    + bridges      = A -> B wherever the global graph has A -> (hidden steps) -> B

Projects without an overlay (created before interviews existed) see the whole global graph.
"""

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..chunks import Chunk
from ..facets import is_universal, matches
from ..ontology.editor import (
    ChecklistInput,
    CitationInput,
    EditError,
    build_checklist,
    build_citations,
)
from ..ontology.model import (
    LOCKABLE_FIELDS,
    Edge,
    Node,
    Ontology,
    find_cycle,
    slugify,
    would_create_cycle,
)
from ..ontology.store import OntologyStore, YamlStore
from ..progress_view import ProjectMeta, ProposalView
from ..state import ProgressDB
from .definition import InterviewDef, QuestionKind

GEN_PREFIX = "gen."
Answer = str | list[str]


def is_generated(node_id: str) -> bool:
    return node_id.startswith(GEN_PREFIX)


def generated_id(title: str, taken: set[str]) -> str:
    base = GEN_PREFIX + slugify(title)
    out, i = base, 2
    while out in taken:
        out, i = f"{base}-{i}", i + 1
    return out


class FollowUp(BaseModel):
    """An adaptive question the LLM asked during the interview, with the user's answer."""

    id: str
    section: str
    text: str
    kind: QuestionKind = "text"
    options: list[str] = Field(default_factory=list)
    answer: Answer | None = None


ProposalKind = Literal["include", "exclude", "add_node", "remove_node", "add_edge"]


class Proposal(BaseModel):
    kind: ProposalKind
    node_id: str | None = None
    node: Node | None = None  # add_node only
    source: str | None = None
    target: str | None = None

    @property
    def key(self) -> str:
        if self.kind == "add_edge":
            return f"add_edge:{self.source}->{self.target}"
        return f"{self.kind}:{self.node_id}"


class Overlay(BaseModel):
    version: int = 1
    answers: dict[str, Answer] = Field(default_factory=dict)
    followups: list[FollowUp] = Field(default_factory=list)
    included: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    # Global ids the project has seen; accepted global steps outside it are flagged "new".
    seen: list[str] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    proposals: list[Proposal] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)  # proposal keys not to offer again
    promoted: dict[str, str] = Field(default_factory=dict)  # generated id -> global copy id

    @model_validator(mode="after")
    def _check(self) -> Overlay:
        ids = [n.id for n in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate project step ids")
        bad = [i for i in ids if not is_generated(i)]
        if bad:
            raise ValueError(f"Project step ids must start with '{GEN_PREFIX}': {bad}")
        for e in self.edges:
            if e.source == e.target:
                raise ValueError(f"Edge {e.source} -> {e.target} is a self-loop")
        cycle = find_cycle({x for e in self.edges for x in e.key}, (e.key for e in self.edges))
        if cycle:
            raise ValueError(f"Project edges have a cycle: {' -> '.join(cycle)}")
        return self

    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def edge(self, source: str, target: str) -> Edge | None:
        return next((e for e in self.edges if e.key == (source, target)), None)

    def drop_node(self, node_id: str) -> None:
        self.nodes = [n for n in self.nodes if n.id != node_id]
        self.edges = [e for e in self.edges if node_id not in e.key]


def _as_list(value: Answer | None) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def facet_answers(defn: InterviewDef, answers: Mapping[str, Answer]) -> dict[str, list[str]]:
    return {
        q.id: _as_list(answers.get(q.id))
        for q in defn.accepted_questions()
        if q.facet and _as_list(answers.get(q.id))
    }


def visible_global(
    onto: Ontology, overlay: Overlay, answers: Mapping[str, Sequence[str]]
) -> list[str]:
    """Accepted global step ids this project shows, in ontology order."""
    hidden = set(overlay.excluded) | set(overlay.promoted.values())
    included = set(overlay.included)
    return [
        n.id
        for n in onto.nodes
        if n.status == "accepted"
        and n.id not in hidden
        and (n.id in included or is_universal(n.facets) or matches(n.facets, answers))
    ]


def bridge_edges(onto: Ontology, visible: set[str]) -> set[tuple[str, str]]:
    """A -> B for visible A, B joined only through hidden accepted steps, so hiding a step
    doesn't lose the order of what's around it."""
    accepted = {n.id for n in onto.nodes if n.status == "accepted"}
    preds: dict[str, list[str]] = defaultdict(list)
    for e in onto.edges:
        if e.status == "accepted" and e.source in accepted and e.target in accepted:
            preds[e.target].append(e.source)
    out: set[tuple[str, str]] = set()
    for v in visible:
        stack = [p for p in preds[v] if p not in visible]
        seen = set(stack)
        while stack:
            for p in preds[stack.pop()]:
                if p in visible:
                    out.add((p, v))
                elif p not in seen:
                    seen.add(p)
                    stack.append(p)
    return out


def compose(
    onto: Ontology,
    overlay: Overlay,
    overlay_hash: str,
    answers: Mapping[str, Sequence[str]],
    include_proposed: bool = False,
) -> tuple[Ontology, ProjectMeta]:
    """The project's graph, plus which parts of it came from the overlay. Dangling overlay
    references (e.g. to a since-deleted global step) and overlay edges that would close a cycle
    with newer global edges are dropped."""
    visible = visible_global(onto, overlay, answers)
    vis = set(visible)
    promoted_targets = set(overlay.promoted.values())
    nodes = [n for n in onto.nodes if n.id in vis]
    if include_proposed:
        nodes += [n for n in onto.nodes if n.status == "proposed" and n.id not in promoted_targets]
    nodes += overlay.nodes
    ids = {n.id for n in nodes}

    edges = [e.model_copy() for e in onto.edges if e.source in ids and e.target in ids]
    keys = {e.key for e in edges}
    bridges: set[tuple[str, str]] = set()
    for key in sorted(bridge_edges(onto, vis)):
        if key not in keys:
            edges.append(Edge.of(*key))
            keys.add(key)
            bridges.add(key)
    project_edges: set[tuple[str, str]] = set()
    for e in overlay.edges:
        if e.source in ids and e.target in ids and e.key not in keys:
            if would_create_cycle(keys, e.key):
                continue
            edges.append(e.model_copy())
            keys.add(e.key)
            project_edges.add(e.key)

    new_nodes = vis - set(overlay.seen)
    meta = ProjectMeta(
        overlay_hash=overlay_hash,
        project_nodes={n.id for n in overlay.nodes},
        project_edges=project_edges,
        bridge_edges=bridges,
        new_nodes=new_nodes,
        promoted=dict(overlay.promoted),
        proposals=proposal_views(onto, overlay, [i for i in visible if i in new_nodes]),
    )
    return Ontology(nodes=nodes, edges=edges), meta


def proposal_views(
    onto: Ontology, overlay: Overlay, new_nodes: Sequence[str]
) -> list[ProposalView]:
    titles = {n.id: n.title for n in onto.nodes} | {n.id: n.title for n in overlay.nodes}
    for p in overlay.proposals:
        if p.node is not None:
            titles.setdefault(p.node.id, p.node.title)

    def title(node_id: str | None) -> str:
        return titles.get(node_id or "", node_id or "")

    out: list[ProposalView] = []
    for p in overlay.proposals:
        if p.kind == "add_edge":
            label = f"{title(p.source)} → {title(p.target)}"
        else:
            label = title(p.node_id)
        out.append(
            ProposalView(
                key=p.key,
                kind=p.kind,
                title=label,
                node_id=p.node_id,
                source=p.source,
                target=p.target,
                grounded=p.node.grounded if p.node is not None else True,
            )
        )
    out += [
        ProposalView(key=f"new:{nid}", kind="new", title=title(nid), node_id=nid)
        for nid in new_nodes
    ]
    return out


class OverlayRepo:
    """All project overlays. Also keeps their references to global ids in step with global
    deletes and merges (Editor calls `retarget`)."""

    def __init__(self, directory: Path, backups_dir: Path) -> None:
        self.dir = directory
        self.backups_dir = backups_dir
        self._stores: dict[int, YamlStore[Overlay]] = {}

    def path(self, project_id: int) -> Path:
        return self.dir / f"{project_id}.yaml"

    def store(self, project_id: int) -> YamlStore[Overlay]:
        if project_id not in self._stores:
            self._stores[project_id] = YamlStore(
                self.path(project_id), self.backups_dir, Overlay, f"project-{project_id}"
            )
        return self._stores[project_id]

    def exists(self, project_id: int) -> bool:
        return self.path(project_id).exists()

    def ids(self) -> list[int]:
        return sorted(int(p.stem) for p in self.dir.glob("*.yaml") if p.stem.isdigit())

    def delete(self, project_id: int) -> None:
        if self.exists(project_id):
            self.store(project_id).delete()
        self._stores.pop(project_id, None)

    def retarget(self, source: str, target: str | None) -> None:
        """Point references to global step `source` at `target` (a merge), or drop them."""

        def swap(ids: list[str]) -> list[str]:
            out = [target if i == source else i for i in ids]
            return list(dict.fromkeys(i for i in out if i is not None))

        def fn(o: Overlay) -> None:
            o.included, o.excluded, o.seen = swap(o.included), swap(o.excluded), swap(o.seen)
            if target is not None and target in o.excluded and target in o.included:
                o.excluded.remove(target)
            edges: dict[tuple[str, str], Edge] = {}
            for e in o.edges:
                s = target if e.source == source else e.source
                t = target if e.target == source else e.target
                if s is None or t is None or s == t:
                    continue
                edges.setdefault((s, t), e.model_copy(update={"source": s, "target": t}))
            o.edges = list(edges.values())
            if find_cycle({x for k in edges for x in k}, edges):
                o.edges = [e for e in o.edges if source not in (e.source, e.target)]
            proposals: list[Proposal] = []
            for p in o.proposals:
                refs = {p.node_id, p.source, p.target}
                if source in refs:
                    if target is None:
                        continue
                    p = p.model_copy(
                        update={
                            k: target
                            for k in ("node_id", "source", "target")
                            if getattr(p, k) == source
                        }
                    )
                proposals.append(p)
            o.proposals = proposals
            promoted: dict[str, str] = {}
            for g, v in o.promoted.items():
                if v != source:
                    promoted[g] = v
                elif target is not None:
                    promoted[g] = target
            o.promoted = promoted

        for pid in self.ids():
            store = self.store(pid)
            try:
                current, _ = store.load()
            except ValueError:
                continue  # an invalid overlay is reported when its project is opened
            refs = {*current.included, *current.excluded, *current.seen, *current.promoted.values()}
            refs |= {x for e in current.edges for x in e.key}
            refs |= {x for p in current.proposals for x in (p.node_id, p.source, p.target) if x}
            if source in refs:
                store.update(None, fn)


class OverlayEditor:
    """Edits to a tailored project: its generated steps and edges, its pending proposals, and
    promoting a generated step to the global ontology."""

    def __init__(
        self,
        overlays: OverlayRepo,
        global_store: OntologyStore,
        interview: Callable[[], InterviewDef],
        db: ProgressDB,
        chunks: Callable[[], dict[str, Chunk]],
    ) -> None:
        self.overlays = overlays
        self.global_store = global_store
        self.interview = interview
        self.db = db
        self.chunks = chunks

    def _apply(self, project_id: int, expected_hash: str, fn: Callable[[Overlay], None]) -> str:
        if not self.overlays.exists(project_id):
            raise EditError(404, "This project isn't tailored")
        _, new_hash = self.overlays.store(project_id).update(expected_hash, lambda o: fn(o) or o)
        return new_hash

    @staticmethod
    def _gen(o: Overlay, node_id: str) -> Node:
        node = o.node(node_id)
        if node is None:
            raise EditError(404, f"Unknown project step {node_id}")
        return node

    def _combined_keys(self, o: Overlay) -> tuple[set[str], set[tuple[str, str]]]:
        onto, _ = self.global_store.load()
        graph, _ = compose(onto, o, "", facet_answers(self.interview(), o.answers))
        return {n.id for n in graph.nodes}, {e.key for e in graph.edges}

    # --- Generated steps -------------------------------------------------------------------

    def rename_node(self, project_id: int, expected_hash: str, node_id: str, title: str) -> str:
        if not title.strip():
            raise EditError(422, "Title is required")

        def fn(o: Overlay) -> None:
            n = self._gen(o, node_id)
            n.title = title.strip()
            n.lock("title")

        return self._apply(project_id, expected_hash, fn)

    def set_checklist(
        self, project_id: int, expected_hash: str, node_id: str, items: list[ChecklistInput]
    ) -> str:
        def fn(o: Overlay) -> None:
            n = self._gen(o, node_id)
            n.checklist = build_checklist(items)
            n.lock("checklist")

        return self._apply(project_id, expected_hash, fn)

    def set_citations(
        self, project_id: int, expected_hash: str, node_id: str, items: list[CitationInput]
    ) -> str:
        chunks = self.chunks()

        def fn(o: Overlay) -> None:
            n = self._gen(o, node_id)
            n.citations = build_citations(n, items, chunks)
            n.grounded = n.grounded or bool(n.citations)
            n.lock("citations")

        return self._apply(project_id, expected_hash, fn)

    def delete_node(self, project_id: int, expected_hash: str, node_id: str) -> str:
        def fn(o: Overlay) -> None:
            self._gen(o, node_id)
            o.drop_node(node_id)
            o.promoted.pop(node_id, None)

        with self.db.transaction() as c:
            ProgressDB.set_hidden(c, node_id, True, project_id)
            return self._apply(project_id, expected_hash, fn)

    def add_edge(self, project_id: int, expected_hash: str, source: str, target: str) -> str:
        if not (is_generated(source) or is_generated(target)):
            raise EditError(422, "Edges between global steps are edited in the global ontology")

        def fn(o: Overlay) -> None:
            ids, keys = self._combined_keys(o)
            for node_id in (source, target):
                if node_id not in ids:
                    raise EditError(404, f"Unknown node {node_id}")
            key = (source, target)
            if key in keys:
                return
            if would_create_cycle(keys, key):
                raise EditError(409, f"Edge {source} -> {target} would create a cycle")
            o.edges.append(Edge.of(source, target, locked=True))

        return self._apply(project_id, expected_hash, fn)

    def delete_edge(self, project_id: int, expected_hash: str, source: str, target: str) -> str:
        def fn(o: Overlay) -> None:
            if o.edge(source, target) is None:
                raise EditError(404, f"Unknown project edge {source} -> {target}")
            o.edges = [e for e in o.edges if e.key != (source, target)]

        return self._apply(project_id, expected_hash, fn)

    # --- Proposals -------------------------------------------------------------------------

    def resolve(self, project_id: int, expected_hash: str, key: str, accept: bool) -> str:
        hide: list[str] = []

        def fn(o: Overlay) -> None:
            if key.startswith("new:"):
                node_id = key.removeprefix("new:")
                if node_id not in o.seen:
                    o.seen.append(node_id)
                if not accept and node_id not in o.excluded:
                    o.excluded.append(node_id)
                return
            p = next((x for x in o.proposals if x.key == key), None)
            if p is None:
                raise EditError(404, f"No proposal {key}")
            o.proposals = [x for x in o.proposals if x.key != key]
            if not accept:
                o.rejected.append(key)
                if p.kind == "add_node":  # its edges can't be accepted any more
                    o.proposals = [x for x in o.proposals if p.node_id not in (x.source, x.target)]
                return
            node_id = p.node_id or ""
            match p.kind:
                case "include":
                    o.excluded = [i for i in o.excluded if i != node_id]
                    if node_id not in o.included:
                        o.included.append(node_id)
                case "exclude":
                    o.included = [i for i in o.included if i != node_id]
                    if node_id not in o.excluded:
                        o.excluded.append(node_id)
                case "add_node":
                    if p.node is not None and o.node(p.node.id) is None:
                        o.nodes.append(p.node.model_copy(deep=True))
                case "remove_node":
                    o.drop_node(node_id)
                    hide.append(node_id)
                case "add_edge":
                    assert p.source is not None and p.target is not None
                    ids, keys = self._combined_keys(o)
                    if p.source not in ids or p.target not in ids:
                        raise EditError(409, "Accept the steps this edge connects first")
                    if would_create_cycle(keys, (p.source, p.target)):
                        raise EditError(409, "This edge would create a cycle")
                    if (p.source, p.target) not in keys:
                        o.edges.append(Edge.of(p.source, p.target))

        with self.db.transaction() as c:
            new_hash = self._apply(project_id, expected_hash, fn)
            for node_id in hide:
                ProgressDB.set_hidden(c, node_id, True, project_id)
            return new_hash

    # --- Promotion -------------------------------------------------------------------------

    def promote(self, project_id: int, expected_hash: str, node_id: str) -> tuple[str, str]:
        """Copy a generated step into the global ontology as a proposal, tagged with this
        project's facet answers. The project keeps its own copy."""
        created: list[str] = []

        def fn(o: Overlay) -> None:
            n = self._gen(o, node_id)
            if node_id in o.promoted:
                raise EditError(409, "This step was already promoted")
            tags = facet_answers(self.interview(), o.answers)

            def add(g: Ontology) -> None:
                gid = g.unique_id(slugify(n.title))
                g.nodes.append(
                    n.model_copy(
                        deep=True,
                        update={
                            "id": gid,
                            "facets": tags,
                            "status": "proposed",
                            "locked_fields": list(LOCKABLE_FIELDS),
                        },
                    )
                )
                created.append(gid)

            self.global_store.update(None, add)
            o.promoted[node_id] = created[0]

        new_hash = self._apply(project_id, expected_hash, fn)
        return created[0], new_hash
