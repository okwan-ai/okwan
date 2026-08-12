"""Auth adapters — pluggable credential strategies for connectors.

An AuthAdapter declares which credential fields it needs and how to
apply them to outbound requests. Adapters are stateless; `bind()`
returns a per-call httpx auth object.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from .errors import CredentialError


@dataclass(frozen=True, slots=True)
class AuthAdapter:
    """Base adapter. Subclasses define `required_fields` and `bind`."""

    required_fields: tuple[str, ...] = ()

    def validate(self, credentials: dict[str, str]) -> None:
        missing = [f for f in self.required_fields if not credentials.get(f)]
        if missing:
            raise CredentialError(f"missing credential fields: {', '.join(missing)}")

    def bind(self, credentials: dict[str, str]) -> httpx.Auth:  # pragma: no cover
        raise NotImplementedError


class _BearerAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


@dataclass(frozen=True, slots=True)
class BearerTokenAuth(AuthAdapter):
    """Static bearer token (WhatsApp Cloud API, Stripe, Notion...)."""

    required_fields: tuple[str, ...] = ("access_token",)

    def bind(self, credentials: dict[str, str]) -> httpx.Auth:
        self.validate(credentials)
        return _BearerAuth(credentials["access_token"])


class _HeaderKeyAuth(httpx.Auth):
    def __init__(self, header: str, value: str) -> None:
        self._header, self._value = header, value

    def auth_flow(self, request: httpx.Request):
        request.headers[self._header] = self._value
        yield request


@dataclass(frozen=True, slots=True)
class ApiKeyAuth(AuthAdapter):
    """API key in a configurable header (Airtable legacy, Paystack...)."""

    header: str = "X-API-Key"
    required_fields: tuple[str, ...] = ("api_key",)

    def bind(self, credentials: dict[str, str]) -> httpx.Auth:
        self.validate(credentials)
        return _HeaderKeyAuth(self.header, credentials["api_key"])
