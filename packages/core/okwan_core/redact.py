"""Keep credentials out of upstream error text, where it is made.

Some rails quote the key they rejected ("Invalid API key provided:
sk_live_…"). An error body then leaves Okwan through whatever surface
shows it: a REST response, a SQL error, an MCP tool result, a stored run.
Redacting at each surface missed some (found by the red team, 2026-11-10:
a read-only key read back a merchant's stored Stripe key through the
connector REST route and `/v1/query`). So the text is cleaned once, where
an upstream body becomes an `UpstreamError`, before any surface can see
it: of every value the request carried as a credential, and of every
stored credential value of the connection.
"""
from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable

import httpx

REDACTED = "[redacted]"
#: Shorter than this, a value is too likely to be ordinary text to replace.
MIN_SECRET = 4
#: Request headers and query parameters that carry a credential, by name.
_SECRET_NAME = re.compile(r"auth|token|key|secret|signature|password|passwd|cookie|session", re.IGNORECASE)


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every secret in `text`, longest first so a value that
    extends a shorter one never shows its tail."""
    for value in sorted({s for s in secrets if s and len(s) >= MIN_SECRET}, key=len, reverse=True):
        text = text.replace(value, REDACTED)
    return text


def sent_secrets(request: httpx.Request) -> list[str]:
    """Every credential a request carried: the value of each header or
    query parameter whose name says it holds one, the token after an
    Authorization scheme, and both halves of a Basic pair."""
    values: list[str] = []
    for name, value in request.headers.items():
        if not _SECRET_NAME.search(name):
            continue
        values.append(value)
        scheme, _, rest = value.partition(" ")
        rest = rest.strip()
        if not rest:
            continue
        values.append(rest)
        if scheme.lower() == "basic":
            try:
                pair = base64.b64decode(rest, validate=True).decode()
            except (binascii.Error, UnicodeDecodeError):
                continue
            values += [pair, *pair.split(":", 1)]
    values += [v for k, v in request.url.params.multi_items() if _SECRET_NAME.search(k)]
    return values
