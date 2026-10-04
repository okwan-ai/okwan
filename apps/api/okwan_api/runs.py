"""Every reconciliation run as a tenant becomes a stored record.

One helper, called from each surface that runs a fold or a reconciliation
(the dashboard's session routes, REST under /v1/reconciliations, the
hosted MCP's okwan_reconcile), for successful and failed runs alike, so
pages read results instead of running them and there is history.

What is stored is decided in okwan_recon (`trimmed_rows`): outcome,
order reference, currency, totals, what each rail took, reasons. A raw
rail record is never written. A failed run stores the error after the
same scrub the connection test applies.

Recording never fails the run it records: like metering, a lost record
is logged and the result still goes back to the caller.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from okwan_recon.across import AcrossRails
from okwan_recon.engine import ref_paths
from okwan_recon.paging import MAX_ROWS
from okwan_vault import RunRecord

from .auth import get_store
from .scrub import scrub, secrets_of

logger = logging.getLogger(__name__)

SURFACES = ("dashboard", "rest", "mcp")


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
            status, summary, err = "ok", result.summary, None
        else:
            status, summary, rows = "failed", None, None
            secrets = secrets_of(resolver) if resolver is not None else []
            err = scrub(str(error), secrets)
        rec = await get_store().add_run(
            tenant.id, kind=kind, name=spec.name, surface=surface, status=status,
            started_at=started, finished_at=run_clock(), summary=summary, rows=rows,
            error=err,
        )
        return rec.id
    except Exception:  # noqa: BLE001 — a lost record must not fail the run
        logger.warning("run not recorded for %s", getattr(tenant, "id", tenant), exc_info=True)
        return None


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


def run_dict(rec: RunRecord, with_rows: bool) -> dict[str, Any]:
    """The record as the API returns it. A listing leaves the rows out."""
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
        "summary": rec.summary,
        "error": rec.error,
        "rows_total": total,
    }
    if with_rows:
        rows = rec.rows or []
        out["rows"] = rows
        out["has_more"] = total is not None and len(rows) < total
    return out
