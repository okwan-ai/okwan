"""Clover auth: a merchant's API token, its merchant ID and its environment.

Clover takes `Authorization: Bearer <token>` and names the merchant in
every path (`/v3/merchants/{mId}/payments`), so the merchant ID is a
stored field beside the token rather than something looked up: a token
is scoped to one merchant, and asking with another merchant's ID is a
401 that reads exactly like a bad token. Keeping both visible makes that
mistake one a person can see.

`environment` chooses the host: `production` (api.clover.com, North
America) or `sandbox` (apisandbox.dev.clover.com). A sandbox token sent
to production, or the reverse, is the same 401.

The merchant ID goes into a URL path, so it is checked against the shape
Clover issues (13 upper-case letters and digits) before anything is
built from it: a stored value can never steer a request to another path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from okwan_core import AuthAdapter
from okwan_core.errors import CredentialError

HOSTS = {
    "production": "https://api.clover.com",
    "sandbox": "https://apisandbox.dev.clover.com",
}
_MERCHANT_ID = re.compile(r"[A-Z0-9]{13}")


def host_for(credentials: dict[str, str]) -> str:
    env = (credentials.get("environment") or "").strip().lower()
    if env not in HOSTS:
        raise CredentialError("clover.environment must be 'production' or 'sandbox'")
    return HOSTS[env]


def merchant_id(credentials: dict[str, str]) -> str:
    mid = (credentials.get("merchant_id") or "").strip().upper()
    if not _MERCHANT_ID.fullmatch(mid):
        raise CredentialError(
            "clover.merchant_id must be the 13-character merchant ID from the Clover dashboard")
    return mid


class _CloverAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


@dataclass(frozen=True, slots=True)
class CloverAuth(AuthAdapter):
    """A merchant API token with read permissions only: Merchant (for the
    merchant's timezone and currency), Orders and Payments. Nothing that
    writes."""

    required_fields: tuple[str, ...] = ("access_token", "merchant_id", "environment")

    def bind(self, credentials: dict[str, str]) -> httpx.Auth:
        self.validate(credentials)
        host_for(credentials)
        merchant_id(credentials)
        return _CloverAuth(credentials["access_token"].strip())
