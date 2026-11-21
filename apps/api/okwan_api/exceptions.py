"""Every open finding a tenant and its merchants have, across their checks.

A check's stored run lists what needs a look; each check has its own page.
This answers the question a person starts the day with: what is open,
everywhere, how old is it and how much is at stake. It reads the newest
stored run of each check for the target and its direct children (as
Overview and Findings read runs), and turns each run's findings into items
(okwan_recon.exceptions): the finding's key, which stays the same from one
run to the next, the references it is about, the amount at stake, the day
it is dated, when it was first seen and how many days it has been open.

Like the run history it reads, it runs nothing, is not metered, and is
for a dashboard session or an admin key: a target outside the caller's
subtree answers 404.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from okwan_recon.exceptions import open_items
from okwan_recon.registry import all_across, all_reconciliations

from .admin import _guard
from .auth import admin_actor, get_store

#: Most items one answer carries, oldest and largest first; the count of
#: every open item is given whatever the cut.
MAX_ITEMS = 500


def now() -> datetime:
    """The clock items are aged by; a test moves it."""
    return datetime.now(UTC)


def _checks() -> list[tuple[str, str, str]]:
    """Every check a run can be stored for: (kind, name, title)."""
    out = [("across", s.name, s.display_title) for s in all_across()]
    out += [("pair", s.name, s.display_title) for s in all_reconciliations()]
    return out


def _cut(rec) -> bool:
    """Whether the stored rows hold fewer findings than the run found: rows
    are kept worst first up to a limit, so a very long result keeps only
    its first findings."""
    s = rec.summary or {}
    if rec.kind == "across":
        found = sum(s.get(k) or 0 for k in ("collected_twice", "collected_inconsistent", "uncollected"))
    else:
        found = sum(s.get(k) or 0 for k in ("matched_discrepant", "unmatched_left", "unmatched_right", "ambiguous"))
    return found > len(open_items(rec.name, rec.kind, rec.rows, now()))


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/tenants", tags=["tenants"])

    @router.get("/{tenant_id}/exceptions")
    async def list_exceptions(tenant_id: str, actor=Depends(admin_actor)) -> dict[str, Any]:
        """Every open finding of the target and its direct children, from
        each check's newest stored run, oldest first and then largest. Each
        check's newest run is listed too, with whether it finished, so a
        check that failed since its findings were stored shows. Not metered."""
        await _guard(actor, tenant_id)
        store = get_store()
        target = await store.get_tenant(tenant_id)
        if target is None:
            raise HTTPException(404, f"no such tenant: {tenant_id}")
        tenants = [target, *await store.children_of(tenant_id)]
        names = {t.id: t.name for t in tenants}
        at = now()
        items: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        for kind, name, title in _checks():
            latest = await store.latest_runs(list(names), kind, name)
            for tid, rec in latest.items():
                good = rec
                if rec.status != "ok":
                    # The findings a person is working on are the last good
                    # run's; the check's own line says the newest one failed.
                    good = next((r for r in await store.list_runs(tid, name, 5)
                                 if r.kind == kind and r.status == "ok"), None)
                    good = await store.get_run(tid, good.id) if good is not None else None
                found = open_items(name, kind, good.rows, at) if good is not None else []
                checks.append({
                    "tenant_id": tid, "check": name, "title": title, "kind": kind,
                    "last_run": {"id": rec.id, "status": rec.status, "finished_at": rec.finished_at.isoformat()},
                    "findings_from": good.id if good is not None else None,
                    "open": len(found),
                    "cut": good is not None and _cut(good),
                    "check_version": ((good.summary or {}).get("check_version") if good is not None else None),
                })
                for item in found:
                    items.append({**item, "tenant_id": tid, "merchant": names[tid], "title": title,
                                  "kind": kind, "run_id": good.id, "run_at": good.finished_at.isoformat()})
        items.sort(key=lambda i: (-(i["open_days"] if i["open_days"] is not None else -1),
                                  -(i["amount_minor"] or 0), i["merchant"], i["check"], i["key"]))
        checks.sort(key=lambda c: (names[c["tenant_id"]], c["check"]))
        return {"data": items[:MAX_ITEMS], "total": len(items), "checks": checks, "as_of": at.isoformat()}

    return router
