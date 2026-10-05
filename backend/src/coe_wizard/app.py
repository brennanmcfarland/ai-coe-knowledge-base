"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .auth import CredentialStore, KeyringCredentialStore
from .config import Settings
from .services import Services


def create_app(settings: Settings, credentials: CredentialStore | None = None) -> FastAPI:
    svc = Services.create(settings, credentials or KeyringCredentialStore())

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await svc.auth.http.aclose()
        svc.db.close()

    app = FastAPI(title="CoE Wizard", lifespan=lifespan)
    app.state.services = svc
    app.include_router(router)

    if settings.static_dir is not None:
        _mount_spa(app, settings.static_dir)
    return app


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    if not index.exists():
        raise FileNotFoundError(f"{index} not found; build the frontend first")
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)
