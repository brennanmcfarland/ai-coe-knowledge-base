"""Runtime settings. Nothing here is secret; credentials live in the OS keyring (see auth.py)."""

from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Parsed from https://app.clickup.com/9017310967/v/o/s/90176814220
DEFAULT_WORKSPACE_ID = "9017310967"
DEFAULT_SPACE_ID = "90176814220"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = REPO_ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8765
    workspace_id: str = DEFAULT_WORKSPACE_ID
    space_id: str = DEFAULT_SPACE_ID
    rpm: int = 60
    static_dir: Path | None = field(default=None)
    # The origin the browser uses. Differs from host:port in dev, where Vite proxies /api.
    public_url: str | None = None
    # Local model: llama.cpp + GGUF live here (shared with build_ontology.sh).
    cache_dir: Path = REPO_ROOT / ".cache"
    # An already-running OpenAI-compatible server to use instead of the managed one.
    llama_url: str | None = None
    # Seconds the managed llama-server stays loaded after the last interview step.
    llm_idle_timeout: float = 600
    # Scripted model for tests (see fake_llm.py).
    fake_llm: bool = False

    @property
    def origin(self) -> str:
        return (self.public_url or f"http://{self.host}:{self.port}").rstrip("/")

    @property
    def corpus_dir(self) -> Path:
        return self.data_dir / "corpus"

    @property
    def manifest_path(self) -> Path:
        return self.corpus_dir / "manifest.json"

    @property
    def chunks_path(self) -> Path:
        return self.data_dir / "chunks.jsonl"

    @property
    def steps_path(self) -> Path:
        """Running step doc the ontology build edits item by item (inspect it mid-build)."""
        return self.data_dir / "ontology-steps.jsonl"

    @property
    def ontology_path(self) -> Path:
        return self.data_dir / "ontology.yaml"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def interview_path(self) -> Path:
        return self.data_dir / "interview.yaml"

    @property
    def projects_dir(self) -> Path:
        """Per-project tailoring overlays."""
        return self.data_dir / "projects"

    @property
    def interviews_dir(self) -> Path:
        """In-progress interview drafts."""
        return self.data_dir / "interviews"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "progress.sqlite3"

    @property
    def redirect_uri(self) -> str:
        return f"{self.origin}/api/auth/callback"
