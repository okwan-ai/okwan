"""Outbound mail for signup verification.

Resend sends it when `RESEND_API_KEY` and `OKWAN_MAIL_FROM` are both set.
Without them, dev writes the link to the server log and every other
environment has no mailer, so signup answers 503: a signup that cannot
reach the inbox it is verifying must fail closed, not mint accounts on an
unproven address. Outside dev there is never a fallback to logging links.

The link carries a token that creates an account, so the provider sees
every one, which makes the provider a security choice as well as a vendor
one:

* **Click and open tracking must be off.** A provider that rewrites links
  routes every verification token through its own servers. Resend has no
  per-message switch for either: tracking is a property of the sending
  domain (`PATCH /domains/{id}` with `click_tracking` and `open_tracking`),
  so it is turned off when the domain is set up (§10 item 15). The message
  below carries only from, to, subject, text and html, nothing that could
  turn tracking on.
* **Nothing here logs the token or the link.** A failed send is logged by
  HTTP status and Resend's error name only; the error message can quote
  the request.
* **A failed send is invisible to the caller.** Signup answers the same
  way for a registered address, which sends nothing, so a send runs after
  the response and its failure is logged, never raised into the request.
"""
from __future__ import annotations

import html
import logging
import os
import re
from typing import Protocol

import httpx
from okwan_vault import accounts

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
SEND_TIMEOUT = 10.0


class Mailer(Protocol):
    async def send_verification(self, email: str, link: str) -> None: ...
    async def send_notice(
        self, to: str, subject: str, text: str, html: str | None = None,
        reply_to: str | None = None, headers: dict[str, str] | None = None,
    ) -> None: ...


class MailNotSent(Exception):
    """A send the provider did not accept.

    The message names the status and the provider's error name only, never
    the link, the token or the address, so it is safe to log.
    """


def verification_message(link: str) -> tuple[str, str, str]:
    """Subject, plain text and HTML for one verification link."""
    hours = int(accounts.SIGNUP_TTL.total_seconds() // 3600)
    subject = "Verify your email for Okwan"
    text = (
        "Verify your email address to finish creating your Okwan account:\n"
        "\n"
        f"{link}\n"
        "\n"
        f"This link expires in {hours} hours.\n"
        "\n"
        "If you didn't sign up for Okwan, you can ignore this email.\n"
    )
    href = html.escape(link, quote=True)
    body = (
        "<!doctype html>\n"
        '<html><body style="font-family: sans-serif; line-height: 1.5; color: #111111">\n'
        "<p>Verify your email address to finish creating your Okwan account:</p>\n"
        f'<p><a href="{href}">Verify your email</a></p>\n'
        f"<p>Or paste this link into your browser:<br>{href}</p>\n"
        f"<p>This link expires in {hours} hours.</p>\n"
        "<p>If you didn't sign up for Okwan, you can ignore this email.</p>\n"
        "</body></html>\n"
    )
    return subject, text, body


def _error_name(response: httpx.Response) -> str:
    """Resend's error name (`validation_error`), or "unknown". Never the message."""
    try:
        name = response.json().get("name")
    except (ValueError, AttributeError):
        return "unknown"
    if isinstance(name, str) and re.fullmatch(r"[a-z_]{1,64}", name):
        return name
    return "unknown"


class ResendMailer:
    """Verification mail through Resend's send-email API.

    `transport` is the seam tests use; production leaves it None.
    """

    def __init__(
        self, api_key: str, sender: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._api_key = api_key
        self._sender = sender
        self._transport = transport

    def __repr__(self) -> str:
        return f"ResendMailer(sender={self._sender!r})"

    async def send_verification(self, email: str, link: str) -> None:
        subject, text, body = verification_message(link)
        payload = {
            "from": self._sender,
            "to": [email],
            "subject": subject,
            "text": text,
            "html": body,
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=SEND_TIMEOUT
            ) as client:
                response = await client.post(
                    RESEND_ENDPOINT,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError as e:
            raise MailNotSent(f"resend unreachable: {type(e).__name__}") from None
        if not response.is_success:
            raise MailNotSent(
                f"resend answered {response.status_code} {_error_name(response)}"
            )

    async def send_notice(
        self, to: str, subject: str, text: str, html: str | None = None,
        reply_to: str | None = None, headers: dict[str, str] | None = None,
    ) -> None:
        """A message that is not a verification link: a support escalation
        to the operator, a person's reply to a customer, or the daily
        check's findings. Plain text, and HTML when given. `headers` are
        mail headers (List-Unsubscribe). Tracking is off on the domain."""
        payload: dict[str, object] = {"from": self._sender, "to": [to], "subject": subject, "text": text}
        if html is not None:
            payload["html"] = html
        if reply_to:
            payload["reply_to"] = [reply_to]
        if headers:
            payload["headers"] = dict(headers)
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=SEND_TIMEOUT
            ) as client:
                response = await client.post(
                    RESEND_ENDPOINT,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError as e:
            raise MailNotSent(f"resend unreachable: {type(e).__name__}") from None
        if not response.is_success:
            raise MailNotSent(
                f"resend answered {response.status_code} {_error_name(response)}"
            )


class DevLogMailer:
    """Writes the link to the log. Dev only: a log is not an inbox."""

    async def send_verification(self, email: str, link: str) -> None:
        logger.warning("dev mailer — verify %s at %s", email, link)

    async def send_notice(
        self, to: str, subject: str, text: str, html: str | None = None,
        reply_to: str | None = None, headers: dict[str, str] | None = None,
    ) -> None:
        logger.warning("dev mailer — notice to %s: %s", to, subject)


async def deliver_verification(mailer: Mailer, email: str, link: str) -> None:
    """Send one verification mail and keep any failure out of the request.

    Runs after the signup response. A failure is logged without the link,
    the token or the address: `MailNotSent` already says only the status
    and the provider's error name, and anything else is named by type.
    """
    try:
        await mailer.send_verification(email, link)
    except MailNotSent as e:
        logger.error("verification mail not sent: %s", e)
    except Exception as e:  # noqa: BLE001 — any other message might hold the link
        logger.error("verification mail not sent: %s", type(e).__name__)


_mailer: Mailer | None = None
_overridden = False


def get_mailer() -> Mailer | None:
    """Resend when configured; the log in dev; otherwise None, and signup 503s."""
    if _overridden:
        return _mailer
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    sender = os.environ.get("OKWAN_MAIL_FROM", "").strip()
    if api_key and sender:
        return ResendMailer(api_key, sender)
    if os.environ.get("OKWAN_ENV", "dev") == "dev":
        return DevLogMailer()
    return None


def set_mailer(mailer: Mailer | None) -> None:
    """Injection point for tests."""
    global _mailer, _overridden
    _mailer, _overridden = mailer, True


def dashboard_url() -> str:
    return os.environ.get("OKWAN_DASHBOARD_URL", "http://localhost:3000").rstrip("/")


def postal_address() -> str:
    """The sender's name and postal address for the foot of a mail Okwan
    sends unasked (the daily check's findings): `OKWAN_MAIL_POSTAL_ADDRESS`,
    one line. US law requires it on such mail, so none goes out without it."""
    return " ".join(os.environ.get("OKWAN_MAIL_POSTAL_ADDRESS", "").split())
