"""Every open finding a tenant and its merchants have, across their checks.

A check's stored run lists what needs a look; each check has its own page.
This answers the question a person starts the day with: what is open,
everywhere, how old is it and how much is at stake. Each item keeps the
same key from one run to the next (okwan_recon.exceptions).

Two views, both bounded by construction:

- The account (no `merchant`): the target and its direct children. It reads
  each check's newest good run without its rows: the run's summary already
  holds its oldest open items, worked out once when it was stored. Each
  merchant has at most PER_MERCHANT items in it, so one merchant cannot push
  the others off; each check says how many more it has.
- One merchant (`merchant` = the target or a direct child): every finding
  its checks' newest good runs kept, from their rows, keyed when stored.

Which run is newest is decided by when its data was read, so a slow run
that read earlier does not replace a newer one. Like the run history it
reads, it runs nothing and is not metered, takes a dashboard session or an
admin key, and answers 404 for a tenant outside the caller's subtree. The
work that is left runs off the event loop.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from okwan_recon.exceptions import aged, oldest_first, open_items, open_summary
from okwan_recon.registry import all_across, all_reconciliations

from .admin import _guard
from .auth import admin_actor, get_store
from .ratelimit import EXCEPTIONS_TENANT, enforce

#: Items of one merchant in the account's list, at most.
PER_MERCHANT = 50
#: Items one answer carries, at most.
MAX_ITEMS = 1000


def now() -> datetime:
    """The clock items are aged by; a test moves it."""
    return datetime.now(UTC)


def _checks() -> list[tuple[str, str, str]]:
    """Every check a run can be stored for: (kind, name, title)."""
    out = [("across", s.name, s.display_title) for s in all_across()]
    out += [("pair", s.name, s.display_title) for s in all_reconciliations()]
    return out


async def _open_of(tid: str, rec) -> dict[str, Any]:
    """The summary's open items, or, for a run stored before summaries kept
    them, the same worked out from its rows."""
    kept = (rec.summary or {}).get("open")
    if isinstance(kept, dict) and isinstance(kept.get("oldest"), list):
        return kept
    full = await get_store().get_run(tid, rec.id)
    rows = full.rows if full is not None else None
    return await asyncio.to_thread(open_summary, tid, rec.name, rec.kind, rows, rec.summary)


def _line(tid, name, title, kind, newest, good, opened) -> dict[str, Any]:
    found = int(opened.get("found") or 0) if opened else 0
    kept = int(opened.get("kept") or 0) if opened else 0
    return {
        "tenant_id": tid, "check": name, "title": title, "kind": kind,
        "last_run": ({"id": newest.id, "status": newest.status, "started_at": newest.started_at.isoformat(),
                      "finished_at": newest.finished_at.isoformat()} if newest else None),
        "findings_from": good.id if good else None,
        "open": found,
        "cut": found > kept,
        "check_version": (good.summary or {}).get("check_version") if good else None,
    }


def _account_items(per_tenant: dict[str, list[dict]], at: datetime) -> list[dict]:
    out: list[dict] = []
    for items in per_tenant.values():
        out += sorted(items, key=oldest_first)[:PER_MERCHANT]
    return [aged(i, at) for i in sorted(out, key=oldest_first)]


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/tenants", tags=["tenants"])

    @router.get("/{tenant_id}/exceptions")
    async def list_exceptions(
        tenant_id: str,
        request: Request,
        merchant: str | None = Query(None, description="One merchant (the target or a direct child): all its open items"),
        limit: int = Query(200, ge=1, le=MAX_ITEMS),
        actor=Depends(admin_actor),
    ) -> dict[str, Any]:
        """Open findings, oldest first and then largest. Without `merchant`,
        those of the target and its direct children, each merchant's oldest
        PER_MERCHANT at most; with it, every finding of that one merchant.
        Each check's newest run is listed too, with whether it finished, so
        a check that failed since its findings were stored shows, and how
        many findings it has. Not metered."""
        await _guard(actor, tenant_id)
        enforce(request, (EXCEPTIONS_TENANT, tenant_id))
        store = get_store()
        target = await store.get_tenant(tenant_id)
        if target is None:
            raise HTTPException(404, f"no such tenant: {tenant_id}")
        tenants = [target, *await store.children_of(tenant_id)]
        names = {t.id: t.name for t in tenants}
        if merchant is not None and merchant not in names:
            raise HTTPException(404, f"no such tenant: {merchant}")
        ids = [merchant] if merchant else list(names)
        at = now()
        lines: list[dict[str, Any]] = []
        per_tenant: dict[str, list[dict]] = {tid: [] for tid in ids}
        for kind, name, title in _checks():
            newest = await store.newest_runs(ids, kind, name)
            good = await store.newest_runs(ids, kind, name, ok_only=True, with_rows=merchant is not None)
            for tid in ids:
                n, g = newest.get(tid), good.get(tid)
                if n is None and g is None:
                    continue
                if g is None:
                    lines.append(_line(tid, name, title, kind, n, None, None))
                    continue
                if merchant is not None:
                    items = await asyncio.to_thread(open_items, tid, name, kind, g.rows, at)
                    found = int(((g.summary or {}).get("open") or {}).get("found") or 0)
                    opened = {"found": max(found, len(items)), "kept": len(items)}
                else:
                    opened = await _open_of(tid, g)
                    items = opened["oldest"]
                lines.append(_line(tid, name, title, kind, n, g, opened))
                per_tenant[tid] += [{**i, "tenant_id": tid, "merchant": names[tid], "title": title,
                                     "kind": kind, "run_id": g.id, "run_at": g.finished_at.isoformat()}
                                    for i in items]
        if merchant is not None:
            items = sorted(per_tenant[merchant], key=oldest_first)  # aged by open_items
        else:
            items = await asyncio.to_thread(_account_items, per_tenant, at)
        lines.sort(key=lambda c: (names[c["tenant_id"]], c["check"]))
        return {
            "data": items[:limit],
            "total": sum(c["open"] for c in lines),
            "listed": min(len(items), limit),
            "checks": lines,
            "as_of": at.isoformat(),
        }

    return router
