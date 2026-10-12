"""Long-lived objects shared by the API routes."""

from collections.abc import Callable
from dataclasses import dataclass

from .auth import AuthManager, CredentialStore
from .chunks import Chunk, read_chunks
from .clickup import ClickUpClient
from .config import Settings
from .interview.definition import InterviewDef, ensure_seeded
from .interview.overlay import OverlayEditor, OverlayRepo
from .interview.service import InterviewService
from .jobs import SyncJob
from .llm_server import ExternalProvider, LLMProvider, ManagedProvider, ServerConfig
from .ontology.editor import Editor
from .ontology.store import OntologyStore, YamlStore
from .scraper import load_manifest, scrape_space
from .state import ProgressDB


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


def make_provider(settings: Settings) -> LLMProvider:
    if settings.fake_llm:
        from .fake_llm import FakeProvider

        return FakeProvider()
    if settings.llama_url:
        return ExternalProvider(settings.llama_url)
    return ManagedProvider(ServerConfig.from_env(settings.cache_dir, settings.llm_idle_timeout))


@dataclass
class Services:
    settings: Settings
    auth: AuthManager
    store: OntologyStore
    db: ProgressDB
    editor: Editor
    chunks: ChunkCache
    sync: SyncJob
    interview_def: YamlStore[InterviewDef]
    overlays: OverlayRepo
    overlay_editor: OverlayEditor
    interviews: InterviewService
    llm: LLMProvider

    @classmethod
    def create(cls, settings: Settings, credentials: CredentialStore) -> Services:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.projects_dir.mkdir(parents=True, exist_ok=True)
        settings.interviews_dir.mkdir(parents=True, exist_ok=True)
        ensure_seeded(settings.interview_path)
        store = OntologyStore(settings.ontology_path, settings.backups_dir)
        interview_def = YamlStore(
            settings.interview_path, settings.backups_dir, InterviewDef, "interview"
        )
        db = ProgressDB(settings.db_path)

        def chunk_stat() -> object:
            p = settings.chunks_path
            return p.stat().st_mtime_ns if p.exists() else None

        chunks = ChunkCache(chunk_stat, lambda: read_chunks(settings.chunks_path))
        overlays = OverlayRepo(settings.projects_dir, settings.backups_dir)
        llm = make_provider(settings)
        return cls(
            settings=settings,
            auth=AuthManager(credentials, settings.redirect_uri),
            store=store,
            db=db,
            editor=Editor(store, db, chunks.get, overlays),
            chunks=chunks,
            sync=SyncJob(),
            interview_def=interview_def,
            overlays=overlays,
            overlay_editor=OverlayEditor(
                overlays, store, lambda: interview_def.load()[0], db, chunks.get
            ),
            interviews=InterviewService(
                interview_def, store, db, overlays, settings.interviews_dir, llm, chunks.get
            ),
            llm=llm,
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
