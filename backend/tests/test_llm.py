import json

import httpx
import pytest

from coe_wizard.llm import LlamaServerBackend, LLMError

SCHEMA = {"type": "object"}


def backend(replies: list[tuple[str, str]], max_attempts: int = 3) -> LlamaServerBackend:
    """Backend whose server answers with (content, finish_reason) pairs in order."""
    queue = list(replies)

    def handler(_request: httpx.Request) -> httpx.Response:
        content, finish = queue.pop(0)
        choice = {"message": {"content": content}, "finish_reason": finish}
        return httpx.Response(200, json={"choices": [choice]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LlamaServerBackend("http://llm", max_attempts=max_attempts, http=http)


async def test_invalid_and_truncated_output_is_retried() -> None:
    llm = backend([('{"a": ', "stop"), ('{"a": 1', "length"), (json.dumps({"a": 1}), "stop")])
    assert await llm.complete("p", SCHEMA) == {"a": 1}


async def test_gives_up_after_max_attempts() -> None:
    llm = backend([("[1]", "stop"), ('{"a"', "stop")], max_attempts=2)
    with pytest.raises(LLMError, match="gave up after 2 attempts"):
        await llm.complete("p", SCHEMA)
