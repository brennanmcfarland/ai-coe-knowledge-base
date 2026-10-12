"""Local LLM access. Everything above this module only sees the LLMBackend protocol."""

import json
import logging
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMBackend(Protocol):
    async def complete(self, prompt: str, json_schema: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON object conforming to `json_schema`."""
        ...


class LlamaServerBackend:
    """Talks to llama.cpp's llama-server through its OpenAI-compatible endpoint.

    The schema is passed as `response_format`, which llama-server compiles into a grammar so every
    output is a valid JSON *prefix*. It can still fail to parse when generation stops early (e.g.
    at `max_tokens`), so unparseable or truncated replies are retried up to `max_attempts` times.
    """

    def __init__(
        self,
        base_url: str,
        system_prompt: str = "You are a precise assistant that answers only in JSON.",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        max_attempts: int = 4,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._system = system_prompt
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_attempts = max_attempts
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(self, prompt: str, json_schema: dict[str, Any]) -> dict[str, Any]:
        body = {
            "messages": [
                {"role": "system", "content": self._system},
                {"role": "user", "content": prompt},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": json_schema, "strict": True},
            },
        }
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = await self._http.post(self._url, json=body)
            except httpx.TransportError as exc:
                raise LLMError(f"llama-server unreachable: {exc}") from exc
            if resp.status_code != 200:
                raise LLMError(f"llama-server returned {resp.status_code}: {resp.text[:300]}")
            try:
                return _parse(resp.json()["choices"][0])
            except BadOutput as exc:
                if attempt == self._max_attempts:
                    raise LLMError(f"{exc} (gave up after {attempt} attempts)") from exc
                log.warning("%s; retrying (attempt %d/%d)", exc, attempt + 1, self._max_attempts)
        raise AssertionError("unreachable")


class BadOutput(LLMError):
    """The model replied, but not with a usable JSON object. Worth retrying."""


def _parse(choice: dict[str, Any]) -> dict[str, Any]:
    content = choice["message"]["content"]
    if choice.get("finish_reason") == "length":
        raise BadOutput(f"Model output was cut off at max_tokens ({len(content)} chars)")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise BadOutput(f"Model returned invalid JSON ({exc}): {content[:300]}") from exc
    if not isinstance(parsed, dict):
        raise BadOutput("Model returned a non-object JSON value")
    return parsed
