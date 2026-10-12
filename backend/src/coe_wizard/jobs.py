"""Background jobs with a progress log, polled by the UI and scripts."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

SyncState = Literal["idle", "running", "succeeded", "failed"]


@dataclass
class SyncJob:
    """A background job with a progress log (ClickUp sync, interview LLM steps)."""

    state: SyncState = "idle"
    messages: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    _task: asyncio.Task[None] | None = None

    def start(self, run: Callable[[Callable[[str], None]], Awaitable[Any]]) -> bool:
        if self.state == "running":
            return False
        self.state, self.messages, self.error, self.result = "running", [], None, None
        self.started_at, self.finished_at = time.time(), None
        self._task = asyncio.create_task(self._run(run))
        return True

    async def _run(self, run: Callable[[Callable[[str], None]], Awaitable[Any]]) -> None:
        try:
            self.result = await run(self.messages.append)
            self.state = "succeeded"
        except Exception as exc:  # surfaced to the UI and scrape.sh
            self.error = str(exc)
            self.messages.append(f"Error: {exc}")
            self.state = "failed"
        finally:
            self.finished_at = time.time()

    def view(self, since: int = 0) -> dict[str, Any]:
        return {
            "state": self.state,
            "messages": self.messages[since:],
            "next": len(self.messages),
            "error": self.error,
            "result": self.result,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
