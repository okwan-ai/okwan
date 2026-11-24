"""Okwan's API versions: each one the day it took effect, and what changed.

An integration is written against the API as it was on some day. A change
that would break code written that way (a field renamed or removed, a type
or a meaning changed, a value no longer given, an event's body reshaped) is
released as a new version, dated the day it ships. An addition (a route, a
field, an event type, an optional parameter) is not a new version: it
reaches everyone, and code that reads Okwan ignores what it does not know.

Each account is answered in its own version: the newest one when the
account was made, and a merchant in the version of the account that made
it. A request names another with the `Okwan-Version` header; every answer
under /v1/ says in the same header which version answered. A webhook
address is on its account's version, unless it was added on another.

A version is named by a day, and a day names the version in effect on it:
the newest on or before it (`in_effect`). So `2026-11-30` is answered as
`2026-11-24` until a version dated on or before the 30th exists, and a
client that names a version this release does not have yet (it is ahead:
a deploy under way, or a rollback) is answered in this release's newest,
and told so, rather than refused. Only a value that is not a day, or a
day before the first version, names nothing. An account made outside the
API (the operator's command line) is stamped with the day it is made,
for the same reason: the checkout it runs from may not be what is
deployed.

An answer is always made in the newest shape, then given back in an older
version by undoing, newest first, each change made since (`Change.back`).
So an older version is never a second copy of the code: it is the list
below, and the one function per change that puts the old shape back.
A version reshapes what is answered, never who may call a route, what it
checks or what it may read: those are the same in every version.

Read-only and pure: no I/O, so the vault (which stamps a new account) and
the API (which answers in a version) read the same list.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

#: The request and response header.
HEADER = "Okwan-Version"

#: The first version: the API as it was before versions were named. Rows
#: made before then (accounts, webhook addresses) are on it, and the
#: schema's column default says the same date (schema.sql).
FIRST = "2026-11-24"

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ROUTE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) /v1/\S*$")


@dataclass(frozen=True)
class Change:
    """One breaking change. `what` is the sentence the changelog shows.
    `routes` are the routes whose successful JSON answers it alters, each
    a method and the path as mounted (``"GET /v1/exceptions"``,
    ``"GET /v1/tenants/{tenant_id}/exceptions"``), and `events` the event
    types whose bodies it alters.
    `back` takes a body in the shape after the change (a route's whole
    answer, or an event's whole body) and returns it as it was before; it
    may change the value it is given."""

    what: str
    back: Callable[[Any], Any]
    routes: frozenset[str] = frozenset()
    events: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Version:
    """A version: the day it took effect (YYYY-MM-DD, UTC), what it is, and
    the breaking changes it made to the version before it."""

    date: str
    what: str
    changes: tuple[Change, ...] = ()


#: Every version, oldest first. A new one goes at the end, dated the day
#: it is released, with its changes (docs/ops/RUNBOOK.md, "API versions").
VERSIONS: tuple[Version, ...] = (
    Version(FIRST, "The first dated version: the API as it was on this day."),
)


def dates() -> tuple[str, ...]:
    """Every version's date, oldest first."""
    return tuple(v.date for v in VERSIONS)


def current() -> str:
    """The newest version: what a new account is made on."""
    return VERSIONS[-1].date


def known(value: str) -> bool:
    """Whether `value` is exactly the date of a version."""
    return value in dates()


def in_effect(value: str | None) -> str | None:
    """The version in effect on the day `value` names: the newest version
    on or before it. None when it is not a day (YYYY-MM-DD, a real date),
    or is a day before the first version."""
    if not isinstance(value, str) or not _DATE.match(value):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    on = [d for d in dates() if d <= value]
    return on[-1] if on else None


def today() -> str:
    """Today, UTC, as a version is named: what the command line stamps."""
    return datetime.now(UTC).date().isoformat()


def _since(to: str, made: str) -> list[Change]:
    """The changes after version `to`, up to and including `made`, newest
    first: the order they are undone in."""
    out: list[Change] = []
    for v in reversed(VERSIONS):
        if to < v.date <= made:
            out.extend(reversed(v.changes))
    return out


def _touches(c: Change, route: str | None, event: str | None) -> bool:
    return (route is not None and route in c.routes) or (event is not None and event in c.events)


def changed(to: str, *, route: str | None = None, event: str | None = None,
            made: str | None = None) -> bool:
    """Whether a body made in version `made` (the newest when not given)
    reads differently in version `to`, for this route or event type."""
    return any(_touches(c, route, event) for c in _since(to, made or current()))


def back(body: Any, *, to: str, route: str | None = None, event: str | None = None,
         made: str | None = None) -> Any:
    """The body as version `to` gives it: each change since undone, newest
    first. A body made in `to` or before is returned as it is: nothing is
    ever made newer than it was made."""
    for c in _since(to, made or current()):
        if _touches(c, route, event):
            body = c.back(body)
    return body


def check() -> None:
    """The list holds together: real dates, each later than the one before,
    none in a shape a header could not carry, and the first with nothing to
    undo. Run at import, so a release with a broken list does not start."""
    seen: str | None = None
    for n, v in enumerate(VERSIONS):
        if not _DATE.match(v.date):
            raise ValueError(f"version {v.date!r} is not YYYY-MM-DD")
        date.fromisoformat(v.date)
        if seen is not None and v.date <= seen:
            raise ValueError(f"version {v.date} is not after {seen}")
        if n == 0 and (v.date != FIRST or v.changes):
            raise ValueError("the first version is FIRST and changes nothing")
        for c in v.changes:
            if not (c.routes or c.events):
                raise ValueError(f"a change in {v.date} names no route and no event: {c.what}")
            for r in c.routes:
                if not _ROUTE.match(r):
                    raise ValueError(f"a change in {v.date} names {r!r}, not a method and a /v1/ path")
        seen = v.date


check()
