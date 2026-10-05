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
    def ontology_path(self) -> Path:
        return self.data_dir / "ontology.yaml"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "progress.sqlite3"

    @property
    def redirect_uri(self) -> str:
        return f"{self.origin}/api/auth/callback"
