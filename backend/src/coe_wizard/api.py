"""HTTP API. All routes live under /api."""

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .auth import AuthError
from .ontology.editor import ChecklistInput, CitationInput, EditError
from .ontology.model import Ontology
from .ontology.store import StaleHashError
from .progress_view import GraphView, build_view
from .services import Services

router = APIRouter(prefix="/api")


def services(request: Request) -> Services:
    return request.app.state.services


Svc = Annotated[Services, Depends(services)]
IfMatch = Annotated[str, Header(alias="If-Match", description="Ontology hash last seen")]


def load_ontology(svc: Services) -> tuple[Ontology, str]:
    try:
        return svc.store.load()
    except ValueError as exc:
        raise HTTPException(503, {"code": "invalid_ontology", "message": str(exc)}) from exc


# --- Auth ------------------------------------------------------------------------------------


class SetupBody(BaseModel):
    client_id: str
    client_secret: str


@router.get("/auth/status")
def auth_status(svc: Svc) -> dict[str, Any]:
    user = svc.auth.user or {}
    return {
        "configured": svc.auth.configured,
        "logged_in": svc.auth.logged_in,
        "user": {"username": user.get("username"), "email": user.get("email")}
        if svc.auth.logged_in
        else None,
        "redirect_uri": svc.settings.redirect_uri,
    }


@router.post("/auth/setup")
def auth_setup(body: SetupBody, svc: Svc) -> dict[str, bool]:
    try:
        svc.auth.configure(body.client_id, body.client_secret)
    except AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"configured": True}


@router.get("/auth/login")
def auth_login(svc: Svc) -> RedirectResponse:
    try:
        return RedirectResponse(svc.auth.authorize_url(), status_code=302)
    except AuthError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/auth/callback")
async def auth_callback(
    svc: Svc, code: str | None = None, state: str | None = None
) -> RedirectResponse:
    target = f"{svc.settings.origin}/login"
    if not code:
        return RedirectResponse(f"{target}?error=missing_code", status_code=302)
    try:
        await svc.auth.complete(code, state)
    except AuthError as exc:
        return RedirectResponse(f"{target}?error={quote(str(exc))}", status_code=302)
    return RedirectResponse(f"{target}?result=ok", status_code=302)


@router.post("/auth/logout")
def auth_logout(svc: Svc) -> dict[str, bool]:
    svc.auth.logout()
    return {"logged_in": False}


# --- Sync ------------------------------------------------------------------------------------


@router.post("/sync", status_code=202)
def start_sync(svc: Svc) -> dict[str, str]:
    if not svc.auth.logged_in:
        raise HTTPException(401, "Log in to ClickUp first")
    if not svc.sync.start(svc.run_scrape):
        raise HTTPException(409, "A sync is already running")
    return {"state": svc.sync.state}


@router.get("/sync")
def sync_status(svc: Svc, since: int = 0) -> dict[str, Any]:
    job = svc.sync
    return {
        "state": job.state,
        "messages": job.messages[since:],
        "next": len(job.messages),
        "error": job.error,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "last_sync": svc.last_sync(),
    }


# --- Projects & progress ---------------------------------------------------------------------


class ProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def project_json(p: Any) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "created_at": p.created_at}


def require_project(svc: Services, project_id: int) -> None:
    if svc.db.get_project(project_id) is None:
        raise HTTPException(404, "Unknown project")


@router.get("/projects")
def list_projects(svc: Svc) -> list[dict[str, Any]]:
    return [project_json(p) for p in svc.db.list_projects()]


@router.post("/projects", status_code=201)
def create_project(body: ProjectBody, svc: Svc) -> dict[str, Any]:
    return project_json(svc.db.create_project(body.name.strip()))


@router.get("/projects/{project_id}")
def get_project(project_id: int, svc: Svc) -> dict[str, Any]:
    p = svc.db.get_project(project_id)
    if p is None:
        raise HTTPException(404, "Unknown project")
    return project_json(p)


@router.patch("/projects/{project_id}")
def rename_project(project_id: int, body: ProjectBody, svc: Svc) -> dict[str, Any]:
    if not svc.db.rename_project(project_id, body.name.strip()):
        raise HTTPException(404, "Unknown project")
    return get_project(project_id, svc)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, svc: Svc) -> None:
    if not svc.db.delete_project(project_id):
        raise HTTPException(404, "Unknown project")


@router.get("/projects/{project_id}/graph")
def project_graph(project_id: int, svc: Svc, edit: bool = False) -> GraphView:
    require_project(svc, project_id)
    onto, digest = load_ontology(svc)
    live = set(svc.chunks.get()) if svc.settings.chunks_path.exists() else None
    return build_view(onto, svc.db.project_state(project_id), digest, edit, live)


class CompleteBody(BaseModel):
    completed: bool


class CheckBody(BaseModel):
    checked: bool


@router.put("/projects/{project_id}/nodes/{node_id}/complete", status_code=204)
def set_complete(project_id: int, node_id: str, body: CompleteBody, svc: Svc) -> None:
    require_project(svc, project_id)
    svc.db.set_completed(project_id, node_id, body.completed)


@router.put("/projects/{project_id}/nodes/{node_id}/checklist/{item_id}", status_code=204)
def set_checked(project_id: int, node_id: str, item_id: str, body: CheckBody, svc: Svc) -> None:
    require_project(svc, project_id)
    svc.db.set_checked(project_id, node_id, item_id, body.checked)


# --- Ontology editing ------------------------------------------------------------------------


@router.get("/ontology/status")
def ontology_status(svc: Svc) -> dict[str, Any]:
    try:
        onto, digest = svc.store.load()
    except ValueError as exc:
        return {"exists": svc.store.exists(), "valid": False, "error": str(exc)}
    return {
        "exists": svc.store.exists(),
        "valid": True,
        "hash": digest,
        "nodes": len(onto.nodes),
        "chunks": len(svc.chunks.get()),
    }


@router.get("/chunks")
def search_chunks(svc: Svc, q: str = "", limit: int = 20) -> list[dict[str, str]]:
    needle = q.lower().strip()
    out: list[dict[str, str]] = []
    for c in svc.chunks.get().values():
        haystack = f"{c.doc_title} {c.page_title} {c.text}".lower()
        if needle and needle not in haystack:
            continue
        out.append(
            {"id": c.id, "doc_title": f"{c.doc_title} / {c.page_title}", "text": c.text[:300]}
        )
        if len(out) >= limit:
            break
    return out


def edit(fn: Any, *args: Any) -> dict[str, Any]:
    try:
        result = fn(*args)
    except StaleHashError as exc:
        raise HTTPException(
            409, {"code": "stale", "message": str(exc), "hash": exc.current}
        ) from exc
    except EditError as exc:
        raise HTTPException(exc.status, {"code": "edit", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid", "message": str(exc)}) from exc
    if isinstance(result, tuple):
        node_id, digest = result
        return {"id": node_id, "hash": digest}
    return {"hash": result}


class TitleBody(BaseModel):
    title: str


class MergeBody(BaseModel):
    into: str


class ChecklistBody(BaseModel):
    class Item(BaseModel):
        id: str | None = None
        text: str

    items: list[Item]


class CitationsBody(BaseModel):
    class Item(BaseModel):
        chunk_id: str
        n: int | None = None

    items: list[Item]


class ResolveBody(BaseModel):
    accept: bool


class EdgeBody(BaseModel):
    source: str
    target: str


@router.post("/ontology/nodes", status_code=201)
def create_node(body: TitleBody, svc: Svc, if_match: IfMatch) -> dict[str, Any]:
    return edit(svc.editor.create_node, if_match, body.title)


@router.patch("/ontology/nodes/{node_id}")
def rename_node(node_id: str, body: TitleBody, svc: Svc, if_match: IfMatch) -> dict[str, Any]:
    return edit(svc.editor.rename_node, if_match, node_id, body.title)


@router.delete("/ontology/nodes/{node_id}")
def delete_node(node_id: str, svc: Svc, if_match: IfMatch) -> dict[str, Any]:
    return edit(svc.editor.delete_node, if_match, node_id)


@router.post("/ontology/nodes/{node_id}/merge")
def merge_node(node_id: str, body: MergeBody, svc: Svc, if_match: IfMatch) -> dict[str, Any]:
    return edit(svc.editor.merge_nodes, if_match, node_id, body.into)


@router.put("/ontology/nodes/{node_id}/checklist")
def put_checklist(node_id: str, body: ChecklistBody, svc: Svc, if_match: IfMatch) -> dict[str, Any]:
    items = [ChecklistInput(i.id, i.text) for i in body.items]
    return edit(svc.editor.set_checklist, if_match, node_id, items)


@router.put("/ontology/nodes/{node_id}/citations")
def put_citations(node_id: str, body: CitationsBody, svc: Svc, if_match: IfMatch) -> dict[str, Any]:
    items = [CitationInput(i.chunk_id, i.n) for i in body.items]
    return edit(svc.editor.set_citations, if_match, node_id, items)


@router.post("/ontology/nodes/{node_id}/resolve")
def resolve_node(node_id: str, body: ResolveBody, svc: Svc, if_match: IfMatch) -> dict[str, Any]:
    return edit(svc.editor.resolve_node, if_match, node_id, body.accept)


@router.post("/ontology/edges", status_code=201)
def add_edge(body: EdgeBody, svc: Svc, if_match: IfMatch) -> dict[str, Any]:
    return edit(svc.editor.add_edge, if_match, body.source, body.target)


@router.delete("/ontology/edges/{source}/{target}")
def delete_edge(source: str, target: str, svc: Svc, if_match: IfMatch) -> dict[str, Any]:
    return edit(svc.editor.delete_edge, if_match, source, target)


@router.post("/ontology/edges/{source}/{target}/resolve")
def resolve_edge(
    source: str, target: str, body: ResolveBody, svc: Svc, if_match: IfMatch
) -> dict[str, Any]:
    return edit(svc.editor.resolve_edge, if_match, source, target, body.accept)
