from collections.abc import Iterator
from pathlib import Path

import pytest

from coe_wizard.ontology.model import ChecklistItem, Citation, Edge, Node, Ontology
from coe_wizard.ontology.store import OntologyStore
from coe_wizard.state import ProgressDB


def make_node(node_id: str, **kw: object) -> Node:
    return Node.model_validate({"id": node_id, "title": node_id.title(), **kw})


def make_onto(nodes: list[str], edges: list[tuple[str, str]], **kw: object) -> Ontology:
    return Ontology(
        nodes=[make_node(n) for n in nodes],
        edges=[Edge.of(s, t) for s, t in edges],
        **kw,  # type: ignore[arg-type]
    )


def item(text: str, item_id: str | None = None) -> ChecklistItem:
    return ChecklistItem(id=item_id or text.lower().replace(" ", "-"), text=text)


def cite(n: int, chunk_id: str) -> Citation:
    return Citation(n=n, chunk_id=chunk_id, excerpt=f"excerpt {chunk_id}", url="https://x")


@pytest.fixture
def store(tmp_path: Path) -> OntologyStore:
    return OntologyStore(tmp_path / "ontology.yaml", tmp_path / "backups")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[ProgressDB]:
    d = ProgressDB(tmp_path / "progress.sqlite3")
    yield d
    d.close()
