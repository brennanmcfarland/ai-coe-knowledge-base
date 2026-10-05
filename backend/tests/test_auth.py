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


def test_missing_state_is_rejected() -> None:
    with pytest.raises(AuthError):
        manager().consume_state(None)
