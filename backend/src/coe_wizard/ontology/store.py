"""YAML persistence for the ontology.

Writes are atomic (temp file + rename), backed up first, guarded by a content hash for optimistic
concurrency, and serialized across processes with an fcntl lock so the builder CLI and the
running server can't interleave.
"""

import contextlib
import fcntl
import hashlib
import io
import os
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .model import Ontology

MAX_BACKUPS = 100
EMPTY_HASH = "empty"


class StaleHashError(Exception):
    def __init__(self, current: str) -> None:
        super().__init__("Ontology changed since it was last read")
        self.current = current


def _yaml() -> YAML:
    y = YAML()  # round-trip mode keeps comments and key order
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 100
    return y


def _merge_into(old: Any, new: Any) -> Any:
    """Copy `new` (plain data) onto `old` (ruamel round-trip data), reusing old containers so
    hand-written comments and key order survive. Lists of mappings are matched by `id`."""
    if isinstance(old, CommentedMap) and isinstance(new, dict):
        for key in [k for k in old if k not in new]:
            del old[key]
        for key, value in new.items():
            old[key] = _merge_into(old[key], value) if key in old else value
        return old
    if isinstance(old, CommentedSeq) and isinstance(new, list):

        def ident(x: Any) -> Any:
            return x.get("id") if isinstance(x, dict) else None

        by_id = {ident(x): x for x in old if ident(x) is not None}
        # Sequence comments are keyed by index; re-key them by item id so they follow the item.
        comments = {
            ident(x): old.ca.items[i]
            for i, x in enumerate(old)
            if ident(x) is not None and i in old.ca.items
        }
        merged = [_merge_into(by_id[ident(x)], x) if ident(x) in by_id else x for x in new]
        old.clear()
        old.ca.items.clear()
        old.extend(merged)
        for i, x in enumerate(merged):
            if ident(x) in comments:
                old.ca.items[i] = comments[ident(x)]
        return old
    return new


class OntologyStore:
    def __init__(self, path: Path, backups_dir: Path) -> None:
        self.path = path
        self.backups_dir = backups_dir
        self._lock = threading.RLock()
        self._cache: tuple[str, Ontology] | None = None

    # --- Reading ---------------------------------------------------------------------------

    def _raw(self) -> bytes | None:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    @staticmethod
    def _hash(raw: bytes | None) -> str:
        return EMPTY_HASH if raw is None else hashlib.sha256(raw).hexdigest()[:16]

    def load(self) -> tuple[Ontology, str]:
        """Return the ontology and its content hash. Raises ValueError if the file is invalid."""
        with self._lock:
            raw = self._raw()
            digest = self._hash(raw)
            if self._cache and self._cache[0] == digest:
                return self._cache[1].model_copy(deep=True), digest
            if raw is None:
                onto = Ontology()
            else:
                data = _yaml().load(raw) or {}
                onto = Ontology.model_validate(data)
            self._cache = (digest, onto)
            return onto.model_copy(deep=True), digest

    def exists(self) -> bool:
        return self.path.exists()

    # --- Writing ---------------------------------------------------------------------------

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(".lock")
        with self._lock, lock_path.open("w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def update(
        self, expected_hash: str | None, mutate: Callable[[Ontology], Ontology | None]
    ) -> tuple[Ontology, str]:
        """Apply `mutate` to the current ontology and persist it.

        `expected_hash=None` skips the staleness check (used by the builder, which holds the file
        lock for the whole read-merge-write). `mutate` may modify in place or return a new
        ontology; the result is re-validated before anything is written.
        """
        with self._file_lock():
            raw = self._raw()
            current_hash = self._hash(raw)
            if expected_hash is not None and expected_hash != current_hash:
                raise StaleHashError(current_hash)
            onto, _ = self.load()
            result = mutate(onto) or onto
            result = Ontology.model_validate(result.model_dump(by_alias=True))
            new_hash = self._write(raw, result)
            return result, new_hash

    def _write(self, old_raw: bytes | None, onto: Ontology) -> str:
        y = _yaml()
        new_data = onto.model_dump(by_alias=True, mode="json")
        doc: Any = new_data
        if old_raw is not None:
            self._backup(old_raw)
            old_doc = y.load(old_raw)
            if isinstance(old_doc, CommentedMap):
                doc = _merge_into(old_doc, new_data)
        buf = io.StringIO()
        y.dump(doc, buf)
        payload = buf.getvalue().encode()
        tmp = self.path.with_suffix(".yaml.tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, self.path)
        digest = self._hash(payload)
        self._cache = (digest, onto.model_copy(deep=True))
        return digest

    def _backup(self, raw: bytes) -> None:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        (self.backups_dir / f"ontology-{stamp}.yaml").write_bytes(raw)
        backups = sorted(self.backups_dir.glob("ontology-*.yaml"))
        for old in backups[:-MAX_BACKUPS]:
            old.unlink(missing_ok=True)
