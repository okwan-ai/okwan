"""Gusto OAuth: how Okwan is let in, and how it stays in.

Gusto has no static key. A business's Gusto admin approves Okwan (the
authorize link), Gusto sends them back with a code, and Okwan's server
swaps the code for a company's access and refresh tokens with Okwan's own
client id and secret. Those two are the server's, from its environment
(OKWAN_GUSTO_CLIENT_ID, OKWAN_GUSTO_CLIENT_SECRET), never a tenant's.

A tenant's vault holds four fields, written by the callback and never
typed: the company's uuid, the access token, the refresh token, and when
the access token expires (epoch seconds).

An access token lives two hours. A refresh token works once: refreshing
returns a new pair, and the old refresh token is spent. So a refresh runs
under one lock per process, a request that waited on the lock reuses the
pair the first one got, and the new pair is written back to the vault at
once (`on_rotate`). One API instance today; a second would need the lock
in the database (OKWAN_PROJECT.md §9 2026-11-09).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlencode

import httpx
from okwan_core import AuthAdapter
from okwan_core.errors import CredentialError

HOSTS = {"demo": "https://api.gusto-demo.com", "production": "https://api.gusto.com"}

#: Gusto versions its API by date and answers in the version a call names.
API_VERSION = "2026-06-15"

#: Refresh this long before the access token runs out, not at the edge.
SKEW_SECONDS = 60

#: The tests' seam for the token endpoint; production leaves it None.
transport: httpx.AsyncBaseTransport | None = None


def environment() -> str:
    """demo (Gusto's sandbox) unless the server says production."""
    env = os.environ.get("OKWAN_GUSTO_ENV", "demo").strip().lower()
    return env if env in HOSTS else "demo"


def host() -> str:
    return HOSTS[environment()]


#: The server's own settings Connect Gusto cannot start without.
APP_SETTINGS = ("OKWAN_GUSTO_CLIENT_ID", "OKWAN_GUSTO_CLIENT_SECRET")


def missing_settings() -> list[str]:
    """Which of APP_SETTINGS this server lacks, by name, never by value:
    for the operator's log, so a misspelt variable is found at once."""
    return [name for name in APP_SETTINGS if not os.environ.get(name, "").strip()]


def app_credentials() -> tuple[str, str]:
    """Okwan's own client id and secret, from the server's environment."""
    if missing_settings():
        raise CredentialError("Gusto is not set up on this Okwan server (OKWAN_GUSTO_CLIENT_ID and _SECRET)")
    return os.environ["OKWAN_GUSTO_CLIENT_ID"].strip(), os.environ["OKWAN_GUSTO_CLIENT_SECRET"].strip()


def configured() -> bool:
    try:
        app_credentials()
    except CredentialError:
        return False
    return True


def redirect_uri() -> str:
    """Where Gusto sends the admin back: the dashboard's callback, exactly
    as registered on the Gusto application."""
    explicit = os.environ.get("OKWAN_GUSTO_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    dashboard = os.environ.get("OKWAN_DASHBOARD_URL", "http://localhost:3000").rstrip("/")
    return f"{dashboard}/integrations/gusto/callback"


def authorize_url(state: str) -> str:
    client_id, _ = app_credentials()
    query = urlencode({"client_id": client_id, "redirect_uri": redirect_uri(),
                       "response_type": "code", "state": state})
    return f"{host()}/oauth/authorize?{query}"


@dataclass(frozen=True, slots=True)
class Tokens:
    access_token: str
    refresh_token: str
    #: Epoch seconds at which the access token stops working.
    expires_at: int

    def fields(self) -> dict[str, str]:
        """The vault fields this pair writes."""
        return {"access_token": self.access_token, "refresh_token": self.refresh_token,
                "expires_at": str(self.expires_at)}


async def _token_request(body: dict[str, str]) -> Tokens:
    client_id, secret = app_credentials()
    payload = {"client_id": client_id, "client_secret": secret, "redirect_uri": redirect_uri(), **body}
    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        response = await client.post(f"{host()}/oauth/token", json=payload)
    if response.status_code >= 400:
        # Never the body: an OAuth error can echo what was sent.
        raise CredentialError(f"Gusto refused the token request ({response.status_code}); connect Gusto again")
    data = response.json()
    access, refresh = data.get("access_token"), data.get("refresh_token")
    if not access or not refresh:
        raise CredentialError("Gusto's token response carried no token pair")
    return Tokens(access, refresh, int(time.time()) + int(data.get("expires_in", 7200)))


async def exchange_code(code: str) -> Tokens:
    """The admin's one-time code (ten minutes) for the company's first pair."""
    return await _token_request({"code": code, "grant_type": "authorization_code"})


async def refresh(refresh_token: str) -> Tokens:
    """A spent-on-use refresh token for a new pair."""
    return await _token_request({"refresh_token": refresh_token, "grant_type": "refresh_token"})


async def token_company(access_token: str) -> str:
    """Which company a fresh token reads: one, under Gusto's strict access."""
    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        response = await client.get(
            f"{host()}/v1/token_info",
            headers={"Authorization": f"Bearer {access_token}", "X-Gusto-API-Version": API_VERSION})
    if response.status_code >= 400:
        raise CredentialError(f"Gusto would not say which company the token reads ({response.status_code})")
    resource = (response.json() or {}).get("resource") or {}
    if resource.get("type") != "Company" or not resource.get("uuid"):
        raise CredentialError("Gusto's token reads no company")
    return str(resource["uuid"])


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


#: One refresh at a time, process-wide, and the pair each spent refresh
#: token turned into, so a request that raced the first one reuses it.
_lock = asyncio.Lock()
_rotated: dict[str, Tokens] = {}


async def rotate(current_refresh: str) -> Tokens:
    async with _lock:
        known = _rotated.get(_digest(current_refresh))
        if known is not None:
            return known
        fresh = await refresh(current_refresh)
        if len(_rotated) > 10_000:
            _rotated.clear()
        _rotated[_digest(current_refresh)] = fresh
        return fresh


class _GustoAuth(httpx.Auth):
    """Bearer auth that renews itself and reports each new pair."""

    def __init__(self, credentials: dict[str, str]) -> None:
        self._access = credentials["access_token"]
        self._refresh = credentials["refresh_token"]
        try:
            self._expires = int(credentials.get("expires_at") or 0)
        except ValueError:
            self._expires = 0
        #: Set by the context factory: writes a new pair to the vault.
        self.on_rotate: Callable[[Tokens], Awaitable[None]] | None = None

    async def _renew(self) -> None:
        fresh = await rotate(self._refresh)
        self._access, self._refresh, self._expires = fresh.access_token, fresh.refresh_token, fresh.expires_at
        if self.on_rotate is not None:
            await self.on_rotate(fresh)

    async def async_auth_flow(self, request: httpx.Request):
        if self._expires - SKEW_SECONDS <= time.time():
            await self._renew()
        request.headers["X-Gusto-API-Version"] = API_VERSION
        request.headers["Authorization"] = f"Bearer {self._access}"
        response = yield request
        if response.status_code == 401:
            await self._renew()
            request.headers["Authorization"] = f"Bearer {self._access}"
            yield request


@dataclass(frozen=True, slots=True)
class GustoOAuth(AuthAdapter):
    """Written by the Connect Gusto callback, never typed."""

    required_fields: tuple[str, ...] = ("company_uuid", "access_token", "refresh_token", "expires_at")
    #: How a person connects it: the catalog and the dashboard read this.
    connect: ClassVar[str] = "oauth"

    def bind(self, credentials: dict[str, str]) -> httpx.Auth:
        self.validate(credentials)
        return _GustoAuth(credentials)
