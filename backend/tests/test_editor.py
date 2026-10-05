import pytest

from coe_wizard.chunks import Chunk
from coe_wizard.ontology.editor import ChecklistInput, CitationInput, EditError, Editor
from coe_wizard.ontology.store import EMPTY_HASH, OntologyStore, StaleHashError
from coe_wizard.state import ProgressDB

from .conftest import cite, item, make_onto

CHUNKS = {
    "c1": Chunk("c1", "d", "p", "Doc", "Page", "https://u/c1", "chunk one text"),
    "c2": Chunk("c2", "d", "p", "Doc", "Page", "https://u/c2", "chunk two text"),
}


@pytest.fixture
def editor(store: OntologyStore, db: ProgressDB) -> Editor:
    return Editor(store, db, lambda: CHUNKS)


def seed(store: OntologyStore, nodes: list[str], edges: list[tuple[str, str]]) -> str:
    return store.update(EMPTY_HASH, lambda _o: make_onto(nodes, edges))[1]


def test_rename_locks_title(editor: Editor, store: OntologyStore) -> None:
    h = seed(store, ["a"], [])
    editor.rename_node(h, "a", "New")
    node = store.load()[0].nodes[0]
    assert node.title == "New" and node.locked_fields == ["title"]


def test_stale_hash_is_rejected(editor: Editor, store: OntologyStore) -> None:
    h = seed(store, ["a"], [])
    editor.rename_node(h, "a", "New")
    with pytest.raises(StaleHashError):
        editor.rename_node(h, "a", "Again")


def test_add_edge_rejects_cycles(editor: Editor, store: OntologyStore) -> None:
    h = seed(store, ["a", "b", "c"], [("a", "b"), ("b", "c")])
    with pytest.raises(EditError) as err:
        editor.add_edge(h, "c", "a")
    assert err.value.status == 409


def test_deleted_edge_is_remembered_as_rejected(editor: Editor, store: OntologyStore) -> None:
    h = seed(store, ["a", "b"], [("a", "b")])
    editor.delete_edge(h, "a", "b")
    onto = store.load()[0]
    assert onto.edges == [] and ("a", "b") in onto.rejected.edges


def test_delete_hides_progress_and_recreate_restores_it(
    editor: Editor, store: OntologyStore, db: ProgressDB
) -> None:
    h = seed(store, ["a", "b"], [("a", "b")])
    p = db.create_project("p")
    db.set_completed(p.id, "b", True)
    h = editor.delete_node(h, "b")
    assert "b" not in db.project_state(p.id)
    node_id, _ = editor.create_node(h, "B")
    assert node_id == "b"
    assert db.project_state(p.id)["b"].completed


def test_merge_migrates_progress_and_repoints_edges(
    editor: Editor, store: OntologyStore, db: ProgressDB
) -> None:
    h = store.update(EMPTY_HASH, lambda _o: make_onto(["a", "b", "c"], [("a", "b"), ("b", "c")]))[1]
    _, h = store.update(h, lambda o: setattr(o.nodes[1], "checklist", [item("x", "ix")]))
    p = db.create_project("p")
    db.set_completed(p.id, "b", True)
    db.set_checked(p.id, "b", "ix", True)

    editor.merge_nodes(h, "b", "c")

    onto = store.load()[0]
    assert {n.id for n in onto.nodes} == {"a", "c"}
    assert [e.key for e in onto.edges] == [("a", "c")]
    assert [i.id for i in onto.node("c").checklist] == ["ix"]  # type: ignore[union-attr]
    state = db.project_state(p.id)
    assert state["c"].completed and state["c"].checked == {"ix"}
    assert "b" not in state


def test_merge_that_creates_cycle_rolls_back_progress(
    editor: Editor, store: OntologyStore, db: ProgressDB
) -> None:
    # a -> x -> b ; merging a into b gives b -> x -> b
    h = seed(store, ["a", "x", "b"], [("a", "x"), ("x", "b")])
    p = db.create_project("p")
    db.set_completed(p.id, "a", True)
    with pytest.raises(EditError) as err:
        editor.merge_nodes(h, "a", "b")
    assert err.value.status == 409
    assert db.project_state(p.id)["a"].completed
    assert {n.id for n in store.load()[0].nodes} == {"a", "x", "b"}


def test_checklist_and_citation_edits_lock(editor: Editor, store: OntologyStore) -> None:
    h = seed(store, ["a"], [])
    h = editor.set_checklist(h, "a", [ChecklistInput(None, "Do it"), ChecklistInput(None, " ")])
    _, h = store.update(h, lambda o: setattr(o.nodes[0], "citations", [cite(1, "c1")]))
    editor.set_citations(h, "a", [CitationInput("c2", None), CitationInput("c1", 1)])
    node = store.load()[0].nodes[0]
    assert [i.text for i in node.checklist] == ["Do it"]
    assert [(c.chunk_id, c.n) for c in node.citations] == [("c2", 2), ("c1", 1)]
    assert set(node.locked_fields) == {"checklist", "citations"}


def test_unknown_chunk_is_rejected(editor: Editor, store: OntologyStore) -> None:
    h = seed(store, ["a"], [])
    with pytest.raises(EditError):
        editor.set_citations(h, "a", [CitationInput("nope")])


def test_resolve_proposals(editor: Editor, store: OntologyStore) -> None:
    h = seed(store, ["a", "b"], [("a", "b")])

    def propose(o):  # type: ignore[no-untyped-def]
        o.nodes[1].status = "proposed"
        o.edges[0].status = "proposed"

    _, h = store.update(h, propose)
    h = editor.resolve_edge(h, "a", "b", accept=False)
    editor.resolve_node(h, "b", accept=True)
    onto = store.load()[0]
    assert onto.edges == [] and onto.node("b").status == "accepted"  # type: ignore[union-attr]
