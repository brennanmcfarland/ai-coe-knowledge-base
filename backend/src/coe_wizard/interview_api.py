"""HTTP API for the local model, interviews, tailored projects and the interview definition."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .api import IfMatch, Svc, edit, require_project
from .interview.definition import InterviewDef
from .interview.overlay import Answer
from .interview.service import Draft, InterviewError, InterviewService
from .ontology.editor import ChecklistInput, CitationInput, EditError
from .ontology.store import StaleHashError

router = APIRouter(prefix="/api")


def _interview(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except InterviewError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# --- Local model ---------------------------------------------------------------------------


@router.get("/llm/status")
def llm_status(svc: Svc) -> dict[str, Any]:
    return svc.llm.status()


@router.post("/llm/download", status_code=202)
async def llm_download(svc: Svc) -> dict[str, Any]:  # async: the download runs on the loop
    svc.llm.start_download()
    return svc.llm.status()


@router.post("/llm/download/cancel")
async def llm_cancel(svc: Svc) -> dict[str, Any]:
    svc.llm.cancel_download()
    return svc.llm.status()


@router.post("/llm/warm", status_code=202)
async def llm_warm(svc: Svc) -> dict[str, Any]:
    svc.llm.warm()
    return svc.llm.status()


# --- Interviews ----------------------------------------------------------------------------


def draft_json(svc: InterviewService, draft: Draft) -> dict[str, Any]:
    job = svc.job(draft.id)
    return {**draft.model_dump(mode="json"), "job": job.view() if job else None}


class NameBody(BaseModel):
    name: str


class AnswerBody(BaseModel):
    value: Answer | None


@router.get("/interviews")
def list_interviews(svc: Svc) -> list[dict[str, Any]]:
    return [draft_json(svc.interviews, d) for d in svc.interviews.list()]


@router.post("/interviews", status_code=201)
def create_interview(body: NameBody, svc: Svc) -> dict[str, Any]:
    return draft_json(svc.interviews, _interview(svc.interviews.create, body.name))


@router.get("/interviews/{draft_id}")
def get_interview(draft_id: str, svc: Svc) -> dict[str, Any]:
    return draft_json(svc.interviews, _interview(svc.interviews.get, draft_id))


@router.patch("/interviews/{draft_id}")
def rename_interview(draft_id: str, body: NameBody, svc: Svc) -> dict[str, Any]:
    return draft_json(svc.interviews, _interview(svc.interviews.rename, draft_id, body.name))


@router.delete("/interviews/{draft_id}", status_code=204)
def delete_interview(draft_id: str, svc: Svc) -> None:
    _interview(svc.interviews.delete, draft_id)


@router.put("/interviews/{draft_id}/answers/{question_id}")
def put_answer(draft_id: str, question_id: str, body: AnswerBody, svc: Svc) -> dict[str, Any]:
    draft = _interview(svc.interviews.answer, draft_id, question_id, body.value)
    return draft_json(svc.interviews, draft)


@router.post("/interviews/{draft_id}/advance")
async def advance_interview(draft_id: str, svc: Svc) -> dict[str, Any]:  # async: starts jobs
    started = _interview(svc.interviews.advance, draft_id)
    out = {"job_started": started["job"]}
    try:
        out["draft"] = draft_json(svc.interviews, svc.interviews.get(draft_id))
    except InterviewError:
        out["draft"] = None
    return out


@router.get("/interviews/{draft_id}/job")
def interview_job(draft_id: str, svc: Svc, since: int = 0) -> dict[str, Any]:
    job = svc.interviews.job(draft_id)
    if job is None:
        raise HTTPException(404, "No job for this interview")
    return job.view(since)


@router.post("/projects/{project_id}/retailor", status_code=201)
def retailor(project_id: int, svc: Svc) -> dict[str, Any]:
    return draft_json(svc.interviews, _interview(svc.interviews.for_project, project_id))


# --- Tailored projects ---------------------------------------------------------------------


class ResolveBody(BaseModel):
    accept: bool


class TitleBody(BaseModel):
    title: str


class EdgeBody(BaseModel):
    source: str
    target: str


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


@router.post("/projects/{project_id}/proposals/{key:path}/resolve")
def resolve_proposal(
    project_id: int, key: str, body: ResolveBody, svc: Svc, if_match: IfMatch
) -> dict[str, Any]:
    require_project(svc, project_id)
    return edit(svc.overlay_editor.resolve, project_id, if_match, key, body.accept)


@router.post("/projects/{project_id}/nodes/{node_id}/promote")
def promote(project_id: int, node_id: str, svc: Svc, if_match: IfMatch) -> dict[str, Any]:
    require_project(svc, project_id)
    return edit(svc.overlay_editor.promote, project_id, if_match, node_id)


@router.patch("/projects/{project_id}/overlay/nodes/{node_id}")
def overlay_rename(
    project_id: int, node_id: str, body: TitleBody, svc: Svc, if_match: IfMatch
) -> dict[str, Any]:
    return edit(svc.overlay_editor.rename_node, project_id, if_match, node_id, body.title)


@router.delete("/projects/{project_id}/overlay/nodes/{node_id}")
def overlay_delete(project_id: int, node_id: str, svc: Svc, if_match: IfMatch) -> dict[str, Any]:
    return edit(svc.overlay_editor.delete_node, project_id, if_match, node_id)


@router.put("/projects/{project_id}/overlay/nodes/{node_id}/checklist")
def overlay_checklist(
    project_id: int, node_id: str, body: ChecklistBody, svc: Svc, if_match: IfMatch
) -> dict[str, Any]:
    items = [ChecklistInput(i.id, i.text) for i in body.items]
    return edit(svc.overlay_editor.set_checklist, project_id, if_match, node_id, items)


@router.put("/projects/{project_id}/overlay/nodes/{node_id}/citations")
def overlay_citations(
    project_id: int, node_id: str, body: CitationsBody, svc: Svc, if_match: IfMatch
) -> dict[str, Any]:
    items = [CitationInput(i.chunk_id, i.n) for i in body.items]
    return edit(svc.overlay_editor.set_citations, project_id, if_match, node_id, items)


@router.post("/projects/{project_id}/overlay/edges", status_code=201)
def overlay_add_edge(
    project_id: int, body: EdgeBody, svc: Svc, if_match: IfMatch
) -> dict[str, Any]:
    return edit(svc.overlay_editor.add_edge, project_id, if_match, body.source, body.target)


@router.delete("/projects/{project_id}/overlay/edges/{source}/{target}")
def overlay_delete_edge(
    project_id: int, source: str, target: str, svc: Svc, if_match: IfMatch
) -> dict[str, Any]:
    return edit(svc.overlay_editor.delete_edge, project_id, if_match, source, target)


# --- Interview definition (curation) -------------------------------------------------------


def definition_json(defn: InterviewDef, digest: str, include_proposed: bool) -> dict[str, Any]:
    data = defn.model_dump(mode="json")
    if not include_proposed:
        data["questions"] = [
            {**q, "options": [o for o in q["options"] if o["status"] == "accepted"]}
            for q in data["questions"]
            if q["status"] == "accepted"
        ]
    return {**data, "hash": digest, "proposals": defn.proposals()}


@router.get("/interview-def")
def get_definition(svc: Svc, edit: bool = False) -> dict[str, Any]:
    try:
        defn, digest = svc.interview_def.load()
    except ValueError as exc:
        raise HTTPException(503, {"code": "invalid_interview", "message": str(exc)}) from exc
    return definition_json(defn, digest, edit)


def _resolve_definition(svc: Svc, if_match: str, fn: Any) -> dict[str, Any]:
    try:
        _, digest = svc.interview_def.update(if_match, lambda d: fn(d) or d)
    except StaleHashError as exc:
        raise HTTPException(
            409, {"code": "stale", "message": str(exc), "hash": exc.current}
        ) from exc
    except EditError as exc:
        raise HTTPException(exc.status, {"code": "edit", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid", "message": str(exc)}) from exc
    return {"hash": digest}


@router.post("/interview-def/questions/{question_id}/resolve")
def resolve_question(
    question_id: str, body: ResolveBody, svc: Svc, if_match: IfMatch
) -> dict[str, Any]:
    def fn(d: InterviewDef) -> None:
        q = d.question(question_id)
        if q is None or q.status != "proposed":
            raise EditError(404, f"No proposed question {question_id}")
        if body.accept:
            q.status = "accepted"
            q.locked = True
        else:
            d.questions = [x for x in d.questions if x.id != question_id]
            d.rejected.questions.append(question_id)

    return _resolve_definition(svc, if_match, fn)


@router.post("/interview-def/questions/{question_id}/options/{value}/resolve")
def resolve_option(
    question_id: str, value: str, body: ResolveBody, svc: Svc, if_match: IfMatch
) -> dict[str, Any]:
    def fn(d: InterviewDef) -> None:
        q = d.question(question_id)
        o = q.option(value) if q else None
        if q is None or o is None or o.status != "proposed":
            raise EditError(404, f"No proposed option {value} on {question_id}")
        if body.accept:
            o.status = "accepted"
            o.locked = True
        else:
            q.options = [x for x in q.options if x.value != value]
            d.rejected.options.append((question_id, value))

    return _resolve_definition(svc, if_match, fn)
