"""What each reconciliation's sources hold for the caller, for listings.

A listing that says a reconciliation can run when one side has nothing
to read would send an agent to an empty answer. So each source states
whether it is configured (its credentials saved, or for imported files,
files uploaded), and for files which files and days they cover; each
reconciliation states its last run and its last good one. Read for the
caller only: its vault, its imports, its stored runs.

One function for both listings, `GET /v1/reconciliations` and the hosted
MCP's `okwan_list_reconciliations` (§9 2026-11-06: connector status in
the listing, no new MCP tool).
"""
from __future__ import annotations

from typing import Any

from okwan_core import get as get_connector
from okwan_recon.registry import all_reconciliations

from .auth import get_store

FILES = "files"


def _day(d) -> str | None:
    return d.isoformat() if d else None


async def recon_status(tenant, resolver) -> dict[str, dict[str, Any]]:
    """Per reconciliation name: sources, runnable, missing credentials,
    last run and last good run."""
    store = get_store()
    imports = await store.import_summary(tenant.id)
    out: dict[str, dict[str, Any]] = {}
    for spec in all_reconciliations():
        sources: list[dict[str, Any]] = []
        missing: list[str] = []
        for ref in (spec.left, spec.right):
            connector = get_connector(ref.connector)
            fields = connector.auth.required_fields
            creds = resolver(ref.connector, fields)
            gaps = [f"{ref.connector}.{f}" for f in fields if not creds.get(f)]
            missing += [g for g in gaps if g not in missing]
            entry: dict[str, Any] = {"source": f"{ref.connector}.{ref.resource}",
                                     "configured": not gaps, "missing_credentials": gaps}
            if ref.connector == FILES:
                held = imports.get(ref.resource) or {}
                entry["imports"] = {"files": held.get("files", 0), "rows": held.get("rows", 0),
                                    "first_date": _day(held.get("first_date")),
                                    "last_date": _day(held.get("last_date"))}
                entry["configured"] = bool(held.get("files"))
            sources.append(entry)
        runs = [r for r in await store.list_runs(tenant.id, name=spec.name, limit=20)
                if r.kind == "pair"]
        last = runs[0] if runs else None
        good = next((r for r in runs if r.status == "ok"), None)
        out[spec.name] = {
            "runnable": all(s["configured"] for s in sources),
            "missing_credentials": missing,
            "sources": sources,
            "last_run": ({"at": last.finished_at.isoformat(), "status": last.status,
                          "surface": last.surface} if last else None),
            "last_ok_at": good.finished_at.isoformat() if good else None,
        }
    return out
