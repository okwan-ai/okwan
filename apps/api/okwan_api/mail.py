"""Outbound mail for signup verification.

NOT BUILT: no mail provider is configured, so there is no production
mailer. `get_mailer()` returns one only in dev, where the verification
link goes to the server log. Anywhere else it returns None and signup
answers 503 — a signup that cannot reach the inbox it is verifying must
fail closed, not mint accounts on an unproven address.

Wiring a provider means one class with `send_verification` and a branch
in `get_mailer()`. The link carries a token that creates an account, so
the provider choice is a security decision too: it sees every link.
"""
from __future__ import annotations

import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)


class Mailer(Protocol):
    async def send_verification(self, email: str, link: str) -> None: ...


class DevLogMailer:
    """Writes the link to the log. Dev only: a log is not an inbox."""

    async def send_verification(self, email: str, link: str) -> None:
        logger.warning("dev mailer — verify %s at %s", email, link)


_mailer: Mailer | None = None
_overridden = False


def get_mailer() -> Mailer | None:
    if _overridden:
        return _mailer
    if os.environ.get("OKWAN_ENV", "dev") == "dev":
        return DevLogMailer()
    return None


def set_mailer(mailer: Mailer | None) -> None:
    """Injection point for tests and for a real provider."""
    global _mailer, _overridden
    _mailer, _overridden = mailer, True


def dashboard_url() -> str:
    return os.environ.get("OKWAN_DASHBOARD_URL", "http://localhost:3000").rstrip("/")
