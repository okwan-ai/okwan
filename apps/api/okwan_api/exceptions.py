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
import unicodedata
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from okwan_recon.exceptions import (
    aged,
    holds,
    items_by_key,
    oldest_first,
    open_items,
    open_summary,
    run_found,
    terms,
)
from okwan_recon.registry import all_across, all_reconciliations
from okwan_vault import (
    ITEM_DECISIONS_MAX,
    ITEM_SUGGESTIONS_MAX,
    Decision,
    DecisionConflict,
    HistoryFull,
    ItemBusy,
    ItemGone,
    SuggestionAnswered,
    ancestors,
)
from okwan_vault.authz import MAX_DEPTH
from pydantic import BaseModel, ConfigDict, Field

from . import access
from .admin import _guard
from .auth import admin_actor, get_store
from .ratelimit import DECIDE_CALLER, DECIDE_TENANT, EXCEPTIONS_TENANT, enforce
from .runs import closed_keys, file_check_names

#: Items of one merchant in the account's list, at most.
PER_MERCHANT = 50
#: Merchants one page of the account's list covers.
MERCHANTS_PER_PAGE = 50
#: Items one answer carries, at most.
MAX_ITEMS = 1000
#: Decisions one item's history answers with, newest first: all of them,
#: since each account that may decide it (the item's own and those above
#: it) records at most ITEM_DECISIONS_MAX.
HISTORY_SHOWN = ITEM_DECISIONS_MAX * (MAX_DEPTH + 1)
#: Checks whose stored rows the account's list reads in one request, for
#: items opened again since their run was stored; the rest are counted.
REOPENED_READ_MAX = 3
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

#: Code points Unicode says to draw as nothing that are not format
#: characters (Cf): a combining grapheme joiner, the Hangul fillers, Khmer's
#: inherent vowels, the variation selectors, and the blank braille cell.
_IGNORABLE = frozenset(
    [0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0]
    + list(range(0x180B, 0x1810)) + list(range(0xFE00, 0xFE10)) + list(range(0xE0100, 0xE01F0)))
#: Categories that are never seen: controls, format characters (zero-width
#: characters, the marks that reverse a line's direction), private use,
#: surrogates, unassigned code points, and line and paragraph separators.
_UNSEEN_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"})


def _unseen(c: str) -> bool:
    """Whether a character changes how text reads without being seen. A
    note is read by an accountant, a mail and an assistant; it must say what
    it shows. The dashboard tests the same (apps/dashboard/lib/file-checks.ts).
    Also a right-to-left sign that is not a letter (a Hebrew gershayim looks
    like a quote mark): the figures between two of them are drawn in the
    other order. Right-to-left letters are written and seen as such."""
    if unicodedata.category(c) in _UNSEEN_CATEGORIES or ord(c) in _IGNORABLE:
        return True
    return unicodedata.bidirectional(c) in ("R", "AL") and unicodedata.category(c)[0] not in "LM"


def _clean(value: str | None, what: str, *, lines: bool) -> str | None:
    """Text as a person wrote it, trimmed, or None when there is none. A tab,
    or a space of another width (a no-break space pasted from a sheet),
    becomes a space. Refused (422) with a character that cannot be seen, or
    a line break where one line is asked for."""
    if value is None:
        return None
    text = "".join(" " if c == "\t" or unicodedata.category(c) == "Zs" else c
                   for c in value.replace("\r\n", "\n")).strip()
    if not text:
        return None
    if any(c != "\n" and _unseen(c) for c in text) or (not lines and "\n" in text):
        raise HTTPException(422, f"{what} has a character that cannot be seen, or that changes the order "
                                 "the text around it is drawn in; write it as plain text")
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
    seen: str | None = Field(None, max_length=200,
                             description="The item's `terms` as the list showed them to the person deciding: "
                                         "the decision is refused (409) if the item has changed since")
    suggestion: str | None = Field(
        None, pattern=r"^sug_[0-9a-f]{16}$",
        description="The suggestion this decision is made from, if any (POST …/suggestions). The decision is "
                    "the person's: it records whether they kept the suggested words (`as_suggested`: "
                    "explained, with the suggested note) or changed them (`edited`). Refused once the "
                    "suggestion is answered, or when the item has changed since it was made")


def _on(text: str) -> dict[str, Any] | None:
    """A decision's terms as the item stood: its amount, currency and day."""
    try:
        amount, currency, day = json.loads(text)
    except (ValueError, TypeError):
        return None
    return {"amount_minor": amount, "currency": currency, "dated_on": day}


def decision_dict(d: Decision, item: dict | None = None) -> dict[str, Any]:
    """A decision as the API answers it. `on`: what the item put at stake
    when it was decided. `changed`, for one that closed the item: the item
    has other terms now, or is alike to another finding, so the decision no
    longer closes it."""
    out: dict[str, Any] = {
        "id": d.id, "seq": d.seq, "status": d.status, "prior_status": d.prior_status,
        "owner": d.owner, "note": d.note,
        "by": {"tenant_id": d.actor_tenant_id, "name": d.actor_name, "credential": d.credential},
        "at": d.created_at.isoformat(), "run_id": d.run_id, "on": _on(d.terms),
        "suggestion": {"id": d.suggestion_id, "as": d.from_suggestion} if d.suggestion_id else None,
    }
    if item is not None and d.closed:
        out["changed"] = not holds(d, item)
        if out["changed"]:
            out["changed_why"] = "alike" if item.get("alike") else "terms"
    return out


def _request_hash(kind: str, check: str, key: str, status: str, owner: str | None, note: str | None,
                  actor_id: str, credential: str, suggestion: str | None = None) -> str:
    """What a retry must repeat to be answered with the decision already
    made: the same decision, from the same suggestion, by the same account
    and credential."""
    parts: list[Any] = [kind, check, key, status, owner, note, actor_id, credential]
    if suggestion is not None:
        parts.append(suggestion)
    text = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()




async def _is_suggested(store, tenant_id: str, kind: str, check: str, key: str, note: str) -> bool:
    """Whether a note is word for word one of Okwan's suggestions on the
    item, turned down or not: such a note is never recorded as a person's
    own words."""
    shown = ITEM_SUGGESTIONS_MAX * (MAX_DEPTH + 1)
    return any(s.note == note for s in await store.suggestions_of(tenant_id, kind, check, key, shown))


async def _from_suggestion(store, tenant_id: str, kind: str, check: str, key: str, item: dict,
                           status: str, note: str | None, suggestion_id: str, run_id: str) -> str:
    """How a decision is made from a suggestion: `as_suggested` when its
    note is the suggestion's words, word for word, else `edited`. Refused for
    another item's suggestion, one already answered, or one made from
    another result than the check's newest (`run_id`): the records it rests
    on are no longer the result, even where the item's terms came back."""
    if status == "open":
        raise HTTPException(422, "a suggestion is decided from to explain, resolve or assign an item, "
                                 "not to open one again")
    s = await store.get_suggestion(tenant_id, suggestion_id)
    if s is None or (s.kind, s.name, s.key) != (kind, check, key):
        raise HTTPException(404, "no such suggestion on this item")
    if s.answer is not None:
        raise HTTPException(409, "this suggestion was already answered: decided from, or turned down")
    if s.run_id != run_id or s.terms != terms(item):
        raise HTTPException(409, "this item's check has a newer result than the one the suggestion was made "
                                 "from, so the records it rests on may have changed: ask for a new one")
    return "as_suggested" if note == s.note else "edited"


async def _root(store, tenant_id: str) -> str:
    """The account at the top of a tenant's tree: decisions are limited per
    account, so a platform's merchants and their keys share one allowance."""
    above = await ancestors(store, tenant_id)
    return above[-1] if above else tenant_id


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


def _since_stored(made: list[tuple[Decision, bool]],
                  listed: set[tuple]) -> tuple[dict[tuple, int], dict[tuple, set[str]]]:
    """From the items decided against each check's newest good run since it
    was stored (each with its newest decision, and whether the first found it
    closed): how many items the check closed, less those opened again, and
    which items were opened again. An item among the run's kept items was
    open when the run was stored, whatever its first decision found: that
    one was recorded against the run before, as this one was being stored.
    A decision against the run was made on the run's own terms, so its
    status is how the item stands."""
    delta: dict[tuple, int] = {}
    reopened: dict[tuple, set[str]] = {}
    for last, first_before in made:
        check = (last.tenant_id, last.kind, last.name)
        then = first_before and (*check, last.key) not in listed
        delta[check] = delta.get(check, 0) + int(last.closed) - int(then)
        if then and not last.closed:
            reopened.setdefault(check, set()).add(last.key)
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
        enforce(request, (EXCEPTIONS_TENANT, f"{actor.id}>{tenant_id}"))
        if state == "closed" and merchant is None:
            raise HTTPException(422, "closed items are listed one merchant at a time: pass merchant")
        store = get_store()
        target = await store.get_tenant(tenant_id)
        if target is None:
            raise HTTPException(404, f"no such tenant: {tenant_id}")
        tenants = [target, *sorted(await store.children_of(tenant_id), key=lambda t: t.id)]
        names = {t.id: t.name for t in tenants}
        if merchant is not None and merchant not in names:
            # A merchant further down, as far as the account can open
            # (authz.MAX_DEPTH), is listed on its own: the daily mail links
            # to every merchant it names. Anything else reads as missing.
            deeper = await store.get_tenant(merchant)
            if deeper is None or tenant_id not in await ancestors(store, merchant):
                raise HTTPException(404, f"no such tenant: {merchant}")
            names[merchant] = deeper.name
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
        changed; a read key is refused. 201, or 200 for a retry. A closing
        decision closes the item while it puts the same amount at stake, in
        the same currency, on the same day: changed, it is open again. A
        finding alike in every reference and day to another of the same
        result cannot be decided on its own (409). Limited per account, its
        merchants together."""
        await _guard(actor, tenant_id)
        store = get_store()
        enforce(request, (DECIDE_CALLER, actor.id))
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
        credential = access.credential_now()
        digest = _request_hash(kind, check, key, body.status, owner, note, actor.id, credential, body.suggestion)
        done = await store.decision_by_idempotency(tenant_id, idempotency_key)
        if done is not None:
            if done.request_hash != digest:
                raise HTTPException(409, "this Idempotency-Key was used for a different decision")
            response.status_code = 200
            return {"decision": decision_dict(done), "replayed": True}
        g = (await store.newest_runs([tenant_id], kind, check, ok_only=True, with_rows=True)).get(tenant_id)
        items = await asyncio.to_thread(items_by_key, tenant_id, check, kind, g.rows if g else None)
        if g is None or key not in items:
            raise HTTPException(404, "no such item: it is not a finding of the check's newest result")
        item = items[key]
        if body.seen is not None and body.seen != terms(item):
            raise HTTPException(409, "this item has changed since it was shown (what it puts at stake, or its "
                                     "day): look at it again before deciding")
        if item.get("alike"):
            raise HTTPException(409, "this finding is alike, in every reference and day, to another one in the same "
                                     "result (two terminals that each closed batch 001 that day, say). They are told "
                                     "apart only by the order the result lists them in, which can change from one run "
                                     "to the next, so a decision could not be pinned to this one")
        source = None
        if body.suggestion is not None:
            source = await _from_suggestion(store, tenant_id, kind, check, key, item, body.status, note,
                                            body.suggestion, g.id)
        elif note is not None and await _is_suggested(store, tenant_id, kind, check, key, note):
            raise HTTPException(422, "this note is one of Okwan's suggestions on this item, word for word: decide "
                                     "from the suggestion, so the record says whose words they are, or write it in "
                                     "your own words")
        if body.status == "assigned" and note is None:
            now_on = (await store.current_decisions([(tenant_id, key)])).get((tenant_id, kind, check, key))
            if holds(now_on, item):
                raise HTTPException(422, "this item is closed: say why it is open again (note) as you give it "
                                         "to someone")
        # The account's allowance, its merchants together, is spent only on a
        # decision about to be recorded.
        enforce(request, (DECIDE_TENANT, await _root(store, tenant_id)))
        try:
            made, replayed = await store.add_decision(
                tenant_id, kind=kind, name=check, key=key, status=body.status, owner=owner, note=note,
                actor_tenant_id=actor.id, actor_name=actor.name, credential=credential,
                run_id=g.id, reads_files=g.reads_files or check in file_check_names(),
                idempotency_key=idempotency_key, request_hash=digest, at=now(), terms=terms(item),
                suggestion_id=body.suggestion, from_suggestion=source)
        except DecisionConflict:
            raise HTTPException(409, "this Idempotency-Key was used for a different decision") from None
        except ItemGone:
            raise HTTPException(409, "the result this item was found in was just deleted; "
                                     "run the check again") from None
        except HistoryFull:
            raise HTTPException(409, f"this account already recorded {ITEM_DECISIONS_MAX} decisions on this item; "
                                     "no more of its decisions are recorded on it") from None
        except ItemBusy:
            raise HTTPException(409, "another decision on this item is being recorded; send this one again",
                                headers={"Retry-After": "1"}) from None
        except SuggestionAnswered:
            raise HTTPException(409, "this suggestion was already answered: decided from, or turned down") from None
        if replayed:
            response.status_code = 200
        return {"decision": decision_dict(made), "replayed": replayed}

    @router.get("/{tenant_id}/exceptions/{kind}/{check}/{key}/decisions")
    async def history(
        tenant_id: str, kind: str, check: str, key: str, request: Request, actor=Depends(admin_actor),
    ) -> dict[str, Any]:
        """One item's decisions, newest first: who decided what, when and
        why. Its status is that of the newest; with none, it is open. Each
        account that may decide an item records at most ITEM_DECISIONS_MAX on
        it, so this is all of them."""
        await _guard(actor, tenant_id)
        enforce(request, (EXCEPTIONS_TENANT, f"{actor.id}>{tenant_id}"))
        if kind not in ("across", "pair") or (kind, check) not in _titles():
            raise HTTPException(404, f"no such check: {kind}/{check}")
        found = await get_store().decision_history(tenant_id, kind, check, key, HISTORY_SHOWN)
        return {"data": [decision_dict(d) for d in found], "status": found[0].status if found else "open"}

    return router


def _with(item: dict, tid: str, names, title: str, kind: str, g, decision: Decision | None) -> dict:
    return {**item, "tenant_id": tid, "merchant": names[tid], "title": title, "kind": kind,
            "run_id": g.id, "run_at": g.finished_at.isoformat(), "terms": terms(item),
            "decision": decision_dict(decision, item) if decision is not None else None}


async def _one_merchant(store, merchant, checks, titles, names, newest, good, state, at):
    """Every finding of one merchant's checks, from their rows, with each
    item's newest decision: those not closed, or with `state=closed`, those
    a person closed. Exact: the rows are read."""
    found: dict[tuple, list[dict]] = {}
    for k in checks:
        if k in good:
            g = good[k]
            found[k] = await asyncio.to_thread(open_items, k[0], k[2], k[1], g.rows, at)
    current = await store.current_decisions([(merchant, i["key"]) for items in found.values() for i in items])
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
            (shut if holds(d, i) else still).append((i, d))
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
    listed = {(*k, i["key"]) for k, o in opened.items() for i in o["oldest"]
              if isinstance(i, dict) and isinstance(i.get("key"), str)}
    current = await store.current_decisions([(tid, key) for tid, _, _, key in listed])
    stored = {k: g for k, g in good.items() if k in opened and not opened[k].get("fresh")}
    made = [(d, first) for d, first in await store.decided_on_runs(ids, [g.id for g in stored.values()])
            if (g := stored.get((d.tenant_id, d.kind, d.name))) is not None and d.run_id == g.id]
    since, reopened = _since_stored(made, listed)
    latest = {(d.tenant_id, d.kind, d.name, d.key): d for d, _ in made}
    # An item opened again since its run was stored is not among the run's
    # kept items: it is read from that run's rows, for at most
    # REOPENED_READ_MAX checks a request. The others are counted; the
    # merchant's own list, which reads its rows, shows them.
    back: dict[tuple, list[dict]] = {}
    for k, keys in sorted(reopened.items())[:REOPENED_READ_MAX]:
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
            if holds(d, i):
                if (tid, kind, name, i.get("key")) not in latest and not o.get("fresh"):
                    delta += 1  # closed against the run before, as this one was being stored
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
