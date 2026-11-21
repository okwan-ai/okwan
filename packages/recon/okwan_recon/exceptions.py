"""Findings that last from one run to the next.

A check's stored run lists what needs a look: a batch never deposited, a
deposit no batch explains, an order collected twice. Run it again tomorrow
and the same batch is listed again, with nothing to say it is the one from
yesterday. A person then re-checks what they already looked at, which is
where time spent on reconciliation goes.

Here a finding gets an identity (`key`) made from what it is about, never
from the run: the check, the kind of finding, and the references of the
records involved, so the same batch is the same finding in every run. Each
stored run carries, for every finding, when it was first seen: copied from
the check's previous stored run when the finding was in it, else the time
of this run. `open_items` turns a stored run into the list a person works
from, each with its age and the amount it puts at stake.

`version` names the declaration a run came from, so a stored result says
which version of the check produced it and a changed check shows.

`open_summary` is worked out once, when a run is stored: how many findings
the run found and kept, and its oldest open items, so a list across every
merchant of an account reads a few items a run and never every stored row.
An item a person has closed (explained or resolved, okwan_vault's
decisions) is left out of it and counted apart.

Pure functions over stored rows. Nothing here reads a connector or the
store, and nothing is added to a row but its key and the time it was first
seen: no record, no customer, nothing a stored row did not already hold.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection
from datetime import UTC, date, datetime
from typing import Any

Row = dict[str, Any]

#: What a pair check's stored row says needs a look (the dashboard's
#: attention list): amounts that differ with nothing to explain them, a
#: record with no counterpart on either side, and a record more than one
#: counterpart fits equally. An explained difference, a clean match and a
#: record the other side was not read far enough to judge are not findings.
PAIR_FINDINGS = ("matched_discrepant", "unmatched_left", "unmatched_right", "ambiguous")
#: The fold's findings, as the daily check counts them.
FOLD_FINDINGS = ("collected_twice", "collected_inconsistent", "uncollected")


def finding(kind: str, row: Row) -> str | None:
    """The verdict a stored row reports when it is a finding, else None."""
    value = row.get("outcome") if kind == "across" else row.get("status")
    wanted = FOLD_FINDINGS if kind == "across" else PAIR_FINDINGS
    return value if value in wanted else None


def _text(value: Any) -> str | None:
    """A reference as a stored row keeps it: Postgres cannot hold a NUL in
    text, so the store writes one as U+FFFD. The key is made from the same
    text whether the row is fresh or read back."""
    return None if value is None else str(value).replace("\x00", "�")


def _record(row: Row) -> Row:
    rec = (row.get("evidence") or {}).get("record")
    return rec if isinstance(rec, dict) else {}


def _dated(row: Row) -> list[Any]:
    """The days a pair finding is about. A processor reuses a batch number
    ("001" every day): two batches that share one are told apart by the
    day each closed. An amount is not part of it, so a figure corrected
    between runs is the same finding."""
    ev = row.get("evidence") or {}
    dated = ev.get("dated") if isinstance(ev.get("dated"), dict) else {}
    return [_record(row).get("when"), dated.get("left"), dated.get("right")]


def _parts(tenant_id: str, name: str, kind: str, row: Row) -> list[Any] | None:
    verdict = finding(kind, row)
    if verdict is None:
        return None
    if kind == "across":
        order = row.get("order") or {}
        return [tenant_id, name, verdict, _text(order.get("ref"))]
    left, right = _text(row.get("left_ref")), _text(row.get("right_ref"))
    parts: list[Any] = [tenant_id, name, verdict, left, right, *_dated(row)]
    if left is None and right is None:
        # Neither side carries a reference (a bank line with none): what the
        # record was is all that tells one such finding from another.
        rec = _record(row)
        parts += [rec.get("amount_minor"), rec.get("currency")]
    return parts


def keyed(tenant_id: str, name: str, kind: str, rows: list[Row]) -> list[tuple[str | None, Row]]:
    """Each row with its finding's key, or None for a row that is not a
    finding. The key belongs to the tenant: the same payout in another
    account has another key. Two findings alike in every part are told
    apart by the order the run lists them in, so neither swallows the
    other."""
    seen: dict[str, int] = {}
    out: list[tuple[str | None, Row]] = []
    for row in rows:
        parts = _parts(tenant_id, name, kind, row) if isinstance(row, dict) else None
        if parts is None:
            out.append((None, row))
            continue
        text = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str)
        n = seen.get(text, 0)
        seen[text] = n + 1
        if n:
            text = f"{text}#{n}"
        out.append((hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:24], row))
    return out


def carry(tenant_id: str, name: str, kind: str, rows: list[Row] | None, previous: list[Row] | None,
          seen_at: datetime) -> list[Row] | None:
    """The rows to store, each finding with its `key` and `first_seen`:
    the time the previous stored run gave the same finding, or `seen_at`
    when it was not in that run. Rows that are not findings are as they
    came. `previous` is the check's newest stored run that finished well;
    a finding missing from it starts again from this run."""
    if rows is None:
        return None
    before: dict[str, str] = {}
    for key, row in keyed(tenant_id, name, kind, previous or []):
        first = row.get("first_seen") if isinstance(row, dict) else None
        if key is not None and isinstance(first, str):
            before[key] = first
    now = seen_at.astimezone(UTC).isoformat()
    out: list[Row] = []
    for key, row in keyed(tenant_id, name, kind, rows):
        out.append(row if key is None else {**row, "key": key, "first_seen": before.get(key, now)})
    return out


def version(spec: Any, members: list[Any] | None = None) -> str:
    """A short digest of a declaration as it is now: two runs of the same
    check carry the same version until its declaration changes. A fold's
    version covers the declarations it folds."""
    text = spec.model_dump_json()
    for member in members or []:
        text += "\x1f" + member.model_dump_json()
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _day(value: Any) -> date | None:
    """A stored day or instant as a calendar day, else None."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _pair_terms(row: Row) -> tuple[int | None, str | None, date | None]:
    """What a pair finding puts at stake, in what currency, and the day it
    is dated: the difference for amounts that differ, else the record's own
    amount; the record's day, else the day of the side that has one."""
    ev = row.get("evidence") or {}
    rec = _record(row)
    compared = ev.get("compared") if isinstance(ev.get("compared"), dict) else {}
    dated = ev.get("dated") if isinstance(ev.get("dated"), dict) else {}
    if row.get("status") == "matched_discrepant":
        diff = row.get("discrepancy")
        amount = abs(diff) if isinstance(diff, int) and not isinstance(diff, bool) else None
        currency = compared.get("currency")
    else:
        value = rec.get("amount_minor")
        amount = abs(value) if isinstance(value, int) and not isinstance(value, bool) else None
        currency = rec.get("currency")
    day = _day(rec.get("when")) or _day(dated.get("left")) or _day(dated.get("right"))
    return amount, currency if isinstance(currency, str) else None, day


def _fold_terms(row: Row) -> tuple[int | None, str | None, date | None]:
    """An order collected twice puts the excess at stake, one that does not
    add up the gap, one never collected its total."""
    total, taken = row.get("order_total_minor"), row.get("collected_minor")
    ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (total, taken))
    if row.get("outcome") == "uncollected":
        amount = total if isinstance(total, int) and not isinstance(total, bool) else None
    else:
        amount = abs(taken - total) if ints else None
    order = row.get("order") or {}
    currency = order.get("currency")
    # The order's own day, as the fold keeps it. `collected_on` is not a day:
    # it names the rails that collected.
    return amount, currency if isinstance(currency, str) else None, _day(order.get("on"))


#: Items a run's summary keeps, oldest first: what a list across every
#: merchant of an account shows of one check before it says how many more.
OPEN_KEPT = 20
#: The summary counts that are findings, for a pair check and for the fold.
_PAIR_COUNTS = ("matched_with_unexplained_discrepancy", "unmatched_left", "unmatched_right", "ambiguous")
_FOLD_COUNTS = FOLD_FINDINGS


def _item(name: str, kind: str, key: str, row: Row) -> Row:
    """A finding as an item, without its age."""
    amount, currency, day = _fold_terms(row) if kind == "across" else _pair_terms(row)
    first = _instant(row.get("first_seen"))
    item: Row = {
        "key": key,
        "check": name,
        "finding": finding(kind, row),
        "amount_minor": amount,
        "currency": currency,
        "dated_on": day.isoformat() if day else None,
        "first_seen": first.isoformat() if first else None,
        "reason": row.get("reason"),
    }
    if kind == "across":
        item["order_ref"] = (row.get("order") or {}).get("ref")
    else:
        item["left_ref"], item["right_ref"] = row.get("left_ref"), row.get("right_ref")
    return item


def _since(item: Row) -> date | None:
    first = _instant(item.get("first_seen"))
    return _day(item.get("dated_on")) or (first.date() if first else None)


def oldest_first(item: Row) -> tuple:
    """The order every list keeps: the longest open first, then the largest
    amount at stake, then by key so the order is the same every time."""
    since = _since(item)
    return (since or date.max, -(item.get("amount_minor") or 0), str(item.get("key")))


def aged(item: Row, now: datetime) -> Row:
    """An item with how many whole days it has been open, counted from the
    day it is dated or, where it has none, from when it was first seen."""
    since = _since(item)
    return {**item, "open_days": max((now.date() - since).days, 0) if since else None}


def _found(tenant_id: str, name: str, kind: str, rows: list[Row] | None) -> list[Row]:
    out = []
    for computed, row in keyed(tenant_id, name, kind, rows or []):
        if computed is None:
            continue
        stored = row.get("key")
        out.append(_item(name, kind, stored if isinstance(stored, str) else computed, row))
    return out


def open_items(tenant_id: str, name: str, kind: str, rows: list[Row] | None, now: datetime) -> list[Row]:
    """A stored run's findings as the items a person works from, oldest
    first: what the finding is, the references involved, the amount at
    stake and its currency, the day it is dated, when it was first seen and
    how many whole days it has been open."""
    return [aged(i, now) for i in sorted(_found(tenant_id, name, kind, rows), key=oldest_first)]


def run_found(kind: str, summary: Row | None) -> int:
    """How many findings a run found, by its own counts: more than its
    stored rows hold when they were cut."""
    counts = _FOLD_COUNTS if kind == "across" else _PAIR_COUNTS
    return sum(int((summary or {}).get(k) or 0) for k in counts)


def open_summary(tenant_id: str, name: str, kind: str, rows: list[Row] | None,
                 summary: Row | None, closed: Collection[str] = ()) -> Row:
    """What a run's summary keeps of its open items, worked out once when
    the run is stored: how many findings the run found (its own counts) and
    how many its stored rows kept (rows are kept worst first up to a
    limit), and its OPEN_KEPT oldest items, without their age.

    `closed`: the keys of items a person has explained or resolved when the
    run is stored. They are not open: they are left out of the counts and
    the items, and counted as `closed`. A person can only decide an item a
    stored row holds, so every closed one is among the kept rows."""
    shut = set(closed)
    every = _found(tenant_id, name, kind, rows)
    items = sorted((i for i in every if i["key"] not in shut), key=oldest_first)
    n_closed = len(every) - len(items)
    found = max(run_found(kind, summary), len(every)) - n_closed
    return {"found": found, "kept": len(items), "closed": n_closed, "oldest": items[:OPEN_KEPT]}
