"""Square auth: a bearer access token, pinned to one API version.

Square takes `Authorization: Bearer <token>` and a `Square-Version`
header naming the API version the response shapes follow. Pinning the
version here means a Square release can never change a field under the
connector without a code change and a test.

`environment` chooses the host: `production` (connect.squareup.com) or
`sandbox` (connect.squareupsandbox.com). It is a stored field like the
token rather than a guess from the token's shape: a production token
sent to the sandbox, or the reverse, fails as a 401 that reads like a bad
token, and the field makes the choice visible.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from okwan_core import AuthAdapter
from okwan_core.errors import CredentialError

API_VERSION = "2026-09-16"
HOSTS = {
    "production": "https://connect.squareup.com",
    "sandbox": "https://connect.squareupsandbox.com",
}


def host_for(credentials: dict[str, str]) -> str:
    env = (credentials.get("environment") or "").strip().lower()
    if env not in HOSTS:
        raise CredentialError("square.environment must be 'production' or 'sandbox'")
    return HOSTS[env]


class _SquareAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self._token}"
        request.headers["Square-Version"] = API_VERSION
        yield request


@dataclass(frozen=True, slots=True)
class SquareAuth(AuthAdapter):
    """An access token with read scopes: PAYMENTS_READ, PAYOUTS_READ,
    ORDERS_READ and MERCHANT_PROFILE_READ (locations). Nothing that writes."""

    required_fields: tuple[str, ...] = ("access_token", "environment")

    def bind(self, credentials: dict[str, str]) -> httpx.Auth:
        self.validate(credentials)
        host_for(credentials)
        return _SquareAuth(credentials["access_token"].strip())
