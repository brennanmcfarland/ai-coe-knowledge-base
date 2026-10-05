import httpx
import pytest
import respx

from coe_wizard.clickup import API_BASE, ClickUpClient, ClickUpError, RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_bucket_allows_burst_then_paces() -> None:
    clock = FakeClock()
    limiter = RateLimiter(60, clock=clock, sleep=clock.sleep)
    for _ in range(60):
        await limiter.acquire()
    assert clock.sleeps == []
    await limiter.acquire()
    assert clock.now == pytest.approx(1.0)  # 60/min refills one token per second


async def test_low_remaining_header_blocks_until_reset() -> None:
    clock = FakeClock()
    limiter = RateLimiter(60, clock=clock, sleep=clock.sleep)
    limiter.observe(
        httpx.Headers({"x-ratelimit-remaining": "1", "x-ratelimit-reset": "1030"}), wall_now=1000
    )
    await limiter.acquire()
    assert clock.now == pytest.approx(31.0)


async def test_healthy_remaining_header_does_not_block() -> None:
    clock = FakeClock()
    limiter = RateLimiter(60, clock=clock, sleep=clock.sleep)
    limiter.observe(
        httpx.Headers({"x-ratelimit-remaining": "50", "x-ratelimit-reset": "1030"}), wall_now=1000
    )
    await limiter.acquire()
    assert clock.now == 0


def client(clock: FakeClock) -> ClickUpClient:
    limiter = RateLimiter(600, clock=clock, sleep=clock.sleep)
    return ClickUpClient("tok", limiter=limiter, sleep=clock.sleep)


@respx.mock
async def test_retries_429_then_succeeds() -> None:
    clock = FakeClock()
    route = respx.get(f"{API_BASE}/v2/x").mock(
        side_effect=[httpx.Response(429), httpx.Response(503), httpx.Response(200, json={"a": 1})]
    )
    assert await client(clock).get("/v2/x") == {"a": 1}
    assert route.call_count == 3
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok"
    assert clock.now >= 1  # backed off


@respx.mock
async def test_gives_up_after_max_retries() -> None:
    clock = FakeClock()
    respx.get(f"{API_BASE}/v2/x").mock(return_value=httpx.Response(429))
    with pytest.raises(ClickUpError) as err:
        await client(clock).get("/v2/x")
    assert err.value.status == 429


@respx.mock
async def test_4xx_is_not_retried() -> None:
    clock = FakeClock()
    route = respx.get(f"{API_BASE}/v2/x").mock(return_value=httpx.Response(401))
    with pytest.raises(ClickUpError):
        await client(clock).get("/v2/x")
    assert route.call_count == 1


@respx.mock
async def test_docs_paginates_with_cursor() -> None:
    clock = FakeClock()
    route = respx.get(f"{API_BASE}/v3/workspaces/w/docs").mock(
        side_effect=[
            httpx.Response(200, json={"docs": [{"id": "d1"}], "next_cursor": "c2"}),
            httpx.Response(200, json={"docs": [{"id": "d2"}], "next_cursor": None}),
        ]
    )
    docs = [d["id"] async for d in client(clock).docs("w", "s", "SPACE")]
    assert docs == ["d1", "d2"]
    assert route.calls[1].request.url.params["cursor"] == "c2"
    assert route.calls[0].request.url.params["parent_type"] == "SPACE"
