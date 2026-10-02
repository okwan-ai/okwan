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
import logging
import math
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request

MAX_KEYS = 100_000


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


class Limiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[tuple[str, str], tuple[float, int]] = {}

    def take(self, *checks: tuple[Rule, str]) -> float | None:
        """Count one attempt against every check, or none of them.

        Returns seconds until the earliest refusing window resets, or None
        when the attempt is allowed. A refused attempt is not counted, so a
        client that backs off is not punished for the refusal itself.
        """
        now = self._clock()
        current: list[tuple[tuple[str, str], float, int]] = []
        wait = 0.0
        for rule, subject in checks:
            key = (rule.name, subject)
            start, n = self._hits.get(key, (now, 0))
            if now - start >= rule.window:
                start, n = now, 0
            if n >= rule.limit:
                wait = max(wait, start + rule.window - now)
            current.append((key, start, n))
        if wait:
            return wait
        for key, start, n in current:
            self._hits[key] = (start, n + 1)
        if len(self._hits) > MAX_KEYS:
            self._sweep(now)
        return None

    def _sweep(self, now: float) -> None:
        """Bound memory under a flood of distinct keys."""
        windows = {r.name: r.window for r in _RULES}
        self._hits = {
            k: v for k, v in self._hits.items()
            if now - v[0] < windows.get(k[0], 0)
        }
        if len(self._hits) > MAX_KEYS:
            oldest = sorted(self._hits.items(), key=lambda kv: kv[1][0])
            self._hits = dict(oldest[len(oldest) // 2:])

    def reset(self) -> None:
        self._hits.clear()


_RULES = (SIGNUP_IP, SIGNUP_ADDRESS, VERIFY_IP, SIGNIN_IP, SIGNIN_ADDRESS,
          TEST_IP, TEST_TENANT)

limiter = Limiter()


def client_ip(request: Request) -> str:
    secret = os.environ.get("OKWAN_DASHBOARD_SECRET", "")
    forwarded = request.headers.get("x-okwan-client-ip", "").strip()
    presented = request.headers.get("x-okwan-dashboard-secret", "")
    # Bytes, not str: compare_digest raises on non-ASCII str, and header
    # values arrive latin-1 decoded, so a crafted header would be a 500.
    matched = bool(secret) and secrets.compare_digest(
        presented.encode("latin-1"), secret.encode("latin-1", "replace")
    )
    # Cloudflare sets CF-Connecting-IP to the address it accepted the
    # connection from, overwriting anything the client sent. Trusting it
    # assumes every request reaches Render through Cloudflare, which is the
    # same assumption that makes any X-Forwarded-For entry trustworthy.
    cloudflare = _address(request.headers.get("cf-connecting-ip", ""))
    # Fallback when Cloudflare's header is absent (local runs): count from
    # the right, because a client's own entries land to the left of what a
    # trusted proxy appends. One hop errs toward a shared bucket, which is
    # coarse but unforgeable; too many hops would read a forged entry.
    hops = int(os.environ.get("OKWAN_TRUSTED_PROXY_HOPS", "1"))
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")
             if p.strip()]
    if matched and forwarded:
        chosen, via = forwarded, "dashboard"
    elif cloudflare:
        chosen, via = cloudflare, "cloudflare"
    elif hops and len(chain) >= hops:
        chosen, via = chain[-hops], "chain"
    else:
        chosen, via = (request.client.host if request.client else "unknown"), "peer"
    if os.environ.get("OKWAN_LOG_FORWARDED") == "1":
        _log_forwarded(chain, bool(forwarded), matched, via, chosen)
    return chosen


def _address(value: str) -> str:
    """A well-formed IP address, or empty. Anything else is not trusted."""
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return ""


# TEMPORARY DIAGNOSTIC — remove once the Render hop is confirmed (§10 item 1).
# Logs exactly four things: the X-Forwarded-For chain, whether
# X-Okwan-Client-IP was present (not its value), whether the dashboard
# secret matched (never the secret), and the address chosen. Nothing else
# from the request. Its own handler, because uvicorn leaves the root
# logger at WARNING and an INFO record from this module would be dropped.
_forwarded_log: logging.Logger | None = None


def _log_forwarded(chain: list[str], header_present: bool, matched: bool,
                   via: str, chosen: str) -> None:
    global _forwarded_log
    if _forwarded_log is None:
        log = logging.getLogger("okwan_api.forwarded")
        log.setLevel(logging.INFO)
        if not log.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
            log.addHandler(handler)
        log.propagate = False
        _forwarded_log = log
    # %r on the chain: header values are client-written, and repr keeps
    # one request on one log line whatever they contain.
    _forwarded_log.info(
        "x-forwarded-for=%r x-okwan-client-ip=%s dashboard-secret=%s via=%s chose=%r",
        chain, "present" if header_present else "absent",
        "matched" if matched else "not-matched", via, chosen,
    )


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
