"""Redaction shared by every surface that shows or stores an upstream error.

Some rails quote the key they rejected; a connection error can carry a
DSN. A stored value must not leave through an error message any more
than through a success, whether the message is shown to an operator,
returned to an agent, or written into run history.
"""
from __future__ import annotations

from okwan_core import all_connectors

#: Stored errors and shown errors are cut here; a rail's body can run to
#: thousands of characters and the first 300 say what happened.
ERROR_CHARS = 300


def scrub(text: str, secrets: list[str]) -> str:
    """Replace every stored value in `text`, longest first so a value that
    extends a shorter one never shows its tail, then cut to ERROR_CHARS."""
    for value in sorted(secrets, key=len, reverse=True):
        if len(value) >= 4:
            text = text.replace(value, "[redacted]")
    return text[:ERROR_CHARS]


def secrets_of(resolver) -> list[str]:
    """Every stored value a run could have sent upstream, from the resolver
    the run used, so no second read of the vault."""
    return [
        v for c in all_connectors()
        for v in resolver(c.name, tuple(c.auth.required_fields)).values() if v
    ]
