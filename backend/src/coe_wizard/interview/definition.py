"""The curated interview: sections of questions, where each choice question marked `facet` is a
facet dimension and its options are the facet's values (used to tag chunks and nodes).

Lives in data/interview.yaml, copied from the committed seed when missing. Builds add proposed
questions and option values, which follow the ontology's curation rules: locked items are never
overwritten, rejected ones are never proposed again.
"""

import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..ontology.model import Status

QuestionKind = Literal["single", "multi", "text"]
SEED = resources.files(__package__) / "seed.yaml"


def value_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "value"


class Option(BaseModel):
    value: str
    label: str
    status: Status = "accepted"
    locked: bool = False


class Question(BaseModel):
    id: str
    section: str
    text: str
    kind: QuestionKind
    facet: bool = False
    options: list[Option] = Field(default_factory=list)
    status: Status = "accepted"
    locked: bool = False

    def accepted_options(self) -> list[Option]:
        return [o for o in self.options if o.status == "accepted"]

    def option(self, value: str) -> Option | None:
        return next((o for o in self.options if o.value == value), None)


class Section(BaseModel):
    id: str
    title: str
    max_followups: int = Field(default=3, ge=0, le=10)


class InterviewRejected(BaseModel):
    questions: list[str] = Field(default_factory=list)
    options: list[tuple[str, str]] = Field(default_factory=list)


class InterviewDef(BaseModel):
    version: int = 1
    sections: list[Section] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    rejected: InterviewRejected = Field(default_factory=InterviewRejected)

    @model_validator(mode="after")
    def _check(self) -> InterviewDef:
        sections = [s.id for s in self.sections]
        if len(set(sections)) != len(sections):
            raise ValueError("Duplicate section ids")
        ids = [q.id for q in self.questions]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"Duplicate question ids: {dupes}")
        for q in self.questions:
            if q.section not in sections:
                raise ValueError(f"Question {q.id} is in unknown section {q.section}")
            if q.kind == "text":
                if q.facet or q.options:
                    raise ValueError(f"Text question {q.id} can't be a facet or have options")
                continue
            values = [o.value for o in q.options]
            if len(set(values)) != len(values):
                raise ValueError(f"Question {q.id} has duplicate option values")
            if q.status == "accepted" and not q.accepted_options():
                raise ValueError(f"Choice question {q.id} has no accepted options")
        return self

    def question(self, qid: str) -> Question | None:
        return next((q for q in self.questions if q.id == qid), None)

    def section(self, sid: str) -> Section | None:
        return next((s for s in self.sections if s.id == sid), None)

    def accepted_questions(self, section: str | None = None) -> list[Question]:
        return [
            q
            for q in self.questions
            if q.status == "accepted" and (section is None or q.section == section)
        ]

    def facets(self) -> dict[str, list[str]]:
        """Accepted facet dimensions and their accepted values."""
        return {
            q.id: [o.value for o in q.accepted_options()]
            for q in self.accepted_questions()
            if q.facet
        }

    def proposals(self) -> int:
        return sum(q.status == "proposed" for q in self.questions) + sum(
            o.status == "proposed"
            for q in self.questions
            if q.status == "accepted"
            for o in q.options
        )


def ensure_seeded(path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with resources.as_file(SEED) as seed:
            shutil.copyfile(seed, path)


class ProposedQuestion(BaseModel):
    text: str
    section: str
    kind: QuestionKind
    options: list[str]


def merge_proposals(
    current: InterviewDef,
    new_values: Mapping[str, Iterable[str]],
    new_questions: Sequence[ProposedQuestion] = (),
) -> InterviewDef:
    """Add build proposals: new values for existing facets, and new facet questions."""
    out = current.model_copy(deep=True)
    rejected_opts = {tuple(x) for x in out.rejected.options}
    for qid, labels in new_values.items():
        q = out.question(qid)
        if q is None or q.kind == "text" or q.locked:
            continue
        have = {o.value for o in q.options} | {o.label.lower() for o in q.options}
        for label in labels:
            label = label.strip()
            value = value_slug(label)
            if not label or value in have or label.lower() in have:
                continue
            if (qid, value) in rejected_opts:
                continue
            q.options.append(Option(value=value, label=label, status="proposed"))
            have.add(value)
    sections = {s.id for s in out.sections}
    for pq in new_questions:
        qid = value_slug(pq.text).replace("-", "_")[:40]
        by_value: dict[str, str] = {}
        for o in pq.options:
            if o.strip():
                by_value.setdefault(value_slug(o), o.strip())
        options = list(by_value.items())
        if (
            out.question(qid) is not None
            or qid in out.rejected.questions
            or pq.section not in sections
            or (pq.kind != "text" and not options)
        ):
            continue
        out.questions.append(
            Question(
                id=qid,
                section=pq.section,
                text=pq.text.strip(),
                kind=pq.kind,
                facet=pq.kind != "text",
                options=[]
                if pq.kind == "text"
                else [Option(value=value, label=label) for value, label in options],
                status="proposed",
            )
        )
    return out
