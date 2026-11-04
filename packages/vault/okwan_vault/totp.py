"""Time-based one-time codes (RFC 6238), the operator console's second factor.

Six digits, a 30-second step, SHA-1: what every authenticator app (Google
Authenticator, Microsoft Authenticator, 1Password, Authy) reads from a setup
key. Standard library only.

`verify` accepts the step before and after the current one, for a phone
clock a little off, and refuses a step already used, so a code seen over a
shoulder cannot be replayed inside its window.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP = 30
DIGITS = 6
ISSUER = "Okwan"


def new_secret() -> str:
    """A 160-bit secret in base32, the form authenticator apps take."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at(secret: str, step: int, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def current_step(at: float | None = None) -> int:
    return int((time.time() if at is None else at) // STEP)


def matching_step(secret: str, code: str, at: float | None = None, window: int = 1) -> int | None:
    """The step `code` belongs to, within `window` steps of now, or None."""
    code = "".join(code.split())
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    now = current_step(at)
    found = None
    for step in range(now - window, now + window + 1):
        # Compare every candidate, so timing says nothing about which matched.
        if hmac.compare_digest(code_at(secret, step), code):
            found = step
    return found


class ReplayGuard:
    """The last step accepted per account, so a code works once."""

    def __init__(self) -> None:
        self._last: dict[str, int] = {}

    def verify(self, who: str, secret: str, code: str, at: float | None = None) -> bool:
        step = matching_step(secret, code, at)
        if step is None or step <= self._last.get(who, -1):
            return False
        self._last[who] = step
        return True


def setup_uri(secret: str, account: str) -> str:
    """otpauth:// for an authenticator app that reads one."""
    label = quote(f"{ISSUER}:{account}")
    query = urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": STEP})
    return f"otpauth://totp/{label}?{query}"


def grouped(secret: str) -> str:
    """The setup key in groups of four, easier to type into a phone."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
