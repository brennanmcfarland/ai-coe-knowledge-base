"""ClickUp OAuth.

The client id/secret live in the OS keyring. The access token lives only in process memory, so
every backend restart requires a fresh login.
"""

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
import keyring
from keyring.errors import KeyringError

AUTHORIZE_URL = "https://app.clickup.com/api"
TOKEN_URL = "https://api.clickup.com/api/v2/oauth/token"
USER_URL = "https://api.clickup.com/api/v2/user"

KEYRING_SERVICE = "coe-wizard-clickup"
STATE_TTL_SECONDS = 600


@dataclass(frozen=True)
class ClientCredentials:
    client_id: str
    client_secret: str


class CredentialStore(Protocol):
    def load(self) -> ClientCredentials | None: ...
    def save(self, creds: ClientCredentials) -> None: ...


class KeyringCredentialStore:
    def load(self) -> ClientCredentials | None:
        try:
            client_id = keyring.get_password(KEYRING_SERVICE, "client_id")
            client_secret = keyring.get_password(KEYRING_SERVICE, "client_secret")
        except KeyringError:
            return None
        if not client_id or not client_secret:
            return None
        return ClientCredentials(client_id, client_secret)

    def save(self, creds: ClientCredentials) -> None:
        keyring.set_password(KEYRING_SERVICE, "client_id", creds.client_id)
        keyring.set_password(KEYRING_SERVICE, "client_secret", creds.client_secret)


class MemoryCredentialStore:
    def __init__(self, creds: ClientCredentials | None = None) -> None:
        self._creds = creds

    def load(self) -> ClientCredentials | None:
        return self._creds

    def save(self, creds: ClientCredentials) -> None:
        self._creds = creds


class AuthError(Exception):
    pass


@dataclass
class AuthManager:
    store: CredentialStore
    redirect_uri: str
    http: httpx.AsyncClient = field(default_factory=lambda: httpx.AsyncClient(timeout=30))
    token: str | None = None
    user: dict[str, Any] | None = None
    _states: dict[str, float] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return self.store.load() is not None

    @property
    def logged_in(self) -> bool:
        return self.token is not None

    def configure(self, client_id: str, client_secret: str) -> None:
        if not client_id.strip() or not client_secret.strip():
            raise AuthError("client_id and client_secret are required")
        self.store.save(ClientCredentials(client_id.strip(), client_secret.strip()))

    def authorize_url(self) -> str:
        creds = self.store.load()
        if creds is None:
            raise AuthError("ClickUp OAuth app is not configured")
        self._prune_states()
        state = secrets.token_urlsafe(32)
        self._states[state] = time.monotonic() + STATE_TTL_SECONDS
        query = urlencode(
            {"client_id": creds.client_id, "redirect_uri": self.redirect_uri, "state": state}
        )
        return f"{AUTHORIZE_URL}?{query}"

    def consume_state(self, state: str | None) -> None:
        self._prune_states()
        if not state or self._states.pop(state, None) is None:
            raise AuthError("Invalid or expired OAuth state")

    async def complete(self, code: str, state: str | None) -> None:
        self.consume_state(state)
        creds = self.store.load()
        if creds is None:
            raise AuthError("ClickUp OAuth app is not configured")
        resp = await self.http.post(
            TOKEN_URL,
            params={
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "code": code,
            },
        )
        if resp.status_code != 200:
            raise AuthError(f"Token exchange failed ({resp.status_code})")
        token = resp.json().get("access_token")
        if not token:
            raise AuthError("Token exchange returned no access_token")
        user_resp = await self.http.get(USER_URL, headers={"Authorization": f"Bearer {token}"})
        self.token = token
        self.user = user_resp.json().get("user") if user_resp.status_code == 200 else None

    def logout(self) -> None:
        self.token = None
        self.user = None

    def _prune_states(self) -> None:
        now = time.monotonic()
        for s in [s for s, exp in self._states.items() if exp < now]:
            del self._states[s]
