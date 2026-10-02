"""Paging a reconciliation's result rows.

`limit` bounds the rows returned, never the records read: a fetch cut to
the page size reconciles a fragment and reports the rest as unmatched or
unverifiable. The read runs to the declaration's cap; the result is
paged.

Tools are stateless, so each page re-runs the reconciliation. Upstream
can change between pages, and an offset into a different result skips
or repeats rows without a sign. The cursor therefore carries a
fingerprint of the result it was cut from, and a page whose result no
longer matches is refused rather than served.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

DEFAULT_ROWS = 100
MAX_ROWS = 1000

Row = dict[str, Any]


class StaleCursor(Exception):
    """The result changed since the cursor was issued."""


def fingerprint(rows: list[Row]) -> str:
    """Identity of a result: each row's status and records. Excludes
    anything derived from the clock — a coverage span ending at now
    would make every re-run look different."""
    ident = [
        [r.get("status") or r.get("outcome"), r.get("left"), r.get("right"), r.get("order")]
        for r in rows
    ]
    blob = json.dumps(ident, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _encode(offset: int, fp: str, view: str) -> str:
    raw = json.dumps({"o": offset, "f": fp, "v": view}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str) -> dict[str, Any] | None:
    try:
        pad = "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(cursor + pad))
        if isinstance(data, dict) and isinstance(data.get("o"), int):
            return data
    except (ValueError, TypeError):
        pass
    return None


def page_rows(
    rows: list[Row], limit: int, cursor: str | None = None, view: str = "all"
) -> dict[str, Any]:
    """One page of `rows` (already filtered to `view`).

    Raises StaleCursor when the cursor was cut from a different result or
    a different filter, or cannot be read. Silently restarting would hand
    the caller page one again as if it were page two.
    """
    limit = max(1, min(limit, MAX_ROWS))
    fp = fingerprint(rows)
    offset = 0
    if cursor:
        state = _decode(cursor)
        if state is None:
            raise StaleCursor("unreadable cursor; restart without one")
        if state.get("v") != view:
            raise StaleCursor(
                f"cursor was issued for {state.get('v')!r}, not {view!r}; restart without one"
            )
        if state.get("f") != fp:
            raise StaleCursor(
                "the result changed since this cursor was issued; restart without one"
            )
        offset = state["o"]
    page = rows[offset:offset + limit]
    end = offset + len(page)
    more = end < len(rows)
    return {
        "rows": page,
        "total_rows": len(rows),
        "has_more": more,
        "next_cursor": _encode(end, fp, view) if more else None,
    }
