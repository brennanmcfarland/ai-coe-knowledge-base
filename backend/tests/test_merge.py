from coe_wizard.ontology.merge import merge_ontology, remap_markers
from coe_wizard.ontology.model import Ontology, Rejected

from .conftest import cite, item, make_node, make_onto


def test_first_build_is_accepted_wholesale() -> None:
    built = make_onto(["a", "b"], [("a", "b")])
    built.nodes[0].status = "proposed"
    out = merge_ontology(Ontology(), built)
    assert [n.status for n in out.nodes] == ["accepted", "accepted"]
    assert [e.key for e in out.edges] == [("a", "b")]


def test_locked_fields_survive_and_unlocked_refresh() -> None:
    cur = make_onto(["a"], [])
    cur.nodes[0].title = "Curated title"
    cur.nodes[0].checklist = [item("old")]
    cur.nodes[0].locked_fields = ["title"]

    fresh = make_onto(["a"], [])
    fresh.nodes[0].title = "LLM title"
    fresh.nodes[0].summary = "new summary"
    fresh.nodes[0].checklist = [item("new")]

    out = merge_ontology(cur, fresh)
    node = out.nodes[0]
    assert node.title == "Curated title"
    assert [i.text for i in node.checklist] == ["new"]
    assert node.summary == "new summary"


def test_new_items_arrive_as_proposals_and_rejected_stay_out() -> None:
    cur = make_onto(["a"], [], rejected=Rejected(nodes=["c"], edges=[("a", "b")]))
    fresh = make_onto(["a", "b", "c", "d"], [("a", "b"), ("d", "a")])
    out = merge_ontology(cur, fresh)
    statuses = {n.id: n.status for n in out.nodes}
    assert statuses == {"a": "accepted", "b": "proposed", "d": "proposed"}
    # a->b was rejected; d->a is new
    assert [(e.key, e.status) for e in out.edges] == [(("d", "a"), "proposed")]


def test_stale_proposals_drop_but_accepted_items_stay() -> None:
    cur = make_onto(["a", "b", "p"], [("a", "b")])
    cur.nodes[2].status = "proposed"
    out = merge_ontology(cur, make_onto(["a"], []))
    assert {n.id for n in out.nodes} == {"a", "b"}
    assert [e.key for e in out.edges] == [("a", "b")]


def test_proposed_edges_never_create_cycles() -> None:
    cur = make_onto(["a", "b", "c"], [("a", "b"), ("b", "c")])
    out = merge_ontology(cur, make_onto(["a", "b", "c"], [("c", "a")]))
    assert ("c", "a") not in [e.key for e in out.edges]


def test_locked_citations_keep_markers_pointing_at_same_chunks() -> None:
    cur = make_onto(["a"], [])
    cur.nodes[0].citations = [cite(7, "chunk-x")]
    cur.nodes[0].locked_fields = ["citations"]
    fresh = Ontology(
        nodes=[
            make_node(
                "a", summary="foo [1] bar [2]", citations=[cite(1, "chunk-y"), cite(2, "chunk-x")]
            )
        ]
    )
    out = merge_ontology(cur, fresh)
    assert out.nodes[0].summary == "foo  bar [7]"
    assert [c.n for c in out.nodes[0].citations] == [7]


def test_remap_drops_unknown_markers() -> None:
    assert remap_markers("x [3]", [], []) == "x "
