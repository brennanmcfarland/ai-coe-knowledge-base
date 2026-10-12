"""Interview drafts and the jobs that move them along.

A draft (data/interviews/<id>.yaml) holds the answers until the interview completes; only then is
the project created, together with its overlay. For a re-tailor the draft is linked to an
existing project, and completing it writes the changes to that project as proposals instead.

Flow per section: answer the curated questions -> (LLM) follow-ups -> answer them -> next
section. After the last section, (LLM) tailoring creates or updates the project.
"""

import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..chunks import Chunk
from ..jobs import SyncJob
from ..llm import LLMBackend
from ..llm_server import LLMProvider
from ..ontology.store import OntologyStore, YamlStore
from ..state import ProgressDB
from .definition import InterviewDef
from .engine import generate_followups, retailor_diff, tailor, untailored
from .overlay import Answer, FollowUp, OverlayRepo

DRAFT_ID = re.compile(r"^[0-9a-f]{32}$")


class InterviewError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Draft(BaseModel):
    # Defaults only so YamlStore can construct an empty model before the first write.
    id: str = ""
    name: str = ""
    project_id: int | None = None  # set when re-tailoring an existing project
    created_at: float = Field(default_factory=time.time)
    section: int = 0
    stage: Literal["questions", "followups"] = "questions"
    answers: dict[str, Answer] = Field(default_factory=dict)
    followups: list[FollowUp] = Field(default_factory=list)


def _empty(value: Answer | None) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


class InterviewService:
    def __init__(
        self,
        definition: YamlStore[InterviewDef],
        ontology: OntologyStore,
        db: ProgressDB,
        overlays: OverlayRepo,
        drafts_dir: Path,
        llm: LLMProvider,
        chunks: Callable[[], dict[str, Chunk]],
    ) -> None:
        self.definition = definition
        self.ontology = ontology
        self.db = db
        self.overlays = overlays
        self.drafts_dir = drafts_dir
        self.llm = llm
        self.chunks = chunks
        self.jobs: dict[str, SyncJob] = {}

    # --- Drafts ----------------------------------------------------------------------------

    def defn(self) -> InterviewDef:
        try:
            return self.definition.load()[0]
        except ValueError as exc:
            raise InterviewError(503, f"interview.yaml is invalid: {exc}") from exc

    def _store(self, draft_id: str) -> YamlStore[Draft]:
        if not DRAFT_ID.match(draft_id):
            raise InterviewError(404, "Unknown interview")
        return YamlStore(self.drafts_dir / f"{draft_id}.yaml", None, Draft)

    def get(self, draft_id: str) -> Draft:
        store = self._store(draft_id)
        if not store.exists():
            raise InterviewError(404, "Unknown interview")
        return store.load()[0]

    def _save(self, draft: Draft) -> Draft:
        self._store(draft.id).update(None, lambda _d: draft)
        return draft

    def list(self) -> list[Draft]:
        out: list[Draft] = []
        for path in sorted(self.drafts_dir.glob("*.yaml")):
            try:
                out.append(self.get(path.stem))
            except InterviewError, ValueError:
                continue
        return sorted(out, key=lambda d: d.created_at)

    def create(self, name: str) -> Draft:
        if not name.strip():
            raise InterviewError(422, "Name is required")
        return self._save(Draft(id=uuid.uuid4().hex, name=name.strip()))

    def for_project(self, project_id: int) -> Draft:
        """The project's re-tailor draft, prefilled with its current answers."""
        project = self.db.get_project(project_id)
        if project is None:
            raise InterviewError(404, "Unknown project")
        existing = next((d for d in self.list() if d.project_id == project_id), None)
        if existing is not None:
            return existing
        answers: dict[str, Answer] = {}
        followups: list[FollowUp] = []
        if self.overlays.exists(project_id):
            overlay, _ = self.overlays.store(project_id).load()
            answers, followups = dict(overlay.answers), list(overlay.followups)
        return self._save(
            Draft(
                id=uuid.uuid4().hex,
                name=project.name,
                project_id=project_id,
                answers=answers,
                followups=followups,
            )
        )

    def rename(self, draft_id: str, name: str) -> Draft:
        if not name.strip():
            raise InterviewError(422, "Name is required")
        draft = self.get(draft_id)
        draft.name = name.strip()
        return self._save(draft)

    def delete(self, draft_id: str) -> None:
        job = self.jobs.get(draft_id)
        if job is not None and job.state == "running":
            raise InterviewError(409, "This interview is still working")
        self._remove(draft_id)
        self.jobs.pop(draft_id, None)

    def _remove(self, draft_id: str) -> None:
        store = self._store(draft_id)
        if not store.exists():
            raise InterviewError(404, "Unknown interview")
        store.path.unlink()
        store.path.with_suffix(".lock").unlink(missing_ok=True)

    def answer(self, draft_id: str, question_id: str, value: Answer | None) -> Draft:
        draft = self.get(draft_id)
        defn = self.defn()
        q = defn.question(question_id)
        followup = next((f for f in draft.followups if f.id == question_id), None)
        if q is not None and q.status == "accepted":
            kind, options = q.kind, [o.value for o in q.accepted_options()]
        elif followup is not None:
            kind, options = followup.kind, followup.options
        else:
            raise InterviewError(404, f"Unknown question {question_id}")
        if value is not None and not _empty(value):
            values = [value] if isinstance(value, str) else value
            if (kind == "multi") != isinstance(value, list):
                raise InterviewError(422, f"{question_id} takes {kind} answers")
            if kind != "text" and any(v not in options for v in values):
                raise InterviewError(422, f"Not an option of {question_id}")
        if followup is not None:
            followup.answer = None if _empty(value) else value
        elif _empty(value):
            draft.answers.pop(question_id, None)
        else:
            assert value is not None
            draft.answers[question_id] = value
        return self._save(draft)

    # --- Advancing -------------------------------------------------------------------------

    def job(self, draft_id: str) -> SyncJob | None:
        return self.jobs.get(draft_id)

    def advance(self, draft_id: str) -> dict[str, Any]:
        """Move past the current step. Returns {"job": True} when an LLM job was started."""
        draft = self.get(draft_id)
        job = self.jobs.get(draft_id)
        if job is not None and job.state == "running":
            raise InterviewError(409, "This interview is already working on something")
        defn = self.defn()
        if not defn.sections:
            raise InterviewError(503, "interview.yaml has no sections")
        draft.section = min(draft.section, len(defn.sections) - 1)
        section = defn.sections[draft.section]
        if draft.stage == "questions":
            missing = [
                q.text
                for q in defn.accepted_questions(section.id)
                if q.kind != "text" and _empty(draft.answers.get(q.id))
            ]
            if missing:
                raise InterviewError(422, "Please answer: " + "; ".join(missing))
            if section.max_followups > 0:
                return self._start(draft_id, self._followups_job)
        return self._next_section(draft)

    def _next_section(self, draft: Draft) -> dict[str, Any]:
        defn = self.defn()
        if draft.section + 1 < len(defn.sections):
            draft.section += 1
            draft.stage = "questions"
            self._save(draft)
            return {"job": False}
        return self._start(draft.id, self._tailor_job)

    def _start(
        self, draft_id: str, run: Callable[[str, Callable[[str], None]], Any]
    ) -> dict[str, Any]:
        job = self.jobs.setdefault(draft_id, SyncJob())
        job.start(lambda log: run(draft_id, log))
        return {"job": True}

    async def _followups_job(self, draft_id: str, log: Callable[[str], None]) -> dict[str, Any]:
        log("Loading the model (the first time can take a minute or two)…")
        async with self.llm.lease() as llm:
            draft = self.get(draft_id)
            defn = self.defn()
            section = defn.sections[draft.section]
            log(f"Thinking of follow-up questions about {section.title.lower()}…")
            asked = await generate_followups(
                llm, defn, section, draft.answers, draft.followups, self.chunks()
            )
            draft = self.get(draft_id)  # answers may have changed while the model thought
            draft.followups = [f for f in draft.followups if f.section != section.id] + asked
            if asked:
                draft.stage = "followups"
                self._save(draft)
                return {"stage": "followups"}
            log("No follow-up questions needed.")
            if draft.section + 1 < len(defn.sections):
                draft.section += 1
                draft.stage = "questions"
                self._save(draft)
                return {"stage": "questions"}
            self._save(draft)
            return await self._tailor(draft_id, llm, log)

    async def _tailor_job(self, draft_id: str, log: Callable[[str], None]) -> dict[str, Any]:
        log("Loading the model (the first time can take a minute or two)…")
        async with self.llm.lease() as llm:
            return await self._tailor(draft_id, llm, log)

    async def _tailor(
        self, draft_id: str, llm: LLMBackend, log: Callable[[str], None]
    ) -> dict[str, Any]:
        draft = self.get(draft_id)
        defn = self.defn()
        onto, _ = self.ontology.load()
        log("Tailoring the steps to your project…")
        fresh = await tailor(llm, defn, onto, self.chunks(), draft.answers, draft.followups, log)
        if draft.project_id is None:
            with self.db.transaction() as c:
                project = ProgressDB.insert_project(c, draft.name)
                # Written inside the transaction: if this fails, the project isn't created.
                self.overlays.store(project.id).update(None, lambda _o: fresh)
            project_id = project.id
            log(f"Created {draft.name} with {len(fresh.nodes)} project-specific steps.")
        else:
            project_id = draft.project_id
            if self.db.get_project(project_id) is None:
                raise InterviewError(404, "The project was deleted")
            if self.overlays.exists(project_id):
                current, _ = self.overlays.store(project_id).load()
            else:
                current = untailored(onto)
            updated = retailor_diff(defn, onto, current, fresh)
            self.overlays.store(project_id).update(None, lambda _o: updated)
            log(f"{len(updated.proposals)} changes to review.")
        # The job (and its result) stays readable; only the draft goes.
        self._remove(draft_id)
        return {"project_id": project_id}
