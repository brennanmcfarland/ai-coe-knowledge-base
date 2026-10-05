from coe_wizard.progress_view import build_view
from coe_wizard.state import NodeState

from .conftest import cite, make_onto


def states(view):  # type: ignore[no-untyped-def]
    return {n.id: n.state for n in view.nodes}


def done(*ids: str) -> dict[str, NodeState]:
    return {i: NodeState(True, frozenset()) for i in ids}


def test_frontier_advances_as_predecessors_complete() -> None:
    onto = make_onto(["a", "b", "c", "d"], [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
    assert states(build_view(onto, {}, "h", False, None)) == {
        "a": "frontier",
        "b": "locked",
        "c": "locked",
        "d": "locked",
    }
    assert states(build_view(onto, done("a", "b"), "h", False, None)) == {
        "a": "complete",
        "b": "complete",
        "c": "frontier",
        "d": "locked",
    }
    assert states(build_view(onto, done("a", "b", "c"), "h", False, None))["d"] == "frontier"


def test_proposals_hidden_outside_edit_mode_and_never_gate() -> None:
    onto = make_onto(["a", "b", "p"], [("a", "b"), ("p", "b")])
    onto.nodes[2].status = "proposed"
    onto.edges[1].status = "proposed"
    learner = build_view(onto, done("a"), "h", False, None)
    assert {n.id for n in learner.nodes} == {"a", "b"} and len(learner.edges) == 1
    assert states(learner)["b"] == "frontier"
    editing = build_view(onto, done("a"), "h", True, None)
    assert {n.id for n in editing.nodes} == {"a", "b", "p"} and editing.proposals == 2
    assert states(editing)["b"] == "frontier"


def test_checklist_ticks_and_broken_citations() -> None:
    onto = make_onto(["a"], [])
    onto.nodes[0].citations = [cite(1, "live"), cite(2, "gone")]
    view = build_view(onto, {"a": NodeState(False, frozenset())}, "h", True, {"live"})
    assert [c.broken for c in view.nodes[0].citations] == [False, True]
