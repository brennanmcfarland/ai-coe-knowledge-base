from pathlib import Path

import pytest

from coe_wizard.ontology.model import Ontology
from coe_wizard.ontology.store import EMPTY_HASH, OntologyStore, StaleHashError

from .conftest import make_onto


def test_missing_file_loads_empty(store: OntologyStore) -> None:
    onto, digest = store.load()
    assert onto.nodes == [] and digest == EMPTY_HASH


def test_update_rejects_stale_hash(store: OntologyStore) -> None:
    _, h1 = store.update(EMPTY_HASH, lambda _o: make_onto(["a"], []))
    store.update(h1, lambda o: o.nodes.append(make_onto(["b"], []).nodes[0]))
    with pytest.raises(StaleHashError) as err:
        store.update(h1, lambda o: None)
    assert err.value.current != h1


def test_invalid_mutation_writes_nothing(store: OntologyStore) -> None:
    _, h1 = store.update(EMPTY_HASH, lambda _o: make_onto(["a", "b"], [("a", "b")]))
    before = store.path.read_bytes()

    def add_cycle(o: Ontology) -> None:
        o.edges.append(make_onto(["a", "b"], [("b", "a")]).edges[0])

    with pytest.raises(ValueError, match="cycle"):
        store.update(h1, add_cycle)
    assert store.path.read_bytes() == before


def test_hand_written_comments_survive_editor_writes(store: OntologyStore) -> None:
    store.update(EMPTY_HASH, lambda _o: make_onto(["a", "b"], []))
    text = store.path.read_text().replace("- id: b", "# keep me\n  - id: b")
    store.path.write_text(text)
    _, digest = store.load()
    store.update(digest, lambda o: setattr(o.nodes[1], "title", "Renamed"))
    out = store.path.read_text()
    assert "# keep me" in out and "Renamed" in out


def test_writes_are_backed_up(store: OntologyStore, tmp_path: Path) -> None:
    _, h1 = store.update(EMPTY_HASH, lambda _o: make_onto(["a"], []))
    store.update(h1, lambda o: setattr(o.nodes[0], "title", "x"))
    assert len(list((tmp_path / "backups").glob("ontology-*.yaml"))) == 1


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("nodes:\n  - {id: a, title: A}\n  - {id: a, title: B}\n", "Duplicate"),
        ("nodes:\n  - {id: a, title: A}\nedges:\n  - {from: a, to: z}\n", "unknown node"),
        (
            "nodes:\n  - {id: a, title: A}\n  - {id: b, title: B}\n"
            "edges:\n  - {from: a, to: b}\n  - {from: b, to: a}\n",
            "cycle",
        ),
        ("nodes:\n  - {title: A}\n", "id"),
    ],
)
def test_invalid_yaml_reports_errors(store: OntologyStore, yaml_text: str, message: str) -> None:
    store.path.write_text(yaml_text)
    with pytest.raises(ValueError, match=message):
        store.load()
