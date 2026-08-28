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
TOKEN_BYTES = 32


def generate() -> tuple[str, str, str]:
    """Return (full_key, public_prefix, hash_hex). Store only the last two."""
    body = secrets.token_urlsafe(TOKEN_BYTES)
    full = f"{PREFIX}_{body}"
    return full, full[: len(PREFIX) + 9], hash_key(full)


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()


def matches(full_key: str, hash_hex: str) -> bool:
    return secrets.compare_digest(hash_key(full_key), hash_hex)
