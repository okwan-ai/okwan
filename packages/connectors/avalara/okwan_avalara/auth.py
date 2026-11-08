"""Avalara auth: an account ID and license key, the company, the environment.

AvaTax takes HTTP Basic auth with the account ID as the user name and the
license key as the password, and asks every caller to name itself in an
`X-Avalara-Client` header. A license key is the account's, not a user's:
it can read every company in the account, so the company to read is a
stored field beside it, and it goes into the URL path, so it is checked
against a safe shape before anything is built from it.

`environment` chooses the host: `production` (rest.avatax.com, where a
free trial also lives) or `sandbox` (sandbox-rest.avatax.com). Credentials
from one never work on the other, and the failure is a 401 that reads like
a bad key. A license key also takes a few minutes to start working after
it is created.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass

import httpx
from okwan_core import AuthAdapter
from okwan_core.errors import CredentialError

HOSTS = {
    "production": "https://rest.avatax.com",
    "sandbox": "https://sandbox-rest.avatax.com",
}
CLIENT = "Okwan; 1.0; Okwan; okwan-avalara"
_ACCOUNT_ID = re.compile(r"\d{4,20}")
_COMPANY_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,49}")


def host_for(credentials: dict[str, str]) -> str:
    env = (credentials.get("environment") or "").strip().lower()
    if env not in HOSTS:
        raise CredentialError("avalara.environment must be 'production' or 'sandbox'")
    return HOSTS[env]


def company_code(credentials: dict[str, str]) -> str:
    code = (credentials.get("company_code") or "").strip()
    if not _COMPANY_CODE.fullmatch(code):
        raise CredentialError(
            "avalara.company_code must be the company code from AvaTax (letters, digits, - _ .)")
    return code


class _AvalaraAuth(httpx.Auth):
    def __init__(self, account_id: str, license_key: str) -> None:
        pair = f"{account_id}:{license_key}".encode()
        self._header = "Basic " + base64.b64encode(pair).decode()

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = self._header
        request.headers["X-Avalara-Client"] = CLIENT
        yield request


@dataclass(frozen=True, slots=True)
class AvalaraAuth(AuthAdapter):
    """The account's ID and a license key. AvaTax keys carry no scopes, so
    read-only is Okwan's: every operation here is a list."""

    required_fields: tuple[str, ...] = ("account_id", "license_key", "company_code", "environment")

    def bind(self, credentials: dict[str, str]) -> httpx.Auth:
        self.validate(credentials)
        host_for(credentials)
        company_code(credentials)
        account = credentials["account_id"].strip()
        if not _ACCOUNT_ID.fullmatch(account):
            raise CredentialError("avalara.account_id must be the account's number")
        return _AvalaraAuth(account, credentials["license_key"].strip())
