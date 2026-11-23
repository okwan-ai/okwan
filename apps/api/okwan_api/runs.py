"""Every reconciliation run as a tenant becomes a stored record.

One helper, called from each surface that runs a fold or a reconciliation
(the dashboard's session routes, REST under /v1/reconciliations, the
hosted MCP's okwan_reconcile), for successful and failed runs alike, so
pages read results instead of running them and there is history.

What is stored is decided in okwan_recon (`trimmed_rows`): outcome,
order reference, currency, totals, what each rail took, reasons. A raw
rail record is never written. A failed run stores the error after the
same scrub the connection test applies.

Each finding in a stored run carries its key and when it was first seen,
carried from the check's newest stored run that finished well
(okwan_recon.exceptions). The summary names the version of the check that
produced it and keeps its oldest open items (`open`), worked out here once,
leaving out the items a person has already explained or resolved.

Recording never fails the run it records: like metering, a lost record
is logged and the result still goes back to the caller.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from okwan_recon.across import AcrossRails
from okwan_recon.engine import ref_paths
from okwan_recon.exceptions import carry, holds, items_by_key, open_summary, version
from okwan_recon.paging import MAX_ROWS
from okwan_recon.registry import all_reconciliations
from okwan_recon.registry import get as get_reconciliation
from okwan_vault import FilesGone, RunRecord

from .auth import get_store
from .scrub import scrub, secrets_of

logger = logging.getLogger(__name__)

#: The connector that serves an account's imported files (okwan_files).
FILES = "files"

def run_clock() -> datetime:
    """When a run started, on the API's clock, tz-aware."""
    return datetime.now(UTC)


async def record_run(
    tenant, spec, surface: str, started: datetime, *,
    result=None, error: Any = None, resolver=None,
) -> str | None:
    """Store one run and return its id, or None when it could not be stored."""
    try:
        kind = "across" if isinstance(spec, AcrossRails) else "pair"
        if result is not None:
            rows = (
                result.trimmed_rows(spec.ledger_currency, limit=MAX_ROWS)
                if kind == "across"
                else result.trimmed_rows(*ref_paths(spec), limit=MAX_ROWS)
            )
            before = await previous_run(tenant.id, kind, spec.name)
            previous = before.rows if before is not None else None
            rows = await asyncio.to_thread(carry, tenant.id, spec.name, kind, rows, previous, started)
            closed = await closed_keys(tenant.id, kind, spec.name, rows)
            summary = await asyncio.to_thread(
                _summarised, tenant.id, spec.name, kind, rows, result.summary or {}, closed)
            summary["check_version"] = check_version(spec)
            status, err = "ok", None
        else:
            before = None
            status, summary, rows = "failed", None, None
            secrets = secrets_of(resolver) if resolver is not None else []
            err = scrub(str(error), secrets)
        rec = await get_store().add_run(
            tenant.id, kind=kind, name=spec.name, surface=surface, status=status,
            started_at=started, finished_at=run_clock(), summary=summary, rows=rows,
            error=err, reads_files=reads_files(spec), files=files_read(spec, result),
        )
        if status == "ok":
            # What changed since the check's previous good result, for the
            # account's webhook addresses, if it has any. Never fails the run.
            from .webhooks import after_run

            await after_run(tenant, rec, before)
        return rec.id
    except FilesGone:
        # Not a fault: a file the run read was deleted, or expired, while it
        # ran. Its result quotes that file, so it is not kept; the caller
        # is told there is no stored run, and the next run reads what is left.
        logger.info("run of %s not kept for %s: a file it read is gone", spec.name, getattr(tenant, "id", tenant))
        return None
    except Exception:  # a lost record must not fail the run
        logger.warning("run not recorded for %s", getattr(tenant, "id", tenant), exc_info=True)
        return None


def _keep(tenant_id: str, name: str, kind: str, rows, previous, started: datetime, summary: dict):
    """The rows and summary to store when no item is closed: each finding
    with its key and first seen time, and the summary with the run's oldest
    open items. Off the event loop: up to MAX_ROWS rows are keyed."""
    kept = carry(tenant_id, name, kind, rows, previous, started)
    return kept, _summarised(tenant_id, name, kind, kept, summary, ())


def _summarised(tenant_id: str, name: str, kind: str, kept, summary: dict, closed) -> dict:
    """The summary to store with carried rows: its oldest open items, those
    a person has closed (`closed`) left out and counted apart."""
    return {**summary, "open": open_summary(tenant_id, name, kind, kept, summary, closed)}


async def closed_keys(tenant_id: str, kind: str, name: str, rows) -> set[str]:
    """The keys of this run's findings a person has explained or resolved,
    as their newest decisions stand now, on the terms this run finds them
    on: an item whose amount, currency or day changed since, or that is now
    alike to another finding, is open again.
    A store that cannot answer leaves every finding open: shown again
    rather than hidden."""
    items = await asyncio.to_thread(items_by_key, tenant_id, name, kind, rows)
    if not items:
        return set()
    try:
        current = await get_store().current_decisions([(tenant_id, k) for k in items])
    except Exception:
        logger.warning("decisions not read for %s's %s", tenant_id, name, exc_info=True)
        return set()
    return {k for (t, kd, n, k), d in current.items()
            if t == tenant_id and kd == kind and n == name and k in items and holds(d, items[k])}


async def previous_run(tenant_id: str, kind: str, name: str) -> RunRecord | None:
    """The check's newest stored run that finished well, by when its data was
    read, with its rows, or None. However many failed runs came after it,
    the times its findings were first seen carry on; and what changed since
    it is what the webhooks say."""
    try:
        good = await get_store().newest_runs([tenant_id], kind, name, ok_only=True, with_rows=True)
    except Exception:  # the run is still stored; its findings start from now
        logger.warning("previous run of %s not read for %s", name, tenant_id, exc_info=True)
        return None
    return good.get(tenant_id)


def check_version(spec) -> str:
    """The version of a check its stored runs name (okwan_recon.exceptions)."""
    if isinstance(spec, AcrossRails):
        return version(spec, [get_reconciliation(rail.reconciliation) for rail in spec.rails])
    return version(spec)


def reads_files(spec) -> bool:
    """Whether a check reads the account's imported files on any side. Its
    stored result then holds references, amounts and dates from them, and
    the store deletes it with any of those files."""
    if isinstance(spec, AcrossRails):
        return any(reads_files(get_reconciliation(rail.reconciliation)) for rail in spec.rails)
    return FILES in (spec.left.connector, spec.right.connector)


def file_check_names() -> list[str]:
    """The checks that read imported files, from the registry as it is
    now. The store asks for these when a file is removed (`file_checks`):
    a run of one goes with the tenant's files even when it was stored
    without the mark, by a release from before the mark existed."""
    return sorted(spec.name for spec in all_reconciliations() if reads_files(spec))


def files_read(spec, result) -> frozenset[str]:
    """The imported files whose rows a result holds, by id: every record
    of a side the files connector served says which import it came from.
    The store keeps the run only while each of them is still there."""
    if result is None or isinstance(spec, AcrossRails):
        return frozenset()
    found: set[str] = set()
    for side, source in (("left", spec.left), ("right", spec.right)):
        if source.connector != FILES:
            continue
        for record in result.records(side):
            file_id = record.get("file_id") if isinstance(record, dict) else None
            if isinstance(file_id, str):
                found.add(file_id)
    return frozenset(found)


def run_total(rec: RunRecord) -> int | None:
    """How many rows the run produced, from its summary; the stored rows
    are cut at MAX_ROWS, worst first."""
    s = rec.summary
    if not s:
        return None
    if rec.kind == "across":
        return s.get("orders")
    return sum(
        s.get(k, 0) or 0
        for k in ("matched", "unmatched_left", "unmatched_right", "ambiguous",
                  "unverifiable_left", "unverifiable_right")
    )


def twice_currency(rows: list | None, summary: dict | None) -> str | None:
    """The one currency the collected-twice orders share, else None: when
    any such order has no currency, when they differ, or when the rows on
    hand do not hold every collected-twice order the summary counts (the
    total could not then be labelled). Defined here, once; the dashboard
    reads it rather than recomputing."""
    if not rows or not summary:
        return None
    twice = [r for r in rows if r.get("outcome") == "collected_twice"]
    if not twice or len(twice) < (summary.get("collected_twice") or 0):
        return None
    currencies = {(r.get("order") or {}).get("currency") for r in twice}
    if None in currencies or len(currencies) != 1:
        return None
    return currencies.pop()


def listed_summary(summary: dict | None) -> dict | None:
    """A run's summary without the open items it keeps (references, amounts
    and days, like its rows): what a listing, and the support assistant,
    see. Counts stay."""
    if not summary or not isinstance(summary.get("open"), dict):
        return summary
    opened = summary["open"]
    return {**summary, "open": {k: opened[k] for k in ("found", "kept") if k in opened}}


def run_dict(rec: RunRecord, with_rows: bool) -> dict[str, Any]:
    """The record as the API returns it. A listing leaves the rows out, and
    the open items its summary keeps (`listed_summary`)."""
    total = run_total(rec)
    out: dict[str, Any] = {
        "id": rec.id,
        "tenant_id": rec.tenant_id,
        "kind": rec.kind,
        "name": rec.name,
        "surface": rec.surface,
        "status": rec.status,
        "started_at": rec.started_at.isoformat(),
        "finished_at": rec.finished_at.isoformat(),
        "summary": rec.summary if with_rows else listed_summary(rec.summary),
        "error": rec.error,
        "rows_total": total,
        "twice_currency": twice_currency(rec.rows, rec.summary) if rec.kind == "across" else None,
    }
    if with_rows:
        rows = rec.rows or []
        out["rows"] = rows
        out["has_more"] = total is not None and len(rows) < total
    return out
