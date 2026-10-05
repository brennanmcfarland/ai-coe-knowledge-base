"""Per-project wizard progress in a local SQLite file."""

import contextlib
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS: list[str] = [
    """
    CREATE TABLE projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE node_progress (
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        node_id TEXT NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0,
        hidden INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (project_id, node_id)
    );
    CREATE TABLE checklist_progress (
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        node_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        checked INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (project_id, node_id, item_id)
    );
    """,
]


@dataclass(frozen=True)
class Project:
    id: int
    name: str
    created_at: float


@dataclass(frozen=True)
class NodeState:
    completed: bool
    checked: frozenset[str]


class ProgressDB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        with self.transaction() as c:
            c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = c.execute("SELECT version FROM schema_version").fetchone()
            version = row["version"] if row else 0
            for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
                for stmt in filter(str.strip, script.split(";")):
                    c.execute(stmt)
                c.execute("DELETE FROM schema_version")
                c.execute("INSERT INTO schema_version (version) VALUES (?)", (i,))

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialized transaction; rolls back if the body raises (including non-DB failures,
        which is how ontology-merge keeps YAML and SQLite consistent)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    # --- Projects --------------------------------------------------------------------------

    def list_projects(self) -> list[Project]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
        return [Project(r["id"], r["name"], r["created_at"]) for r in rows]

    def get_project(self, project_id: int) -> Project | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return Project(r["id"], r["name"], r["created_at"]) if r else None

    def create_project(self, name: str) -> Project:
        now = time.time()
        with self.transaction() as c:
            cur = c.execute("INSERT INTO projects (name, created_at) VALUES (?, ?)", (name, now))
            assert cur.lastrowid is not None
            return Project(cur.lastrowid, name, now)

    def rename_project(self, project_id: int, name: str) -> bool:
        with self.transaction() as c:
            cur = c.execute("UPDATE projects SET name = ? WHERE id = ?", (name, project_id))
            return cur.rowcount > 0

    def delete_project(self, project_id: int) -> bool:
        with self.transaction() as c:
            cur = c.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cur.rowcount > 0

    # --- Progress --------------------------------------------------------------------------

    def project_state(self, project_id: int) -> dict[str, NodeState]:
        with self._lock:
            nodes = self._conn.execute(
                "SELECT node_id, completed FROM node_progress WHERE project_id = ? AND hidden = 0",
                (project_id,),
            ).fetchall()
            items = self._conn.execute(
                "SELECT node_id, item_id FROM checklist_progress"
                " WHERE project_id = ? AND checked = 1",
                (project_id,),
            ).fetchall()
        checked: dict[str, set[str]] = {}
        for r in items:
            checked.setdefault(r["node_id"], set()).add(r["item_id"])
        out = {r["node_id"]: NodeState(bool(r["completed"]), frozenset()) for r in nodes}
        for node_id, ids in checked.items():
            prev = out.get(node_id, NodeState(False, frozenset()))
            out[node_id] = NodeState(prev.completed, frozenset(ids))
        return out

    def set_completed(self, project_id: int, node_id: str, completed: bool) -> None:
        with self.transaction() as c:
            c.execute(
                "INSERT INTO node_progress (project_id, node_id, completed) VALUES (?, ?, ?)"
                " ON CONFLICT (project_id, node_id) DO UPDATE SET completed = excluded.completed",
                (project_id, node_id, int(completed)),
            )

    def set_checked(self, project_id: int, node_id: str, item_id: str, checked: bool) -> None:
        with self.transaction() as c:
            c.execute(
                "INSERT INTO checklist_progress (project_id, node_id, item_id, checked)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (project_id, node_id, item_id)"
                " DO UPDATE SET checked = excluded.checked",
                (project_id, node_id, item_id, int(checked)),
            )

    # --- Ontology edits (called inside a transaction alongside the YAML write) -------------

    @staticmethod
    def set_hidden(c: sqlite3.Connection, node_id: str, hidden: bool) -> None:
        c.execute("UPDATE node_progress SET hidden = ? WHERE node_id = ?", (int(hidden), node_id))

    @staticmethod
    def migrate_node(c: sqlite3.Connection, source: str, target: str) -> None:
        """Move progress from `source` onto `target`: completion OR'd, checklist ticks unioned."""
        c.execute(
            "INSERT INTO node_progress (project_id, node_id, completed, hidden)"
            " SELECT project_id, ?, completed, 0 FROM node_progress WHERE node_id = ?"
            " ON CONFLICT (project_id, node_id) DO UPDATE SET"
            " completed = MAX(completed, excluded.completed)",
            (target, source),
        )
        c.execute(
            "INSERT INTO checklist_progress (project_id, node_id, item_id, checked)"
            " SELECT project_id, ?, item_id, checked FROM checklist_progress WHERE node_id = ?"
            " ON CONFLICT (project_id, node_id, item_id) DO UPDATE SET"
            " checked = MAX(checked, excluded.checked)",
            (target, source),
        )
        c.execute("DELETE FROM node_progress WHERE node_id = ?", (source,))
        c.execute("DELETE FROM checklist_progress WHERE node_id = ?", (source,))
