"""API key issuance and verification.

The key is shown once at creation and never stored. What is stored is a
SHA-256 hash plus a short public prefix, so a leaked database yields no
usable keys and support can still identify which key a customer means.

SHA-256 rather than a password hash on purpose: these are 256-bit random
tokens, not user-chosen passwords, so there is nothing to brute force and
verification stays fast enough to run on every request.
"""
from __future__ import annotations

import hashlib
import secrets

PREFIX = "okw"
#: What a sandbox's keys start with, so a person and their code can tell a
#: test key from a live one at a glance. The tenant decides which a key is,
#: never the caller: a sandbox's keys are always test keys and a live
#: tenant's never are.
TEST = f"{PREFIX}_test_"
TOKEN_BYTES = 32


def generate(*, test: bool = False) -> tuple[str, str, str]:
    """Return (full_key, public_prefix, hash_hex). Store only the last two."""
    body = secrets.token_urlsafe(TOKEN_BYTES)
    full = f"{TEST if test else PREFIX + '_'}{body}"
    return full, public_prefix(full), hash_key(full)


def is_test(full_key: str) -> bool:
    return full_key.startswith(TEST)


def public_prefix(full_key: str) -> str:
    """The part of a key that is stored and shown, so a person can tell
    their keys apart: `okw_` (or `okw_test_`) and eight characters, never
    enough to use."""
    return full_key[: (len(TEST) if is_test(full_key) else len(PREFIX) + 1) + 8]


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()


def matches(full_key: str, hash_hex: str) -> bool:
    return secrets.compare_digest(hash_key(full_key), hash_hex)
