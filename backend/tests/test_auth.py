from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from coe_wizard.auth import (
    TOKEN_URL,
    USER_URL,
    AuthError,
    AuthManager,
    ClientCredentials,
    MemoryCredentialStore,
)


def manager(configured: bool = True) -> AuthManager:
    creds = ClientCredentials("cid", "secret") if configured else None
    return AuthManager(MemoryCredentialStore(creds), "http://127.0.0.1:8765/api/auth/callback")


def state_of(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


def test_authorize_requires_configuration() -> None:
    with pytest.raises(AuthError):
        manager(configured=False).authorize_url()


def test_authorize_url_carries_client_and_state() -> None:
    url = manager().authorize_url()
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["cid"]
    assert q["redirect_uri"] == ["http://127.0.0.1:8765/api/auth/callback"]
    assert len(q["state"][0]) > 20


@respx.mock
async def test_callback_with_valid_state_stores_token_in_memory() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    respx.get(USER_URL).mock(return_value=httpx.Response(200, json={"user": {"username": "u"}}))
    m = manager()
    await m.complete("code", state_of(m.authorize_url()))
    assert m.token == "tok" and m.user == {"username": "u"}


async def test_unknown_state_is_rejected() -> None:
    m = manager()
    m.authorize_url()
    with pytest.raises(AuthError):
        await m.complete("code", "forged")
    assert m.token is None


@respx.mock
async def test_state_is_single_use() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    respx.get(USER_URL).mock(return_value=httpx.Response(200, json={"user": {}}))
    m = manager()
    state = state_of(m.authorize_url())
    await m.complete("code", state)
    with pytest.raises(AuthError):
        await m.complete("code", state)


@respx.mock
async def test_personal_token_is_verified_and_sent_raw() -> None:
    route = respx.get(USER_URL).mock(
        return_value=httpx.Response(200, json={"user": {"username": "u"}})
    )
    m = manager(configured=False)
    await m.use_personal_token(" pk_123 ")
    assert m.token == "pk_123" and m.user == {"username": "u"}
    assert route.calls.last.request.headers["Authorization"] == "pk_123"


@respx.mock
async def test_rejected_personal_token_is_not_kept() -> None:
    respx.get(USER_URL).mock(return_value=httpx.Response(401, json={"err": "nope"}))
    m = manager()
    with pytest.raises(AuthError):
        await m.use_personal_token("pk_bad")
    assert m.token is None


async def test_non_personal_token_is_rejected() -> None:
    with pytest.raises(AuthError):
        await manager().use_personal_token("not-a-pk")


def test_missing_state_is_rejected() -> None:
    with pytest.raises(AuthError):
        manager().consume_state(None)
