"""Combines the ontology with a project's progress into what the wizard renders."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from .ontology.model import Citation, Facets, Node, Ontology, Status
from .state import NodeState

WizardState = Literal["complete", "frontier", "locked"]
# global: from ontology.yaml; project: the project's overlay; bridge: stands in for a path
# through global steps the project hides (not editable).
Scope = Literal["global", "project", "bridge"]


class ProposalView(BaseModel):
    """A pending change to a tailored project (re-tailor diff, or a newly matching global step)."""

    key: str
    kind: Literal["include", "exclude", "add_node", "remove_node", "add_edge", "new"]
    title: str
    node_id: str | None = None
    source: str | None = None
    target: str | None = None
    grounded: bool = True


@dataclass
class ProjectMeta:
    """What a project's overlay contributes to its view (see interview/overlay.py)."""

    overlay_hash: str
    project_nodes: set[str] = field(default_factory=set)
    project_edges: set[tuple[str, str]] = field(default_factory=set)
    bridge_edges: set[tuple[str, str]] = field(default_factory=set)
    new_nodes: set[str] = field(default_factory=set)
    promoted: dict[str, str] = field(default_factory=dict)
    proposals: list[ProposalView] = field(default_factory=list)


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
    facets: Facets = Field(default_factory=dict)
    grounded: bool = True
    scope: Scope = "global"
    is_new: bool = False
    promoted_to: str | None = None


class EdgeView(BaseModel):
    source: str
    target: str
    status: Status
    locked: bool
    scope: Scope = "global"


class GraphView(BaseModel):
    nodes: list[NodeView]
    edges: list[EdgeView]
    hash: str
    proposals: int
    # Set for tailored projects (those with an overlay); untailored ones show the global graph.
    tailored: bool = False
    overlay_hash: str | None = None
    project_proposals: list[ProposalView] = Field(default_factory=list)


def build_view(
    onto: Ontology,
    progress: dict[str, NodeState],
    onto_hash: str,
    include_proposed: bool,
    live_chunks: set[str] | None,
    project: ProjectMeta | None = None,
) -> GraphView:
    """Frontier = incomplete accepted nodes whose accepted predecessors are all complete.

    Proposed nodes and edges never affect gating, and are only included in edit mode.
    `live_chunks=None` skips broken-citation detection (no chunk file yet). For a tailored
    project, `onto` is the composed graph and `project` says which parts came from the overlay.
    """
    meta = project or ProjectMeta(overlay_hash="")

    def edge_scope(key: tuple[str, str]) -> Scope:
        if key in meta.project_edges:
            return "project"
        return "bridge" if key in meta.bridge_edges else "global"

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
                EdgeView(
                    source=e.source,
                    target=e.target,
                    status=e.status,
                    locked=e.locked,
                    scope=edge_scope(e.key),
                )
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
            facets=n.facets,
            grounded=n.grounded,
            scope="project" if n.id in meta.project_nodes else "global",
            is_new=n.id in meta.new_nodes,
            promoted_to=meta.promoted.get(n.id),
        )

    global_nodes = [n for n in onto.nodes if n.id not in meta.project_nodes]
    return GraphView(
        nodes=[view(n) for n in onto.nodes if n.id in visible],
        edges=edges,
        hash=onto_hash,
        proposals=sum(n.status == "proposed" for n in global_nodes)
        + sum(
            e.status == "proposed" and e.key not in meta.project_edges for e in onto.edges
        ),
        tailored=project is not None,
        overlay_hash=project.overlay_hash if project else None,
        project_proposals=meta.proposals,
    )
