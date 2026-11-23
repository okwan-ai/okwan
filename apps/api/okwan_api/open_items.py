"""The open-items list as data an API key can read, a read-only key included.

`OpenItem` (okwan_recon.exceptions) is the row, and the one definition
three surfaces share: `GET /v1/exceptions` here, the SQL table
`exceptions.open_items` (okwan_query.catalog) and the hosted MCP tool
`okwan_open_items` (okwan_query.mcp_http). They read through `read`, so
they answer alike and cannot drift.

A read key, the kind an AI client or a reporting job is given, reads what
is open or closed, each item's newest decision and Okwan's newest
suggestion, and changes nothing: deciding stays on the admin routes
(okwan_api.exceptions), which refuse a read key, and so does asking for a
suggestion. Reading stored results reads no rail, so it is not metered,
as the dashboard's list is not; each read is written to the access log
with the key that made it, and limited per account.

What one read covers: the key's tenant and every tenant below it as far as
it can open (authz.MAX_DEPTH); or one of them, by `merchant`. Each check's
newest good stored result is read from its rows, a few merchants at a time
and off the event loop, at most READ_ITEMS items or READ_MERCHANTS
merchants. Past that the answer says how many merchants it did not read:
one merchant can be read on its own.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from okwan_query.session import TooManyRuns
from okwan_recon.exceptions import WHAT, OpenItem, holds, open_items, run_found, terms
from okwan_vault.authz import MAX_DEPTH, ancestors
from okwan_vault.models import Tenant

from . import access
from .auth import current_tenant, get_store
from .exceptions import _titles
from .ratelimit import OPEN_ITEMS_TENANT, limiter
from .runs import file_check_names

#: Merchants whose stored results are read at once: few, so what one read
#: decodes is small.
READ_CHUNK = 5
#: What one read covers at most: it stops before the next merchant once
#: past READ_ITEMS.
READ_ITEMS = 20_000
READ_MERCHANTS = 2_000
#: Items one REST or MCP answer lists; SQL holds every item read.
LIST_MAX = 1_000
STATES = ("open", "closed", "all")


class TooManyReads(TooManyRuns):
    """This account read its open items OPEN_ITEMS_TENANT times in the
    last minute, or has a read under way; `retry_after` seconds until it
    may again. Answered as a 429 (REST, SQL) or as data (MCP), as too many
    live runs are."""


class NoSuchMerchant(Exception):
    """Not the caller or a tenant below it: reads as one that does not exist."""


#: Accounts with a read of their open items under way. One at a time an
#: account: a read is many statements, and an account's reads take turns
#: rather than hold the database's connections side by side.
_reading: set[str] = set()


async def _account(store, tenant: Tenant) -> str:
    """The account at the top of the key's tree: the limit is the account's,
    shared by its merchants' keys."""
    above = await ancestors(store, tenant.id)
    return above[-1] if above else tenant.id


async def _scope(store, tenant: Tenant, merchant: str | None) -> list[Tenant]:
    """The tenants one read covers: the key's tenant and those below it, or
    the one merchant named, if it is one of them (MAX_DEPTH down at most)."""
    if merchant is None:
        return await store.subtree(tenant.id, MAX_DEPTH)
    found = await store.get_tenant(merchant)
    if found is None:
        raise NoSuchMerchant(merchant)
    if found.id != tenant.id:
        chain = await ancestors(store, found.id)
        if tenant.id not in chain or chain.index(tenant.id) >= MAX_DEPTH:
            raise NoSuchMerchant(merchant)
    return [found]


@dataclass
class Reader:
    """One read of a tenant's open items, as rows of OpenItem fields, a
    batch a check (MAX_ROWS at most). `merchants_read`,
    `merchants_not_read` and `not_kept` are set as it goes. Opening it
    takes the account's turn and its place in the per-minute limit, and
    writes the read to the access log as `surface`, naming `target`."""

    tenant: Tenant
    merchant: str | None = None
    check: str | None = None
    state: str = "open"
    surface: str = "rest:exceptions"
    target: str | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    merchants_read: int = 0
    merchants_not_read: int = 0
    #: Findings the checks' results found but did not keep (their 1,000
    #: worst each): counted, never listed.
    not_kept: int = 0

    async def batches(self) -> AsyncIterator[list[dict[str, Any]]]:
        store = get_store()
        account = await _account(store, self.tenant)
        if account in _reading:
            raise TooManyReads("a read of this account's open items is under way; try again in a few seconds", 2.0)
        wait = limiter.take((OPEN_ITEMS_TENANT, account))
        if wait is not None:
            raise TooManyReads(
                f"this account has read its open items {OPEN_ITEMS_TENANT.limit} times in the last minute; "
                f"try again in {math.ceil(wait)} s", wait)
        _reading.add(account)
        try:
            tenants = await _scope(store, self.tenant, self.merchant)
            # Written before anything is read: a read that fails half way, or a
            # query that errs once the table is loaded, is a read all the same.
            await access.record(self.tenant, self.surface, self.target)
            async for batch in self._read(store, tenants):
                yield batch
        finally:
            _reading.discard(account)

    async def _read(self, store, tenants: list[Tenant]) -> AsyncIterator[list[dict[str, Any]]]:
        titles, files = _titles(), set(file_check_names())
        items_read = 0
        for at in range(0, len(tenants), READ_CHUNK):
            chunk = tenants[at:at + READ_CHUNK]
            ids = [t.id for t in chunk]
            runs = await store.newest_runs_all(ids, ok_only=True, with_rows=True)
            # A merchant's expired files go first, with the results that read
            # them, as before the daily mail and a suggestion: whoever holds
            # the key, a file is never read past its keeping.
            stale = sorted({r.tenant_id for r in runs if r.name in files})
            if any([await store.drop_expired_imports(tid, self.now) for tid in stale]):
                runs = await store.newest_runs_all(ids, ok_only=True, with_rows=True)
            for t in chunk:
                # The bound is looked at before each merchant: a read goes past
                # READ_ITEMS by one merchant's items at most.
                if items_read >= READ_ITEMS or self.merchants_read >= READ_MERCHANTS:
                    self.merchants_not_read = len(tenants) - self.merchants_read
                    return
                found = []
                for run in (r for r in runs if r.tenant_id == t.id):
                    title = titles.get((run.kind, run.name))
                    if title is None or (self.check is not None and run.name != self.check):
                        continue
                    items = await asyncio.to_thread(open_items, t.id, run.name, run.kind, run.rows, self.now)
                    cut = max(run_found(run.kind, run.summary) - len(items), 0)
                    self.not_kept += cut
                    found.append((run, title, items, cut > 0))
                    items_read += len(items)
                # One statement each for the merchant's decisions and suggestions.
                pairs = [(t.id, i["key"]) for _, _, items, _ in found for i in items]
                decisions = await store.current_decisions(pairs)
                suggestions = await store.newest_suggestions(pairs)
                self.merchants_read += 1
                for run, title, items, cut in found:
                    yield await asyncio.to_thread(
                        _rows, t, run, title, items, decisions, suggestions, self.state, cut)


def _rows(tenant: Tenant, run, title: str, items: list[dict[str, Any]], decisions: dict,
          suggestions: dict, state: str, cut: bool) -> list[dict[str, Any]]:
    """The OpenItem fields of one check's items, those in `state`."""
    out = []
    for i in items:
        k = (tenant.id, run.kind, run.name, i["key"])
        d, s = decisions.get(k), suggestions.get(k)
        closed = holds(d, i)
        if state != "all" and closed != (state == "closed"):
            continue
        row = {
            "merchant_id": tenant.id, "merchant": tenant.name, "check_name": run.name, "kind": run.kind,
            "title": title, "item_key": i["key"], "finding": i.get("finding"),
            "what": WHAT.get(i.get("finding"), "needs a look"), "state": "closed" if closed else "open",
            "amount_minor": i.get("amount_minor"), "signed_minor": i.get("signed_minor"),
            "currency": i.get("currency"), "dated_on": _day(i.get("dated_on")),
            "first_seen": _instant(i.get("first_seen")), "open_days": i.get("open_days"),
            "order_ref": i.get("order_ref"), "left_ref": i.get("left_ref"), "right_ref": i.get("right_ref"),
            "alike": bool(i.get("alike")), "result_cut": cut, "run_id": run.id, "run_at": run.finished_at,
            "decision_status": d.status if d else None,
            "decision_changed": (d.closed and not closed) if d else None,
            "decision_owner": d.owner if d else None, "decision_note": d.note if d else None,
            "decided_by": d.actor_name if d else None, "decided_at": d.created_at if d else None,
            "decision_from_suggestion": d.from_suggestion if d else None,
            "suggestion_status": s.status if s else None,
            "suggestion_changed": (s.terms != terms(i)) if s else None,
            "suggestion_note": s.note if s else None,
            "suggestion_confidence": s.confidence if s else None, "suggested_at": s.created_at if s else None,
            "suggestion_answer": s.answer if s else None,
        }
        # Validated against the one definition: what a surface answers is an
        # OpenItem, whichever surface it is.
        out.append(OpenItem.model_validate(row).model_dump())
    return out


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if isinstance(value, str) else None
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


def sql_reader(tenant: Tenant, surface: str):
    """What a SQL session reads `exceptions.open_items` with: every item
    of the tenant and below, open and closed."""
    def make() -> Reader:
        return Reader(tenant, state="all", surface=surface, target="exceptions.open_items")

    return make


def _order(row: dict[str, Any]) -> tuple:
    """Oldest first, then the largest at stake, then by merchant and key,
    so the order is the same every time (okwan_recon.exceptions.oldest_first)."""
    since = row["dated_on"] or (row["first_seen"].date() if row["first_seen"] else None)
    return (since or date.max, -(row["amount_minor"] or 0), row["merchant_id"], row["item_key"])


async def listing(reader: Reader, limit: int) -> dict[str, Any]:
    """What the REST route and the MCP tool answer: the oldest `limit`
    items read, as JSON, how many were read in all, how many the checks'
    results found but did not keep, and what was not read. No more than
    twice `limit` items are held while it reads, besides the check being read."""
    kept: list[dict[str, Any]] = []
    total = 0
    async with aclosing(reader.batches()) as batches:
        async for batch in batches:
            total += len(batch)
            for row in batch:
                kept.append(row)
                if len(kept) > 2 * limit:
                    kept = sorted(kept, key=_order)[:limit]
    rows = sorted(kept, key=_order)[:limit]
    return {
        "data": [OpenItem.model_validate(r).model_dump(mode="json") for r in rows],
        "listed": len(rows),
        "total": total,
        "not_kept": reader.not_kept,
        "merchants_read": reader.merchants_read,
        "merchants_not_read": reader.merchants_not_read,
        "state": reader.state,
        "as_of": reader.now.isoformat(),
    }


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["exceptions"])

    @router.get("/exceptions", response_model=None)
    async def list_open_items(
        request: Request,
        merchant: str | None = Query(None, max_length=64, description="One tenant: the key's own or one below it"),
        check: str | None = Query(None, max_length=128, description="One check, by name"),
        state: Literal["open", "closed", "all"] = Query(
            "open", description="`closed`: what a person explained or resolved; `all`: both"),
        limit: int = Query(200, ge=1, le=LIST_MAX, description="Items listed, oldest first"),
        tenant=Depends(current_tenant),
    ) -> dict[str, Any]:
        """The open items of the key's tenant and every tenant below it, as
        `exceptions.open_items` holds them in SQL and `okwan_open_items`
        answers over MCP: oldest first, then largest, each with its newest
        decision and Okwan's newest suggestion. A read key may read it; no
        key decides here. Not metered; written to the access log."""
        reader = Reader(tenant, merchant=merchant, check=check, state=state, surface="rest:exceptions")
        try:
            return await listing(reader, limit)
        except TooManyReads as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": str(math.ceil(exc.retry_after))}) from exc
        except NoSuchMerchant as exc:
            raise HTTPException(404, f"no such tenant: {merchant}") from exc

    return router
