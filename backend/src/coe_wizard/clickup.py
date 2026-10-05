"""Rate-limited ClickUp REST client."""

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

API_BASE = "https://api.clickup.com/api"
MAX_RETRIES = 6
# Stop spending requests when the server says we are this close to its limit.
REMAINING_FLOOR = 2


class RateLimiter:
    """Token bucket that also defers to the server's X-RateLimit-* headers."""

    def __init__(
        self,
        rpm: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self.capacity = float(rpm)
        self.rate = rpm / 60.0
        self.tokens = float(rpm)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                if now < self._blocked_until:
                    await self._sleep(self._blocked_until - now)
                    continue
                self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
                self._last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await self._sleep((1 - self.tokens) / self.rate)

    def observe(self, headers: httpx.Headers, wall_now: float | None = None) -> None:
        """Block until the server's reset time when it reports we are nearly out of budget."""
        remaining = headers.get("x-ratelimit-remaining")
        reset = headers.get("x-ratelimit-reset")
        if remaining is None or reset is None:
            return
        try:
            remaining_n, reset_epoch = int(remaining), float(reset)
        except ValueError:
            return
        if remaining_n <= REMAINING_FLOOR:
            wall = time.time() if wall_now is None else wall_now
            self.block_for(max(0.0, reset_epoch - wall) + 1)

    def block_for(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, self._clock() + seconds)


class ClickUpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"ClickUp API error {status}: {message}")
        self.status = status


class ClickUpClient:
    def __init__(
        self,
        token: str,
        rpm: int = 60,
        http: httpx.AsyncClient | None = None,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http or httpx.AsyncClient(timeout=60)
        self._headers = {"Authorization": f"Bearer {token}"}
        self._limiter = limiter or RateLimiter(rpm, sleep=sleep)
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{API_BASE}{path}"
        for attempt in range(MAX_RETRIES + 1):
            await self._limiter.acquire()
            try:
                resp = await self._http.get(url, params=params, headers=self._headers)
            except httpx.TransportError as exc:
                if attempt == MAX_RETRIES:
                    raise ClickUpError(0, str(exc)) from exc
                await self._sleep(self._backoff(attempt))
                continue
            self._limiter.observe(resp.headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_RETRIES:
                    raise ClickUpError(resp.status_code, resp.text[:200])
                delay = self._backoff(attempt)
                if resp.status_code == 429:
                    self._limiter.block_for(delay)
                else:
                    await self._sleep(delay)
                continue
            if resp.status_code >= 400:
                raise ClickUpError(resp.status_code, resp.text[:200])
            return resp.json()
        raise AssertionError("unreachable")

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(60.0, 2.0**attempt) + random.uniform(0, 1)

    # --- Endpoints -------------------------------------------------------------------------

    async def space_folders(self, space_id: str) -> list[dict[str, Any]]:
        data = await self.get(f"/v2/space/{space_id}/folder", {"archived": "false"})
        return data.get("folders", [])

    async def space_lists(self, space_id: str) -> list[dict[str, Any]]:
        data = await self.get(f"/v2/space/{space_id}/list", {"archived": "false"})
        return data.get("lists", [])

    async def docs(
        self, workspace_id: str, parent_id: str, parent_type: str
    ) -> AsyncIterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "parent_id": parent_id,
                "parent_type": parent_type,
                "deleted": "false",
                "archived": "false",
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor
            data = await self.get(f"/v3/workspaces/{workspace_id}/docs", params)
            for doc in data.get("docs", []):
                yield doc
            cursor = data.get("next_cursor") or data.get("cursor")
            if not cursor or not data.get("docs"):
                return

    async def doc_pages(self, workspace_id: str, doc_id: str) -> list[dict[str, Any]]:
        data = await self.get(
            f"/v3/workspaces/{workspace_id}/docs/{doc_id}/pages",
            {"max_page_depth": -1, "content_format": "text/md"},
        )
        return data if isinstance(data, list) else data.get("pages", [])
