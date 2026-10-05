"""Curator edits. Every edit locks what it touches so rebuilds won't overwrite it, and records
removals in `rejected` so rebuilds won't re-propose them."""

from collections.abc import Callable
from dataclasses import dataclass

from ..chunks import Chunk
from ..state import ProgressDB
from .model import (
    LOCKABLE_FIELDS,
    ChecklistItem,
    Citation,
    Edge,
    Node,
    Ontology,
    checklist_item_id,
    slugify,
    would_create_cycle,
)
from .pipeline import citation_for
from .store import OntologyStore


class EditError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _node(onto: Ontology, node_id: str) -> Node:
    node = onto.node(node_id)
    if node is None:
        raise EditError(404, f"Unknown node {node_id}")
    return node


def _reject_node(onto: Ontology, node_id: str) -> None:
    onto.nodes = [n for n in onto.nodes if n.id != node_id]
    onto.edges = [e for e in onto.edges if node_id not in e.key]
    if node_id not in onto.rejected.nodes:
        onto.rejected.nodes.append(node_id)


def _reject_edge(onto: Ontology, key: tuple[str, str]) -> None:
    onto.edges = [e for e in onto.edges if e.key != key]
    if key not in onto.rejected.edges:
        onto.rejected.edges.append(key)


@dataclass(frozen=True)
class ChecklistInput:
    id: str | None
    text: str


@dataclass(frozen=True)
class CitationInput:
    chunk_id: str
    n: int | None = None


class Editor:
    def __init__(
        self, store: OntologyStore, db: ProgressDB, chunks: Callable[[], dict[str, Chunk]]
    ) -> None:
        self.store = store
        self.db = db
        self.chunks = chunks

    def _apply(self, expected_hash: str, fn: Callable[[Ontology], None]) -> str:
        _, new_hash = self.store.update(expected_hash, lambda o: fn(o) or o)
        return new_hash

    # --- Nodes -----------------------------------------------------------------------------

    def create_node(self, expected_hash: str, title: str) -> tuple[str, str]:
        title = title.strip()
        if not title:
            raise EditError(422, "Title is required")
        created: list[str] = []

        def fn(o: Ontology) -> None:
            base = slugify(title)
            # Re-creating a deleted node with the same id restores its hidden progress.
            node_id = (
                base if base in o.rejected.nodes and o.node(base) is None else o.unique_id(base)
            )
            if node_id in o.rejected.nodes:
                o.rejected.nodes.remove(node_id)
            o.nodes.append(Node(id=node_id, title=title, locked_fields=list(LOCKABLE_FIELDS)))
            created.append(node_id)

        with self.db.transaction() as c:
            new_hash = self._apply(expected_hash, fn)
            ProgressDB.set_hidden(c, created[0], False)
        return created[0], new_hash

    def rename_node(self, expected_hash: str, node_id: str, title: str) -> str:
        if not title.strip():
            raise EditError(422, "Title is required")

        def fn(o: Ontology) -> None:
            n = _node(o, node_id)
            n.title = title.strip()
            n.lock("title")

        return self._apply(expected_hash, fn)

    def delete_node(self, expected_hash: str, node_id: str) -> str:
        def fn(o: Ontology) -> None:
            _node(o, node_id)
            _reject_node(o, node_id)

        with self.db.transaction() as c:
            ProgressDB.set_hidden(c, node_id, True)
            return self._apply(expected_hash, fn)

    def merge_nodes(self, expected_hash: str, source_id: str, target_id: str) -> str:
        if source_id == target_id:
            raise EditError(422, "Cannot merge a node into itself")

        def fn(o: Ontology) -> None:
            src, dst = _node(o, source_id), _node(o, target_id)
            have_items = {i.id for i in dst.checklist}
            dst.checklist += [i for i in src.checklist if i.id not in have_items]
            have_chunks = {c.chunk_id for c in dst.citations}
            next_n = max((c.n for c in dst.citations), default=0)
            for cit in src.citations:
                if cit.chunk_id not in have_chunks:
                    next_n += 1
                    dst.citations.append(cit.model_copy(update={"n": next_n}))
            dst.lock("checklist")
            dst.lock("citations")
            if dst.status == "proposed" and src.status == "accepted":
                dst.status = "accepted"

            repointed: dict[tuple[str, str], Edge] = {}
            for e in o.edges:
                s = target_id if e.source == source_id else e.source
                t = target_id if e.target == source_id else e.target
                if s == t:
                    continue
                new = e.model_copy(update={"source": s, "target": t})
                prev = repointed.get(new.key)
                if prev is None or (prev.status == "proposed" and new.status == "accepted"):
                    repointed[new.key] = new
            o.edges = list(repointed.values())
            o.nodes = [n for n in o.nodes if n.id != source_id]
            if source_id not in o.rejected.nodes:
                o.rejected.nodes.append(source_id)

        with self.db.transaction() as c:
            ProgressDB.migrate_node(c, source_id, target_id)
            try:
                return self._apply(expected_hash, fn)
            except ValueError as exc:  # re-validation found a cycle created by the merge
                raise EditError(409, f"Merge would create a cycle: {exc}") from exc

    # --- Checklist & citations -------------------------------------------------------------

    def set_checklist(self, expected_hash: str, node_id: str, items: list[ChecklistInput]) -> str:
        def fn(o: Ontology) -> None:
            n = _node(o, node_id)
            out: list[ChecklistItem] = []
            used: set[str] = set()
            for item in items:
                text = item.text.strip()
                if not text:
                    continue
                item_id = item.id or checklist_item_id(text)
                base, i = item_id, 2
                while item_id in used:
                    item_id, i = f"{base}-{i}", i + 1
                used.add(item_id)
                out.append(ChecklistItem(id=item_id, text=text))
            n.checklist = out
            n.lock("checklist")

        return self._apply(expected_hash, fn)

    def set_citations(self, expected_hash: str, node_id: str, items: list[CitationInput]) -> str:
        chunks = self.chunks()

        def fn(o: Ontology) -> None:
            n = _node(o, node_id)
            existing = {c.chunk_id: c for c in n.citations}
            next_n = max([c.n for c in n.citations] + [i.n or 0 for i in items], default=0)
            out: list[Citation] = []
            for item in items:
                if item.chunk_id in chunks:
                    if item.n is None:
                        next_n += 1
                    cit = citation_for(chunks[item.chunk_id], item.n or next_n)
                elif item.chunk_id in existing:  # retired chunk: keep what we had
                    cit = existing[item.chunk_id]
                    if item.n is not None:
                        cit = cit.model_copy(update={"n": item.n})
                else:
                    raise EditError(422, f"Unknown chunk {item.chunk_id}")
                out.append(cit)
            if len({c.n for c in out}) != len(out):
                raise EditError(422, "Citation markers must be unique")
            n.citations = out
            n.lock("citations")

        return self._apply(expected_hash, fn)

    # --- Edges -----------------------------------------------------------------------------

    def add_edge(self, expected_hash: str, source: str, target: str) -> str:
        def fn(o: Ontology) -> None:
            _node(o, source)
            _node(o, target)
            key = (source, target)
            existing = o.edge(source, target)
            if existing is not None:
                existing.status = "accepted"
                existing.locked = True
                return
            if would_create_cycle((e.key for e in o.edges), key):
                raise EditError(409, f"Edge {source} -> {target} would create a cycle")
            if key in o.rejected.edges:
                o.rejected.edges.remove(key)
            o.edges.append(Edge.of(source, target, locked=True))

        return self._apply(expected_hash, fn)

    def delete_edge(self, expected_hash: str, source: str, target: str) -> str:
        def fn(o: Ontology) -> None:
            if o.edge(source, target) is None:
                raise EditError(404, f"Unknown edge {source} -> {target}")
            _reject_edge(o, (source, target))

        return self._apply(expected_hash, fn)

    # --- Proposals -------------------------------------------------------------------------

    def resolve_node(self, expected_hash: str, node_id: str, accept: bool) -> str:
        def fn(o: Ontology) -> None:
            n = _node(o, node_id)
            if n.status != "proposed":
                raise EditError(409, f"Node {node_id} is not a proposal")
            if accept:
                n.status = "accepted"
            else:
                _reject_node(o, node_id)

        return self._apply(expected_hash, fn)

    def resolve_edge(self, expected_hash: str, source: str, target: str, accept: bool) -> str:
        def fn(o: Ontology) -> None:
            e = o.edge(source, target)
            if e is None or e.status != "proposed":
                raise EditError(404, f"No proposed edge {source} -> {target}")
            if accept:
                e.status = "accepted"
            else:
                _reject_edge(o, (source, target))

        return self._apply(expected_hash, fn)
