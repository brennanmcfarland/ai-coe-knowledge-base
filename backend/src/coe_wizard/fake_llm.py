"""Scripted stand-in for the local model (`serve --fake-llm`), for Playwright and demos.

It recognises the request by the schema's property names and answers deterministically:
- follow-ups: one multiple-choice question in the domain section, none elsewhere
- selection: keep every candidate step
- generation: for oncology projects, a cited "Acquire oncology terminologies" step (from the
  first passage) and an uncited "Obtain terminology licenses" step; nothing otherwise
- summaries: echo the sources as [S1]; prerequisites: the first listed step
A prompt containing `simulate-llm-failure` fails once per process, to exercise Retry.
"""

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from .llm import LLMBackend, LLMError
from .llm_server import APPROX_DOWNLOAD_BYTES

FAILURE_TOKEN = "simulate-llm-failure"


class FakeLLMBackend:
    def __init__(self) -> None:
        self.failed = False
        self.prompts: list[str] = []

    async def complete(self, prompt: str, json_schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        if FAILURE_TOKEN in prompt and not self.failed:
            self.failed = True
            raise LLMError("Simulated model failure")
        props = json_schema.get("properties", {})
        if "followups" in props:
            if "Current section: Clinical or scientific domain" in prompt:
                return {
                    "followups": [
                        {
                            "text": "Which coding systems do you need to support?",
                            "kind": "single",
                            "options": ["ICD-O-3", "SNOMED CT", "Not sure yet"],
                        }
                    ]
                }
            return {"followups": []}
        if "decisions" in props:
            ids = props["decisions"]["items"]["properties"]["id"]["enum"]
            return {"decisions": [{"id": i, "include": True} for i in ids]}
        if "covered_by" in str(props.get("steps", "")):
            if "Oncology" not in prompt:
                return {"steps": []}
            labels = props["steps"]["items"]["properties"]["sources"]["items"].get("enum", [])
            return {
                "steps": [
                    {
                        "title": "Acquire oncology terminologies",
                        "description": "Get the oncology code systems the project needs.",
                        "checklist": ["Download ICD-O-3", "Map local codes to ICD-O-3"],
                        "sources": labels[:1],
                        "covered_by": "none",
                    },
                    {
                        "title": "Obtain terminology licenses",
                        "description": "Check the licenses of the code systems you use.",
                        "checklist": ["Review license terms"],
                        "sources": [],
                        "covered_by": "none",
                    },
                ]
            }
        if "summary" in props:
            if "[S1]" in prompt:
                return {"summary": "Use the standard oncology code systems [S1]."}
            return {"summary": "General guidance written without a knowledge-base source."}
        if "prerequisites" in props:
            ids = props["prerequisites"]["items"]["properties"]["id"]["enum"]
            return {"prerequisites": [{"id": ids[0], "confidence": 0.9}] if ids else []}
        if "steps" in props:  # build map step
            return {"steps": [], "facets": {}, "new_values": []}
        if "questions" in props:
            return {"questions": []}
        raise LLMError(f"Fake LLM has no answer for schema {sorted(props)}")


class FakeProvider:
    """Always ready; leases the same scripted backend."""

    def __init__(self, backend: LLMBackend | None = None) -> None:
        self.backend = backend or FakeLLMBackend()

    def status(self) -> dict[str, Any]:
        return {
            "state": "ready",
            "error": None,
            "download": None,
            "approx_bytes": APPROX_DOWNLOAD_BYTES,
        }

    def start_download(self) -> bool:
        return False

    def cancel_download(self) -> bool:
        return False

    def warm(self) -> None:
        return None

    @contextlib.asynccontextmanager
    async def lease(self) -> AsyncIterator[LLMBackend]:
        yield self.backend

    async def aclose(self) -> None:
        return None
