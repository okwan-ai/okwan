"""Self-serve accounts: an email address that owns one root tenant.

Three records, each a different stage of trust:

* a **signup** is a claim — an address, a password hash and a token that
  only the address's inbox has seen. It owns nothing.
* an **account** exists once the token comes back with the password that
  created it. Verification creates the root tenant in the same step, so
  there is never a tenant without an owner or an owner without a tenant.
* a **session** is what the dashboard holds after sign-in. It is a
  separate token type from an API key (`oks_` against `okw_`) so the two
  can be revoked, expired and scoped independently.

The password travels with the signup, not the address. Verification
checks the password against the signup that minted the token, so someone
who registers a victim's address first cannot have the victim click
through to an account whose password the attacker set.

scrypt rather than SHA-256 here, unlike API keys: these are user-chosen
passwords, and a slow, memory-hard hash is the point.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

SESSION_PREFIX = "oks"
VERIFY_PREFIX = "okv"
SIGNUP_TTL = timedelta(hours=24)
SESSION_TTL = timedelta(days=7)
MIN_PASSWORD = 12
MAX_PASSWORD = 256
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class AccountRefused(ValueError):
    """An operator-created login that would break one of the account rules:
    an existing root tenant, one account per tenant, one per address."""

# n=2**14, r=8 costs 16 MiB and tens of milliseconds per hash: noticeable
# to an attacker running millions, not to a person signing in.
_N, _R, _P = 2**14, 8, 1


def normalize_email(email: str) -> str:
    """One tenant per address needs one spelling per address.

    Case-folds the whole address. The local part is technically
    case-sensitive, but no mainstream provider treats it so, and two
    accounts differing only in case is the failure that matters.
    """
    return email.strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return "$".join([
        "scrypt", str(_N), str(_R), str(_P),
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    ])


def check_password(password: str, stored: str | None) -> bool:
    """Constant work whether or not an account exists.

    Called with `stored=None` for an unknown address so a sign-in attempt
    takes the same time either way; otherwise response timing tells an
    attacker which addresses have accounts.
    """
    if stored is None:
        hash_password(password)
        return False
    _, n, r, p, salt, digest = stored.split("$")
    candidate = hashlib.scrypt(
        password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p)
    )
    return secrets.compare_digest(candidate, base64.b64decode(digest))


def new_token(prefix: str) -> tuple[str, str]:
    """Return (full_token, sha256_hex). Store only the hash.

    SHA-256 is right for these, as for API keys: 256-bit random tokens
    have nothing to brute force.
    """
    full = f"{prefix}_{secrets.token_urlsafe(32)}"
    return full, hash_token(full)


def hash_token(full: str) -> str:
    return hashlib.sha256(full.encode()).hexdigest()


def expires(ttl: timedelta) -> datetime:
    return datetime.now(UTC) + ttl
