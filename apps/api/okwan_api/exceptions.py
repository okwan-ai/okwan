"""Every open finding a tenant and its merchants have, across their checks,
and what a person decided about each.

A check's stored run lists what needs a look; each check has its own page.
This answers the question a person starts the day with: what is open,
everywhere, how old is it and how much is at stake. Each item keeps the
same key from one run to the next (okwan_recon.exceptions).

The matching is rules, and the rules decide what is a finding. What a
finding means is a person's call: they give it to someone, explain it
(nothing to fix: a deposit due Monday, a fee the processor kept), resolve
it (fixed where it started) or open it again, and say why. Each decision is
added to the item's history and never changed, with the account and
credential that made it, so when an accountant asks why an item was closed
there is an answer. A read key, the kind an AI client is given, is refused:
whatever reads the list cannot close an item. An item explained or resolved
leaves the list, and does not come back however many runs find it again,
until a person opens it again.

Two views of the list, both bounded by construction:

- The account (no `merchant`): the target and its direct children, a page
  of MERCHANTS_PER_PAGE merchants at a time (`after`: the last merchant id
  of the page before; `next` says where the next page starts). It reads
  each check's newest good run without its rows, two statements a page for
  every check at once: the run's summary already holds its oldest open
  items, worked out once when it was stored, leaving out those closed by
  then. Decisions made since were made against that run, by its id, so the
  counts are brought up to date from them without reading a row. Each
  merchant has at most PER_MERCHANT items, taken in turn from each of its
  checks, so neither one merchant nor one check can push the others off;
  each check says how many findings it has open and closed.
- One merchant (`merchant` = the target or a direct child): every finding
  its checks' newest good runs kept, from their rows, keyed when stored;
  with `state=closed`, the ones a person closed.

Which run is newest is decided by when its data was read, so a slow run
that read earlier does not replace a newer one. Like the run history it
reads, the list runs nothing and is not metered, takes a dashboard session
or an admin key, and answers 404 for a tenant outside the caller's subtree.
The work that is left runs off the event loop.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from okwan_recon.exceptions import (
    aged,
    keyed,
    oldest_first,
    open_items,
    open_summary,
    run_found,
)
from okwan_recon.registry import all_across, all_reconciliations
from okwan_vault import CLOSED_STATUSES, Decision, DecisionConflict, ItemGone
from pydantic import BaseModel, ConfigDict, Field

from . import access
from .admin import _guard
from .auth import admin_actor, get_store
from .ratelimit import DECIDE_TENANT, EXCEPTIONS_TENANT, enforce
from .runs import closed_keys, file_check_names

#: Items of one merchant in the account's list, at most.
PER_MERCHANT = 50
#: Merchants one page of the account's list covers.
MERCHANTS_PER_PAGE = 50
#: Items one answer carries, at most.
MAX_ITEMS = 1000
#: Decisions one item's history answers with, newest first.
HISTORY_SHOWN = 200
#: What a person may write: whom an item is given to, and why.
OWNER_MAX, NOTE_MAX = 80, 1000
_IDEMPOTENCY = re.compile(r"[A-Za-z0-9._:\-]{1,128}")


def now() -> datetime:
    """The clock items are aged by, and decisions are dated by; a test moves it."""
    return datetime.now(UTC)


def _checks() -> list[tuple[str, str, str]]:
    """Every check a run can be stored for: (kind, name, title)."""
    out = [("across", s.name, s.display_title) for s in all_across()]
    out += [("pair", s.name, s.display_title) for s in all_reconciliations()]
    return out


def _titles() -> dict[tuple[str, str], str]:
    return {(kind, name): title for kind, name, title in _checks()}


# ── what a person writes ────────────────────────────────────────────

#: Characters that change how text reads without being seen: controls,
#: zero-width characters and the marks that reverse a line's direction.
#: A note is read by an accountant, a mail and an assistant; it must say
#: what it shows.
_UNSEEN = re.compile("[\x00-\x09\x0b-\x1f\x7f-\x9f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e"
                     "\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb]")


def _clean(value: str | None, what: str, *, lines: bool) -> str | None:
    """Text as a person wrote it, trimmed, or None when there is none.
    Refused (422) with a character that cannot be seen, or a line break
    where one line is asked for."""
    if value is None:
        return None
    text = value.replace("\r\n", "\n").strip()
    if not text:
        return None
    if _UNSEEN.search(text) or (not lines and "\n" in text):
        raise HTTPException(422, f"{what} has a character that cannot be seen; write it as plain text")
    return text


class DecisionIn(BaseModel):
    """A person's decision about one open item."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "assigned", "explained", "resolved"] = Field(
        description="`assigned` gives the item to `owner` and leaves it open; `explained` (nothing to "
                    "fix) and `resolved` (fixed where it started) close it; `open` puts back one closed")
    owner: str | None = Field(None, max_length=OWNER_MAX, description="Whom it is given to: required to assign")
    note: str | None = Field(None, max_length=NOTE_MAX,
                             description="Why: required to explain, resolve or open an item again")


def decision_dict(d: Decision) -> dict[str, Any]:
    return {
        "id": d.id, "seq": d.seq, "status": d.status, "prior_status": d.prior_status,
        "owner": d.owner, "note": d.note,
        "by": {"tenant_id": d.actor_tenant_id, "name": d.actor_name, "credential": d.credential},
        "at": d.created_at.isoformat(), "run_id": d.run_id,
    }


def _request_hash(kind: str, check: str, key: str, status: str, owner: str | None, note: str | None) -> str:
    text = json.dumps([kind, check, key, status, owner, note], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _holds(tenant_id: str, name: str, kind: str, rows, key: str) -> bool:
    """Whether a stored run's rows hold the finding with this key."""
    for computed, row in keyed(tenant_id, name, kind, rows or []):
        if computed is not None and (row.get("key") == key or computed == key):
            return True
    return False


# ── the list ────────────────────────────────────────────────────────

async def _open_of(tid: str, rec) -> dict[str, Any]:
    """The summary's open items. For a run stored before summaries kept
    them, the same worked out from its rows now, with the decisions as they
    stand now (`fresh`: none made since needs adding)."""
    kept = (rec.summary or {}).get("open")
    if isinstance(kept, dict) and isinstance(kept.get("oldest"), list):
        return kept
    full = await get_store().get_run(tid, rec.id)
    rows = full.rows if full is not None else None
    closed = await closed_keys(tid, rec.kind, rec.name, rows)
    worked = await asyncio.to_thread(open_summary, tid, rec.name, rec.kind, rows, rec.summary, closed)
    return {**worked, "fresh": True}


def _line(tid, name, title, kind, newest, good, opened, *, found=None, closed=None) -> dict[str, Any]:
    stored_found = int(opened.get("found") or 0) if opened else 0
    kept = int(opened.get("kept") or 0) if opened else 0
    return {
        "tenant_id": tid, "check": name, "title": title, "kind": kind,
        "last_run": ({"id": newest.id, "status": newest.status, "started_at": newest.started_at.isoformat(),
                      "finished_at": newest.finished_at.isoformat()} if newest else None),
        "findings_from": good.id if good else None,
        "open": stored_found if found is None else found,
        "closed": (int(opened.get("closed") or 0) if opened else 0) if closed is None else closed,
        # The run's stored rows hold fewer findings than it found.
        "cut": stored_found > kept,
        "check_version": (good.summary or {}).get("check_version") if good else None,
    }


def _in_turn(items: list[dict]) -> list[dict]:
    """A merchant's items, at most PER_MERCHANT, taken in turn from each of
    its checks, each check's oldest first: an old item of one check cannot
    keep every other check's off the list."""
    by_check: dict[str, list[dict]] = {}
    for i in sorted(items, key=oldest_first):
        by_check.setdefault(i["check"], []).append(i)
    out: list[dict] = []
    queues = [by_check[c] for c in sorted(by_check)]
    while len(out) < PER_MERCHANT and any(queues):
        for q in queues:
            if q and len(out) < PER_MERCHANT:
                out.append(q.pop(0))
    return out


def _account_items(per_tenant: dict[str, list[dict]], at: datetime) -> list[dict]:
    out: list[dict] = []
    for items in per_tenant.values():
        out += _in_turn(items)
    return [aged(i, at) for i in sorted(out, key=oldest_first)]


def _since_stored(decisions: list[Decision]) -> tuple[dict[tuple, int], dict[tuple, set[str]]]:
    """From the decisions made against each check's newest good run since
    it was stored: how many items the check closed, less those opened
    again, and which items were opened again (closed when the run was
    stored, so not among its kept items). Each item's first decision says
    how it stood when the run was stored, its last how it stands now."""
    by_item: dict[tuple, list[Decision]] = {}
    for d in decisions:
        by_item.setdefault((d.tenant_id, d.kind, d.name, d.key), []).append(d)
    delta: dict[tuple, int] = {}
    reopened: dict[tuple, set[str]] = {}
    for (tid, kind, name, key), ds in by_item.items():
        ds.sort(key=lambda d: d.seq)
        then, now_closed = ds[0].prior_status in CLOSED_STATUSES, ds[-1].closed
        delta[(tid, kind, name)] = delta.get((tid, kind, name), 0) + int(now_closed) - int(then)
        if then and not now_closed:
            reopened.setdefault((tid, kind, name), set()).add(key)
    return delta, reopened


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/tenants", tags=["tenants"])

    @router.get("/{tenant_id}/exceptions")
    async def list_exceptions(
        tenant_id: str,
        request: Request,
        merchant: str | None = Query(None, description="One merchant (the target or a direct child): all its open items"),
        after: str | None = Query(None, description="The account's list: the last merchant id of the page before"),
        state: Literal["open", "closed"] = Query(
            "open", description="`closed`: the items a person explained or resolved, one merchant at a time"),
        limit: int = Query(200, ge=1, le=MAX_ITEMS),
        actor=Depends(admin_actor),
    ) -> dict[str, Any]:
        """Open findings, oldest first and then largest, each with what a
        person last decided about it. Without `merchant`, those of the
        target and its direct children, a page of merchants at a time, each
        merchant's oldest PER_MERCHANT at most, taken in turn from its
        checks; with it, every finding of that one merchant. An item
        explained or resolved is not listed (`state=closed` lists those of
        one merchant). Each check's newest run is listed too, with whether
        it finished, so a check that failed since its findings were stored
        shows, and how many findings it has open and closed. Not metered."""
        await _guard(actor, tenant_id)
        enforce(request, (EXCEPTIONS_TENANT, tenant_id))
        if state == "closed" and merchant is None:
            raise HTTPException(422, "closed items are listed one merchant at a time: pass merchant")
        store = get_store()
        target = await store.get_tenant(tenant_id)
        if target is None:
            raise HTTPException(404, f"no such tenant: {tenant_id}")
        tenants = [target, *sorted(await store.children_of(tenant_id), key=lambda t: t.id)]
        names = {t.id: t.name for t in tenants}
        if merchant is not None and merchant not in names:
            raise HTTPException(404, f"no such tenant: {merchant}")
        if merchant is not None:
            ids, nxt = [merchant], None
        else:
            order = [t.id for t in tenants]
            start = order.index(after) + 1 if after in names else 0
            ids = order[start:start + MERCHANTS_PER_PAGE]
            nxt = ids[-1] if start + MERCHANTS_PER_PAGE < len(order) and ids else None
        titles = _titles()
        at = now()
        newest = {(r.tenant_id, r.kind, r.name): r for r in await store.newest_runs_all(ids)}
        good = {(r.tenant_id, r.kind, r.name): r
                for r in await store.newest_runs_all(ids, ok_only=True, with_rows=merchant is not None)}
        checks = [k for k in sorted(set(newest) | set(good)) if (k[1], k[2]) in titles]  # still declared

        if merchant is not None:
            lines, items = await _one_merchant(store, merchant, checks, titles, names, newest, good, state, at)
        else:
            lines, items = await _account(store, ids, checks, titles, names, newest, good, at)
        lines.sort(key=lambda c: (names[c["tenant_id"]], c["check"]))
        return {
            "data": items[:limit],
            "total": sum(c["closed" if state == "closed" else "open"] for c in lines),
            "listed": min(len(items), limit),
            "checks": lines,
            "next": nxt,
            "as_of": at.isoformat(),
        }

    @router.post("/{tenant_id}/exceptions/{kind}/{check}/{key}/decisions", status_code=201)
    async def decide(
        tenant_id: str,
        kind: str,
        check: str,
        key: str,
        body: DecisionIn,
        request: Request,
        response: Response,
        idempotency_key: str | None = Header(
            None, alias="Idempotency-Key",
            description="Required, one per decision (1 to 128 of A-Z a-z 0-9 . _ : -): a retry with the "
                        "same key and the same decision is answered with the one already made, never a second"),
        actor=Depends(admin_actor),
    ) -> dict[str, Any]:
        """Record a person's decision about one open item of `tenant_id` (the
        caller or one of its merchants): give it to someone, explain it,
        resolve it, or open it again, with why. The item is a finding in the
        check's newest good result. The decision is added to the item's
        history with the account and credential that made it and is never
        changed; a read key is refused. 201, or 200 for a retry."""
        await _guard(actor, tenant_id)
        enforce(request, (DECIDE_TENANT, actor.id))
        if kind not in ("across", "pair") or (kind, check) not in _titles():
            raise HTTPException(404, f"no such check: {kind}/{check}")
        if idempotency_key is None or not _IDEMPOTENCY.fullmatch(idempotency_key):
            raise HTTPException(422, "send an Idempotency-Key header: 1 to 128 of A-Z a-z 0-9 . _ : -, "
                                     "one per decision")
        owner = _clean(body.owner, "The owner", lines=False)
        note = _clean(body.note, "The note", lines=True)
        if body.status == "assigned" and owner is None:
            raise HTTPException(422, "say whom the item is given to (owner)")
        if body.status != "assigned" and note is None:
            raise HTTPException(422, "say why (note): an item is explained, resolved or opened again with a reason")
        digest = _request_hash(kind, check, key, body.status, owner, note)
        store = get_store()
        done = await store.decision_by_idempotency(tenant_id, idempotency_key)
        if done is not None:
            if done.request_hash != digest:
                raise HTTPException(409, "this Idempotency-Key was used for a different decision")
            response.status_code = 200
            return {"decision": decision_dict(done), "replayed": True}
        g = (await store.newest_runs([tenant_id], kind, check, ok_only=True, with_rows=True)).get(tenant_id)
        if g is None or not await asyncio.to_thread(_holds, tenant_id, check, kind, g.rows, key):
            raise HTTPException(404, "no such item: it is not a finding of the check's newest result")
        try:
            made, replayed = await store.add_decision(
                tenant_id, kind=kind, name=check, key=key, status=body.status, owner=owner, note=note,
                actor_tenant_id=actor.id, actor_name=actor.name, credential=access.credential_now(),
                run_id=g.id, reads_files=g.reads_files or check in file_check_names(),
                idempotency_key=idempotency_key, request_hash=digest, at=now())
        except DecisionConflict:
            raise HTTPException(409, "this Idempotency-Key was used for a different decision") from None
        except ItemGone:
            raise HTTPException(409, "the result this item was found in was just deleted; "
                                     "run the check again") from None
        if replayed:
            response.status_code = 200
        return {"decision": decision_dict(made), "replayed": replayed}

    @router.get("/{tenant_id}/exceptions/{kind}/{check}/{key}/decisions")
    async def history(
        tenant_id: str, kind: str, check: str, key: str, request: Request, actor=Depends(admin_actor),
    ) -> dict[str, Any]:
        """One item's decisions, newest first: who decided what, when and
        why. Its status is that of the newest; with none, it is open."""
        await _guard(actor, tenant_id)
        enforce(request, (EXCEPTIONS_TENANT, tenant_id))
        if kind not in ("across", "pair") or (kind, check) not in _titles():
            raise HTTPException(404, f"no such check: {kind}/{check}")
        found = await get_store().decision_history(tenant_id, kind, check, key, HISTORY_SHOWN)
        return {"data": [decision_dict(d) for d in found], "status": found[0].status if found else "open"}

    return router


def _with(item: dict, tid: str, names, title: str, kind: str, g, decision: Decision | None) -> dict:
    return {**item, "tenant_id": tid, "merchant": names[tid], "title": title, "kind": kind,
            "run_id": g.id, "run_at": g.finished_at.isoformat(),
            "decision": decision_dict(decision) if decision is not None else None}


async def _one_merchant(store, merchant, checks, titles, names, newest, good, state, at):
    """Every finding of one merchant's checks, from their rows, with each
    item's newest decision: those not closed, or with `state=closed`, those
    a person closed. Exact: the rows are read."""
    found: dict[tuple, list[dict]] = {}
    for k in checks:
        if k in good:
            g = good[k]
            found[k] = await asyncio.to_thread(open_items, k[0], k[2], k[1], g.rows, at)
    keys = [i["key"] for items in found.values() for i in items]
    current = await store.current_decisions([merchant], keys)
    lines: list[dict] = []
    out: list[dict] = []
    for k in checks:
        tid, kind, name = k
        title, n, g = titles[(kind, name)], newest.get(k), good.get(k)
        if g is None:
            lines.append(_line(tid, name, title, kind, n, None, None))
            continue
        every = found[k]
        shut, still = [], []
        for i in every:
            d = current.get((tid, kind, name, i["key"]))
            (shut if d is not None and d.closed else still).append((i, d))
        total = max(run_found(kind, g.summary), len(every))
        lines.append(_line(tid, name, title, kind, n, g, {"found": total - len(shut), "kept": len(still)},
                           closed=len(shut)))
        for i, d in shut if state == "closed" else still:
            out.append(_with(i, tid, names, title, kind, g, d))
    return lines, sorted(out, key=oldest_first)  # aged by open_items


async def _account(store, ids, checks, titles, names, newest, good, at):
    """The account's list from the stored summaries, brought up to date by
    the decisions made since each was stored."""
    opened: dict[tuple, dict] = {}
    for k in checks:
        if k in good:
            opened[k] = await _open_of(k[0], good[k])
    keys = [i.get("key") for o in opened.values() for i in o["oldest"] if isinstance(i, dict)]
    current = await store.current_decisions(ids, [k for k in keys if isinstance(k, str)])
    stored = {k: g for k, g in good.items() if k in opened and not opened[k].get("fresh")}
    made = [d for d in await store.decisions_on_runs(ids, [g.id for g in stored.values()])
            if (g := stored.get((d.tenant_id, d.kind, d.name))) is not None and d.run_id == g.id]
    since, reopened = _since_stored(made)
    on_run = {(d.tenant_id, d.kind, d.name, d.key) for d in made}
    latest = {(d.tenant_id, d.kind, d.name, d.key): d for d in sorted(made, key=lambda d: d.seq)}
    # An item opened again since its run was stored is not among the run's
    # kept items: it is read from that run's rows, for those checks only.
    back: dict[tuple, list[dict]] = {}
    for k, keys in reopened.items():
        full = await store.get_run(k[0], stored[k].id)
        found = await asyncio.to_thread(open_items, k[0], k[2], k[1], full.rows if full else None, at)
        back[k] = [i for i in found if i["key"] in keys]
    lines: list[dict] = []
    per_tenant: dict[str, list[dict]] = {tid: [] for tid in ids}
    for k in checks:
        tid, kind, name = k
        title, n, g = titles[(kind, name)], newest.get(k), good.get(k)
        if g is None:
            lines.append(_line(tid, name, title, kind, n, None, None))
            continue
        o = opened[k]
        delta = since.get(k, 0)  # 0 for a summary worked out now (`fresh`)
        kept: list[dict] = []
        for i in o["oldest"]:
            d = current.get((tid, kind, name, i.get("key")))
            if d is not None and d.closed:
                if (tid, kind, name, i.get("key")) not in on_run and not o.get("fresh"):
                    delta += 1  # closed against an older run as this one was stored
                continue
            kept.append(_with(i, tid, names, title, kind, g, d))
        for i in back.get(k, []):
            kept.append(_with(i, tid, names, title, kind, g, latest.get((tid, kind, name, i["key"]))))
        found = max(int(o.get("found") or 0) - delta, 0)
        lines.append(_line(tid, name, title, kind, n, g, o, found=found,
                           closed=int(o.get("closed") or 0) + delta))
        per_tenant[tid] += kept
    items = await asyncio.to_thread(_account_items, per_tenant, at)
    return lines, items
