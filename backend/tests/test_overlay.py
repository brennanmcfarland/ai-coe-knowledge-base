from collections.abc import Iterator
from pathlib import Path

import pytest

from coe_wizard.chunks import Chunk
from coe_wizard.facets import matches
from coe_wizard.interview.definition import (
    InterviewDef,
    Option,
    ProposedQuestion,
    Question,
    Section,
    ensure_seeded,
    merge_proposals,
)
from coe_wizard.interview.overlay import (
    Overlay,
    OverlayEditor,
    OverlayRepo,
    Proposal,
    compose,
    facet_answers,
)
from coe_wizard.ontology.editor import CitationInput, EditError, Editor
from coe_wizard.ontology.merge import merge_ontology
from coe_wizard.ontology.model import Edge, Node, Ontology
from coe_wizard.ontology.store import EMPTY_HASH, OntologyStore, StaleHashError, YamlStore
from coe_wizard.state import ProgressDB

from .conftest import make_node, make_onto

DEFN = InterviewDef(
    sections=[Section(id="s", title="S")],
    questions=[
        Question(
            id="domain",
            section="s",
            text="Domain?",
            kind="multi",
            facet=True,
            options=[Option(value="oncology", label="Oncology"), Option(value="cardio", label="C")],
        ),
        Question(
            id="data",
            section="s",
            text="Data?",
            kind="single",
            facet=True,
            options=[Option(value="genomic", label="G"), Option(value="imaging", label="I")],
        ),
    ],
)


def tagged(node_id: str, **facets: list[str]) -> Node:
    return make_node(node_id, facets=facets)


# --- Interview definition ------------------------------------------------------------------


def test_seed_is_copied_and_valid(tmp_path: Path) -> None:
    path = tmp_path / "interview.yaml"
    ensure_seeded(path)
    defn, _ = YamlStore(path, None, InterviewDef).load()
    assert [s.id for s in defn.sections] == ["app", "domain", "data", "risk"]
    assert "oncology" in defn.facets()["domain"]
    path.write_text("version: 1\nsections: []\nquestions: []\n")
    ensure_seeded(path)  # never overwrites
    assert YamlStore(path, None, InterviewDef).load()[0].sections == []


@pytest.mark.parametrize(
    ("questions", "message"),
    [
        ([{"id": "a", "section": "nope", "text": "?", "kind": "text"}], "unknown section"),
        (
            [
                {"id": "a", "section": "s", "text": "?", "kind": "text"},
                {"id": "a", "section": "s", "text": "?", "kind": "text"},
            ],
            "Duplicate question ids",
        ),
        ([{"id": "a", "section": "s", "text": "?", "kind": "single"}], "no accepted options"),
        ([{"id": "a", "section": "s", "text": "?", "kind": "text", "facet": True}], "facet"),
    ],
)
def test_invalid_definitions_are_rejected(questions: list[dict[str, object]], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        InterviewDef.model_validate(
            {"sections": [{"id": "s", "title": "S"}], "questions": questions}
        )


def test_build_proposals_follow_curation_rules() -> None:
    defn = DEFN.model_copy(deep=True)
    defn.rejected.options.append(("domain", "neurology"))
    out = merge_proposals(
        defn,
        {"domain": ["Dermatology", "Neurology", "oncology"], "missing": ["x"]},
        [
            ProposedQuestion(
                text="Which imaging?", section="s", kind="single", options=["CT", "MR"]
            ),
            ProposedQuestion(text="Domain?", section="nowhere", kind="single", options=["x"]),
        ],
    )
    domain = out.question("domain")
    assert domain is not None
    assert [(o.value, o.status) for o in domain.options][-1] == ("dermatology", "proposed")
    assert domain.option("neurology") is None  # rejected before
    new_q = out.question("which_imaging")
    assert new_q is not None and new_q.status == "proposed" and new_q.facet
    assert out.proposals() == 2
    assert DEFN.facets() == out.facets()  # proposals aren't facets until accepted


# --- Facets & composition ------------------------------------------------------------------


def test_facet_matching_is_and_across_dimensions() -> None:
    answers = {"domain": ["oncology"], "data": ["genomic"]}
    assert matches({"domain": ["oncology", "cardio"]}, answers)
    assert not matches({"domain": ["oncology"], "data": ["imaging"]}, answers)
    assert matches({"domain": ["oncology"], "users": ["x"]}, answers)  # unanswered: ignored
    assert not matches({"users": ["x"]}, answers)


def global_onto() -> Ontology:
    return Ontology(
        nodes=[
            make_node("goals"),
            tagged("onco-data", domain=["oncology"]),
            tagged("cardio-data", domain=["cardio"]),
            make_node("deploy"),
        ],
        edges=[
            Edge.of("goals", "cardio-data"),
            Edge.of("cardio-data", "deploy"),
            Edge.of("goals", "onco-data"),
        ],
    )


def test_compose_filters_by_facets_and_bridges_hidden_steps() -> None:
    onto = global_onto()
    overlay = Overlay(answers={"domain": ["oncology"]}, seen=["goals", "onco-data", "cardio-data"])
    graph, meta = compose(onto, overlay, "h", facet_answers(DEFN, overlay.answers))
    assert [n.id for n in graph.nodes] == ["goals", "onco-data", "deploy"]
    assert ("goals", "deploy") in {e.key for e in graph.edges}  # through hidden cardio-data
    assert meta.bridge_edges == {("goals", "deploy")}
    assert meta.new_nodes == {"deploy"}
    assert [p.key for p in meta.proposals] == ["new:deploy"]


def test_compose_honours_include_exclude_and_generated_steps() -> None:
    onto = global_onto()
    overlay = Overlay(
        answers={"domain": ["oncology"]},
        included=["cardio-data"],
        excluded=["deploy"],
        nodes=[Node(id="gen.terms", title="Terms")],
        edges=[
            Edge.of("onco-data", "gen.terms"),
            Edge.of("gen.terms", "goals"),  # would close a cycle with goals -> onco-data
            Edge.of("gen.terms", "gone"),  # dangling
        ],
    )
    graph, meta = compose(onto, overlay, "h", facet_answers(DEFN, overlay.answers))
    assert [n.id for n in graph.nodes] == ["goals", "onco-data", "cardio-data", "gen.terms"]
    assert meta.project_edges == {("onco-data", "gen.terms")}
    assert meta.project_nodes == {"gen.terms"}


def test_overlay_ids_must_be_generated() -> None:
    with pytest.raises(ValueError, match="must start with 'gen.'"):
        Overlay(nodes=[Node(id="terms", title="T")])


# --- Overlay editing -----------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> OverlayRepo:
    return OverlayRepo(tmp_path / "projects", tmp_path / "backups")


@pytest.fixture
def setup(
    tmp_path: Path, store: OntologyStore, db: ProgressDB, repo: OverlayRepo
) -> Iterator[tuple[OverlayEditor, int, str]]:
    store.update(EMPTY_HASH, lambda _o: global_onto())
    pid = db.create_project("P").id
    overlay = Overlay(
        answers={"domain": ["oncology"]},
        seen=["goals", "onco-data", "cardio-data", "deploy"],
        nodes=[Node(id="gen.terms", title="Terms", grounded=False)],
        edges=[Edge.of("onco-data", "gen.terms")],
    )
    _, h = repo.store(pid).update(None, lambda _o: overlay)
    chunks = {"c1": Chunk("c1", "d", "p", "Doc", "Page", "https://u", "ICD-O-3 text")}
    yield OverlayEditor(repo, store, lambda: DEFN, db, lambda: chunks), pid, h


def test_overlay_edges_reject_cycles_and_stale_hashes(
    setup: tuple[OverlayEditor, int, str],
) -> None:
    ed, pid, h = setup
    h = ed.add_edge(pid, h, "gen.terms", "deploy")
    with pytest.raises(EditError, match="cycle"):
        ed.add_edge(pid, h, "deploy", "gen.terms")
    with pytest.raises(EditError, match="global ontology"):
        ed.add_edge(pid, h, "goals", "deploy")
    with pytest.raises(StaleHashError):
        ed.rename_node(pid, "old", "gen.terms", "X")


def test_deleting_a_generated_step_hides_progress_only_in_that_project(
    setup: tuple[OverlayEditor, int, str], db: ProgressDB
) -> None:
    ed, pid, h = setup
    other = db.create_project("Other").id
    db.set_completed(pid, "gen.terms", True)
    db.set_completed(other, "gen.terms", True)
    ed.delete_node(pid, h, "gen.terms")
    assert "gen.terms" not in db.project_state(pid)
    assert db.project_state(other)["gen.terms"].completed


def test_citing_a_generated_step_grounds_it(setup: tuple[OverlayEditor, int, str]) -> None:
    ed, pid, h = setup
    ed.set_citations(pid, h, "gen.terms", [CitationInput("c1")])
    o, _ = ed.overlays.store(pid).load()
    node = o.node("gen.terms")
    assert node is not None and node.grounded and "citations" in node.locked_fields


def test_resolving_proposals(setup: tuple[OverlayEditor, int, str], repo: OverlayRepo) -> None:
    ed, pid, h = setup

    def add(o: Overlay) -> None:
        o.proposals = [
            Proposal(kind="exclude", node_id="goals"),
            Proposal(kind="add_node", node_id="gen.new", node=Node(id="gen.new", title="New")),
            Proposal(kind="add_edge", source="gen.new", target="deploy"),
            Proposal(kind="include", node_id="cardio-data"),
        ]

    _, h = repo.store(pid).update(h, add)
    with pytest.raises(EditError, match="Accept the steps"):
        ed.resolve(pid, h, "add_edge:gen.new->deploy", True)
    h = ed.resolve(pid, h, "add_node:gen.new", True)
    h = ed.resolve(pid, h, "add_edge:gen.new->deploy", True)
    h = ed.resolve(pid, h, "exclude:goals", True)
    h = ed.resolve(pid, h, "include:cardio-data", False)
    o, _ = repo.store(pid).load()
    assert o.proposals == [] and o.rejected == ["include:cardio-data"]
    assert o.node("gen.new") is not None and o.edge("gen.new", "deploy") is not None
    assert "goals" in o.excluded


def test_new_global_steps_can_be_kept_or_excluded(
    setup: tuple[OverlayEditor, int, str], repo: OverlayRepo
) -> None:
    ed, pid, h = setup
    _, h = repo.store(pid).update(h, lambda o: setattr(o, "seen", ["goals"]) or o)
    h = ed.resolve(pid, h, "new:onco-data", True)
    h = ed.resolve(pid, h, "new:deploy", False)
    o, _ = repo.store(pid).load()
    assert {"onco-data", "deploy"} <= set(o.seen) and o.excluded == ["deploy"]


def test_promote_copies_to_global_as_locked_proposal(
    setup: tuple[OverlayEditor, int, str], store: OntologyStore, db: ProgressDB
) -> None:
    ed, pid, h = setup
    gid, h = ed.promote(pid, h, "gen.terms")
    onto, gh = store.load()
    g = onto.node(gid)
    assert g is not None and g.status == "proposed" and not g.grounded
    assert g.facets == {"domain": ["oncology"]} and g.locked_fields
    with pytest.raises(EditError, match="already promoted"):
        ed.promote(pid, h, "gen.terms")
    # Locked proposals survive a rebuild that doesn't produce them.
    assert merge_ontology(onto, make_onto(["goals"], [])).node(gid) is not None
    # Once accepted globally, the project still shows only its own copy.
    store.update(gh, lambda o: setattr(o.node(gid), "status", "accepted") or o)
    o, oh = ed.overlays.store(pid).load()
    graph, _ = compose(store.load()[0], o, oh, facet_answers(DEFN, o.answers))
    assert gid not in {n.id for n in graph.nodes}
    # Attaching a citation globally clears "Not from KB".
    Editor(store, db, ed.chunks).set_citations(store.load()[1], gid, [CitationInput("c1")])
    promoted = store.load()[0].node(gid)
    assert promoted is not None and promoted.grounded


def test_global_delete_and_merge_update_overlays(
    setup: tuple[OverlayEditor, int, str], store: OntologyStore, db: ProgressDB, repo: OverlayRepo
) -> None:
    ed, pid, h = setup
    _, h = repo.store(pid).update(h, lambda o: setattr(o, "included", ["onco-data"]) or o)
    editor = Editor(store, db, ed.chunks, repo)
    editor.merge_nodes(store.load()[1], "onco-data", "cardio-data")
    o, _ = repo.store(pid).load()
    assert o.included == ["cardio-data"] and o.edge("cardio-data", "gen.terms") is not None
    editor.delete_node(store.load()[1], "cardio-data")
    o, _ = repo.store(pid).load()
    assert o.included == [] and o.edges == [] and "cardio-data" not in o.seen


def test_merge_refreshes_facets_unless_locked() -> None:
    cur = make_onto(["a", "b"], [])
    cur.nodes[1].locked_fields = ["facets"]
    cur.nodes[1].facets = {"domain": ["cardio"]}
    built = Ontology(nodes=[tagged("a", domain=["oncology"]), tagged("b", domain=["oncology"])])
    out = merge_ontology(cur, built)
    assert out.nodes[0].facets == {"domain": ["oncology"]}
    assert out.nodes[1].facets == {"domain": ["cardio"]}
