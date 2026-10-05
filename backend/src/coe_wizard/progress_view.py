"""Combines the ontology with a project's progress into what the wizard renders."""

from typing import Literal

from pydantic import BaseModel

from .ontology.model import Citation, Node, Ontology, Status
from .state import NodeState

WizardState = Literal["complete", "frontier", "locked"]


class CitationView(Citation):
    broken: bool = False


class NodeView(BaseModel):
    id: str
    title: str
    summary: str
    checklist: list[dict[str, str | bool]]
    citations: list[CitationView]
    status: Status
    locked_fields: list[str]
    state: WizardState
    completed: bool
    predecessors: list[str]
    successors: list[str]


class EdgeView(BaseModel):
    source: str
    target: str
    status: Status
    locked: bool


class GraphView(BaseModel):
    nodes: list[NodeView]
    edges: list[EdgeView]
    hash: str
    proposals: int


def build_view(
    onto: Ontology,
    progress: dict[str, NodeState],
    onto_hash: str,
    include_proposed: bool,
    live_chunks: set[str] | None,
) -> GraphView:
    """Frontier = incomplete accepted nodes whose accepted predecessors are all complete.

    Proposed nodes and edges never affect gating, and are only included in edit mode.
    `live_chunks=None` skips broken-citation detection (no chunk file yet).
    """
    accepted = {n.id for n in onto.nodes if n.status == "accepted"}
    visible = {n.id for n in onto.nodes} if include_proposed else accepted
    preds: dict[str, list[str]] = {n: [] for n in visible}
    succs: dict[str, list[str]] = {n: [] for n in visible}
    gating_preds: dict[str, list[str]] = {n: [] for n in accepted}
    edges: list[EdgeView] = []
    for e in onto.edges:
        if e.status == "accepted" and e.source in accepted and e.target in accepted:
            gating_preds[e.target].append(e.source)
        if (
            e.source in visible
            and e.target in visible
            and (include_proposed or e.status == "accepted")
        ):
            preds[e.target].append(e.source)
            succs[e.source].append(e.target)
            edges.append(
                EdgeView(source=e.source, target=e.target, status=e.status, locked=e.locked)
            )

    def done(node_id: str) -> bool:
        s = progress.get(node_id)
        return bool(s and s.completed)

    def view(n: Node) -> NodeView:
        s = progress.get(n.id)
        checked = s.checked if s else frozenset()
        if done(n.id):
            state: WizardState = "complete"
        elif n.status == "accepted" and all(done(p) for p in gating_preds[n.id]):
            state = "frontier"
        else:
            state = "locked"
        return NodeView(
            id=n.id,
            title=n.title,
            summary=n.summary,
            checklist=[
                {"id": i.id, "text": i.text, "checked": i.id in checked} for i in n.checklist
            ],
            citations=[
                CitationView(
                    **c.model_dump(),
                    broken=live_chunks is not None and c.chunk_id not in live_chunks,
                )
                for c in n.citations
            ],
            status=n.status,
            locked_fields=list(n.locked_fields),
            state=state,
            completed=done(n.id),
            predecessors=preds[n.id],
            successors=succs[n.id],
        )

    return GraphView(
        nodes=[view(n) for n in onto.nodes if n.id in visible],
        edges=edges,
        hash=onto_hash,
        proposals=sum(n.status == "proposed" for n in onto.nodes)
        + sum(e.status == "proposed" for e in onto.edges),
    )
