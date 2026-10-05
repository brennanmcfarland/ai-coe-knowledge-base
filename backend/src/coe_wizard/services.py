"""Long-lived objects shared by the API routes."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from .auth import AuthManager, CredentialStore
from .chunks import Chunk, read_chunks
from .clickup import ClickUpClient
from .config import Settings
from .ontology.editor import Editor
from .ontology.store import OntologyStore
from .scraper import load_manifest, scrape_space
from .state import ProgressDB

SyncState = Literal["idle", "running", "succeeded", "failed"]


@dataclass
class SyncJob:
    state: SyncState = "idle"
    messages: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    _task: asyncio.Task[None] | None = None

    def start(self, run: Callable[[Callable[[str], None]], Awaitable[None]]) -> bool:
        if self.state == "running":
            return False
        self.state, self.messages, self.error = "running", [], None
        self.started_at, self.finished_at = time.time(), None
        self._task = asyncio.create_task(self._run(run))
        return True

    async def _run(self, run: Callable[[Callable[[str], None]], Awaitable[None]]) -> None:
        try:
            await run(self.messages.append)
            self.state = "succeeded"
        except Exception as exc:  # surfaced to the UI and scrape.sh
            self.error = str(exc)
            self.messages.append(f"Error: {exc}")
            self.state = "failed"
        finally:
            self.finished_at = time.time()


class ChunkCache:
    def __init__(self, path_fn: Callable[[], object], loader: Callable[[], dict[str, Chunk]]):
        self._stat = path_fn
        self._loader = loader
        self._key: object = None
        self._chunks: dict[str, Chunk] = {}

    def get(self) -> dict[str, Chunk]:
        key = self._stat()
        if key != self._key:
            self._chunks, self._key = self._loader(), key
        return self._chunks


@dataclass
class Services:
    settings: Settings
    auth: AuthManager
    store: OntologyStore
    db: ProgressDB
    editor: Editor
    chunks: ChunkCache
    sync: SyncJob

    @classmethod
    def create(cls, settings: Settings, credentials: CredentialStore) -> Services:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        store = OntologyStore(settings.ontology_path, settings.backups_dir)
        db = ProgressDB(settings.db_path)

        def chunk_stat() -> object:
            p = settings.chunks_path
            return p.stat().st_mtime_ns if p.exists() else None

        chunks = ChunkCache(chunk_stat, lambda: read_chunks(settings.chunks_path))
        return cls(
            settings=settings,
            auth=AuthManager(credentials, settings.redirect_uri),
            store=store,
            db=db,
            editor=Editor(store, db, chunks.get),
            chunks=chunks,
            sync=SyncJob(),
        )

    async def run_scrape(self, log: Callable[[str], None]) -> None:
        token = self.auth.token
        if token is None:
            raise RuntimeError("Not logged in to ClickUp")
        client = ClickUpClient(token, rpm=self.settings.rpm)
        try:
            await scrape_space(
                client,
                self.settings.workspace_id,
                self.settings.space_id,
                self.settings.corpus_dir,
                log,
            )
        finally:
            await client.aclose()

    def last_sync(self) -> float | None:
        return load_manifest(self.settings.manifest_path).get("last_sync")
