"""PayPal OAuth2 client-credentials auth.

PayPal mints a short-lived bearer token from a client id and secret.
Tokens are cached process-wide, keyed by a digest of the credential
pair and token endpoint, so a token is fetched once per credential set
rather than once per request — `bind()` runs at every call site and
must stay cheap.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass

import httpx
from okwan_core import AuthAdapter
from okwan_core.errors import CredentialError

SANDBOX_HOST = "https://api-m.sandbox.paypal.com"
LIVE_HOST = "https://api-m.paypal.com"

_TOKEN_SKEW_SECONDS = 60
_cache: dict[str, tuple[str, float]] = {}
_lock = asyncio.Lock()


def host_for(credentials: dict[str, str]) -> str:
    """Sandbox unless the credential set explicitly says live."""
    env = (credentials.get("environment") or "sandbox").strip().lower()
    return LIVE_HOST if env == "live" else SANDBOX_HOST


def _cache_key(client_id: str, secret: str, token_url: str) -> str:
    return hashlib.sha256(f"{client_id}:{secret}:{token_url}".encode()).hexdigest()


async def _fetch_token(client_id: str, secret: str, token_url: str) -> tuple[str, float]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            token_url,
            auth=(client_id, secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
        )
    if response.status_code >= 400:
        raise CredentialError(
            f"PayPal token request failed ({response.status_code}); "
            "check client id and secret"
        )
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise CredentialError("PayPal token response carried no access_token")
    ttl = float(payload.get("expires_in", 32400))
    return token, time.monotonic() + ttl - _TOKEN_SKEW_SECONDS


class _PayPalTokenAuth(httpx.Auth):
    def __init__(self, client_id: str, secret: str, token_url: str) -> None:
        self._client_id = client_id
        self._secret = secret
        self._token_url = token_url
        self._key = _cache_key(client_id, secret, token_url)

    async def _token(self, *, force: bool = False) -> str:
        async with _lock:
            cached = _cache.get(self._key)
            if not force and cached and cached[1] > time.monotonic():
                return cached[0]
            token, expires_at = await _fetch_token(
                self._client_id, self._secret, self._token_url
            )
            _cache[self._key] = (token, expires_at)
            return token

    async def async_auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {await self._token()}"
        response = yield request
        if response.status_code == 401:
            await response.aread()
            request.headers["Authorization"] = f"Bearer {await self._token(force=True)}"
            yield request

    def sync_auth_flow(self, request: httpx.Request):
        raise NotImplementedError("PayPal auth is async-only")


@dataclass(frozen=True, slots=True)
class PayPalOAuth2Auth(AuthAdapter):
    """Client-credentials adapter.

    `ApiKeyAuth` binds `required_fields[0]` only, so a two-field
    credential needs its own adapter — the same reason Shopify has one.
    `environment` is optional and not a secret; it selects the host.
    """

    required_fields: tuple[str, ...] = ("client_id", "client_secret")

    def bind(self, credentials: dict[str, str]) -> httpx.Auth:
        self.validate(credentials)
        return _PayPalTokenAuth(
            credentials["client_id"],
            credentials["client_secret"],
            f"{host_for(credentials)}/v1/oauth2/token",
        )
