"""Limits on the routes that cost the server more than they cost a caller.

Sign-in and verification run scrypt, which is slow on purpose; the same
slowness that makes guessing expensive makes a flood of guesses a way to
occupy the one uvicorn worker. Signup sends mail. The credential test
makes a real upstream call per click. Each is limited per client IP and
per subject (the address, or the tenant), so neither rotating addresses
from one IP nor rotating IPs against one address gets far.

Fixed windows, in memory, per instance. Render runs one instance today;
more instances multiply the effective limit by their count, which is
acceptable for abuse control and not acceptable for billing — metering
stays in the vault.

Behind the dashboard every request arrives from the dashboard's own
address, so the dashboard forwards the browser's IP in
`X-Okwan-Client-IP`. That header is believed only alongside
`X-Okwan-Dashboard-Secret` matching OKWAN_DASHBOARD_SECRET; from anyone
else it would be a way to pick a fresh IP per request.
"""
from __future__ import annotations

import asyncio
import ipaddress
import math
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastapi import HTTPException, Request

#: Counters one rule keeps. A flood of fresh subjects on one rule (a new
#: address on every request) makes room among that rule's own counters,
#: the expired ones first and then its oldest, and never touches another
#: rule's: a burst of addresses at the unsubscribe link cannot give an
#: account back an allowance it spent somewhere else. Each rule's counters
#: are bounded, so the limiter's memory is bounded by the rules it has.
MAX_KEYS = 20_000


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    limit: int
    window: float  # seconds


SIGNUP_IP = Rule("signup:ip", 10, 3600)
SIGNUP_ADDRESS = Rule("signup:address", 3, 3600)
VERIFY_IP = Rule("verify:ip", 20, 900)
SIGNIN_IP = Rule("signin:ip", 20, 900)
SIGNIN_ADDRESS = Rule("signin:address", 10, 900)
TEST_IP = Rule("test:ip", 30, 600)
TEST_TENANT = Rule("test:tenant", 20, 600)
#: The support assistant: each message is a model call that costs money.
SUPPORT_IP = Rule("support:ip", 60, 3600)
SUPPORT_TENANT = Rule("support:tenant", 40, 3600)
#: Live reconciliation runs through SQL (`recon.*`): each reads every
#: system it reconciles, up to 5,000 records a side. A reused result does
#: not count (recon_reuse.py).
SQL_RECON_TENANT = Rule("sql:recon:tenant", 12, 60)
#: File imports per target tenant: each one parses a file of up to 8 MB
#: and writes up to 50,000 rows.
IMPORT_TENANT = Rule("import:tenant", 30, 3600)
#: The list of open items: a session reloading a page, never a loop.
EXCEPTIONS_TENANT = Rule("exceptions:tenant", 60, 60)
#: Reads of the open items as data (REST, SQL, MCP) by one account: each reads
#: stored rows of every merchant it can open.
OPEN_ITEMS_TENANT = Rule("open-items:tenant", 30, 60)
#: Decisions on open items. Each caller (a session's or a key's account) may
#: send 300 an hour, recorded or not; an account and its merchants together
#: record at most 1,000 an hour, charged only for a decision about to be
#: recorded, so requests that record nothing never spend another's share.
DECIDE_CALLER = Rule("decide:caller", 300, 3600)
DECIDE_TENANT = Rule("decide:tenant", 1000, 3600)
#: Suggestions on open items: each one asked for is a model call that costs
#: money. A caller may ask 30 times an hour, and make at most 20 model calls
#: on one item a day, kept or not. What an account and each of its merchants
#: may spend in a day is counted in the database from the calls metered
#: (okwan_api.suggestions), so neither a restart nor a flood gives it back.
SUGGEST_CALLER = Rule("suggest:caller", 30, 3600)
SUGGEST_ITEM = Rule("suggest:item", 20, 86400)
#: The sample check holds nothing of anyone's and costs one run an hour;
#: the limit only keeps one address from hammering a route that needs no key.
SAMPLE_IP = Rule("sample:ip", 120, 60)
#: The unsubscribe link takes a token and no sign-in; this keeps one address
#: from guessing at tokens (each is 24 random bytes) at any speed.
UNSUBSCRIBE_IP = Rule("unsubscribe:ip", 30, 900)
#: Opening Stripe's checkout or billing portal. Each is a request made with
#: Okwan's own Stripe key, and a checkout is a row as well; a person needs
#: one or two. Counted before Stripe is asked, so a burst and a call
#: Stripe refuses both count.
BILLING_TENANT = Rule("billing:tenant", 10, 3600)
#: Operator console unlock: a wrong authenticator code is a guess.
OPS_UNLOCK = Rule("ops:unlock", 5, 900)
OPS_UNLOCK_IP = Rule("ops:unlock:ip", 10, 900)


class Limiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[tuple[str, str], tuple[float, int]] = {}
        #: Counters held, by rule.
        self._held: dict[str, int] = {}

    def take(self, *checks: tuple[Rule, str], cost: int = 1) -> float | None:
        """Count `cost` attempts against every check, or none of them.

        Returns seconds until the earliest refusing window resets, or None
        when the attempt is allowed. A refused attempt is not counted, so a
        client that backs off is not punished for the refusal itself. A
        cost above one is one request that does several units of work (a
        query that runs two reconciliations).
        """
        now = self._clock()
        current: list[tuple[tuple[str, str], float, int]] = []
        wait = 0.0
        for rule, subject in checks:
            key = (rule.name, subject)
            start, n = self._hits.get(key, (now, 0))
            if now - start >= rule.window:
                start, n = now, 0
            if n + cost > rule.limit:
                wait = max(wait, start + rule.window - now)
            current.append((key, start, n))
        if wait:
            return wait
        for key, start, n in current:
            if key not in self._hits:
                self._held[key[0]] = self._held.get(key[0], 0) + 1
            self._hits[key] = (start, n + cost)
        for rule, _ in checks:
            if self._held.get(rule.name, 0) > MAX_KEYS:
                self._make_room(rule, now)
        return None

    def _make_room(self, rule: Rule, now: float) -> None:
        """Bound one rule's counters under a flood of fresh subjects: its
        expired counters go, and, while it still holds more than MAX_KEYS,
        its oldest, down to half. No other rule's counter is touched."""
        mine = [(k, v) for k, v in self._hits.items() if k[0] == rule.name]
        live = [(k, v) for k, v in mine if now - v[0] < rule.window]
        if len(live) > MAX_KEYS:
            live = sorted(live, key=lambda kv: kv[1][0])[-(MAX_KEYS // 2):]
        for k, _ in mine:
            del self._hits[k]
        self._hits.update(live)
        self._held[rule.name] = len(live)

    def reset(self) -> None:
        self._hits.clear()
        self._held.clear()


limiter = Limiter()


def client_ip(request: Request) -> str:
    return address_from(request.headers, request.client.host if request.client else None)


def address_from(headers: Mapping[str, str], peer: str | None) -> str:
    """The caller's address from a request's headers and its TCP peer.

    `client_ip` for a FastAPI request; the hosted MCP server, which sees
    the headers on its own context, calls this directly so both name the
    same address for the same caller.
    """
    secret = os.environ.get("OKWAN_DASHBOARD_SECRET", "")
    forwarded = headers.get("x-okwan-client-ip", "").strip()
    presented = headers.get("x-okwan-dashboard-secret", "")
    # Bytes, not str: compare_digest raises on non-ASCII str, and header
    # values arrive latin-1 decoded, so a crafted header would be a 500.
    matched = bool(secret) and secrets.compare_digest(
        presented.encode("latin-1"), secret.encode("latin-1", "replace")
    )
    # Cloudflare sets CF-Connecting-IP to the address it accepted the
    # connection from, overwriting anything the client sent. Trusting it
    # assumes every request reaches Render through Cloudflare, which is the
    # same assumption that makes any X-Forwarded-For entry trustworthy.
    cloudflare = as_address(headers.get("cf-connecting-ip", ""))
    # Fallback when Cloudflare's header is absent (local runs): count from
    # the right, because a client's own entries land to the left of what a
    # trusted proxy appends. One hop errs toward a shared bucket, which is
    # coarse but unforgeable; too many hops would read a forged entry.
    hops = int(os.environ.get("OKWAN_TRUSTED_PROXY_HOPS", "1"))
    chain = [p.strip() for p in headers.get("x-forwarded-for", "").split(",")
             if p.strip()]
    # Whichever header it comes from, what is recorded as the caller's
    # address has to read as one: it is kept in the access log and can be
    # sent to a bank as evidence.
    if matched and as_address(forwarded):
        return as_address(forwarded)
    if cloudflare:
        return cloudflare
    if hops and len(chain) >= hops and as_address(chain[-hops]):
        return as_address(chain[-hops])
    return peer or "unknown"


def as_address(value: str) -> str:
    """A well-formed IP address in its one canonical spelling, or empty.
    Anything else is not trusted. An IPv6 zone ("fe80::1%eth0") is refused
    outright: what follows the percent sign is free text, spaces and
    sentences included, and no caller across the internet has one."""
    value = str(value).strip()
    if "%" in value:
        return ""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def enforce(request: Request, *checks: tuple[Rule, str]) -> None:
    """429 with Retry-After. One message for every rule, so a limit on an
    address says nothing about whether the address is registered."""
    wait = limiter.take(*((rule, s) for rule, s in checks))
    if wait is not None:
        seconds = math.ceil(wait)
        raise HTTPException(
            429,
            f"too many attempts — try again in {math.ceil(seconds / 60)} min",
            headers={"Retry-After": str(seconds)},
        )


# One scrypt hash is ~50 ms and 16 MiB, and hashlib releases the GIL while
# it runs. Hashing in a thread keeps the event loop serving other requests;
# a thread-side cap of two makes a burst queue rather than exhaust memory.
# Threading, not asyncio, so the cap is not bound to one event loop.
_HASHING = threading.BoundedSemaphore(2)


def _capped[T](fn: Callable[..., T], *args) -> T:
    with _HASHING:
        return fn(*args)


async def off_loop[T](fn: Callable[..., T], *args) -> T:
    return await asyncio.to_thread(_capped, fn, *args)
