"""An account's sandbox: test keys, sample data, nothing live.

A platform builds its integration against Okwan before it points it at a
real merchant. The sandbox is where: a tree of tenants of its own, apart
from every live one by the same subtree guards and row-level security
that keep accounts apart, so a test key reaches only it and a live key
never does. Every route works there as it does live, merchants, keys,
checks, open items, decisions and webhooks included.

- **Keys.** A sandbox's keys start `okw_test_`; the tenant decides, never
  the caller (okwan_vault.apikey).
- **Data.** The sandbox and every merchant made in it start with the
  sample café's week (docs/samples/imports): its processor's batch report
  and its bank statement, imported as files, so the batches-against-
  deposits check finds what the samples' README says, from the first
  call. The week is moved forward by whole weeks to end within the last
  seven days, so it stays inside the check's 120 days and keeps its
  weekdays.
- **No rails.** A sandbox holds no rail credential: saving one, or
  connecting Gusto, is refused, and the store reads every credential of a
  sandbox as empty whatever is stored. Connecting rails' own test modes
  is the next step.
- **Its own allowance.** Requests in the sandbox count against the
  sandbox, which is on the free plan, never against the live account's.
- It gets no daily check and no mail; it goes with its account.

Made from the live account (`POST /v1/sandbox`, an admin key or a
dashboard session), which also issues and revokes its keys.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from okwan_files import Mapping, prepare
from okwan_vault import ADMIN, READ, SandboxRefused
from okwan_vault.authz import ancestors
from pydantic import BaseModel, Field

from .auth import admin_actor, get_store
from .ratelimit import SANDBOX_CHANGE, enforce

#: The sample week as docs/samples/imports holds it (tests/test_sandbox.py
#: keeps the two the same): the processor's batch report and the bank's
#: statement, with the mappings the samples' README gives.
BATCHES_CSV = """\
Batch ID,Close Date,Gross Sales,Fees,Chargebacks,Net Deposit,MID
B-1005,10/05/2026,1284.50,37.25,0.00,1247.25,MID-4471
B-1006,10/06/2026,963.20,27.93,0.00,935.27,MID-4471
B-1007,10/07/2026,1105.75,32.07,0.00,1073.68,MID-4471
B-1008,10/08/2026,1422.10,41.24,0.00,1380.86,MID-4471
B-1009,10/09/2026,1876.40,54.42,0.00,1821.98,MID-4471
B-1010,10/10/2026,2104.85,61.04,0.00,2043.81,MID-4471
B-1011,10/11/2026,1350.00,39.15,0.00,1310.85,MID-4471
B-1012,10/12/2026,990.30,28.72,0.00,961.58,MID-4471
B-1013,10/13/2026,1018.60,29.54,45.00,944.06,MID-4471
"""
STATEMENT_CSV = """\
Date,Description,Amount,Reference
10/06/2026,MERCHANT DEPOSIT MID-4471,1247.25,20261006001
10/07/2026,MERCHANT DEPOSIT MID-4471,935.27,20261007001
10/08/2026,MERCHANT DEPOSIT MID-4471,1073.68,20261008001
10/09/2026,MERCHANT DEPOSIT MID-4471,1380.86,20261009001
10/13/2026,MERCHANT DEPOSIT MID-4471,6138.22,20261013001
10/15/2026,MONTHLY SERVICE FEE,-35.00,20261015001
10/16/2026,MOBILE DEPOSIT,312.40,20261016001
"""
BATCHES = Mapping(kind="settlement_batches", currency="USD", name="sample-processor-batches", columns={
    "batch_id": "Batch ID", "closed_on": "Close Date", "gross_minor": "Gross Sales", "fees_minor": "Fees",
    "chargebacks_minor": "Chargebacks", "net_minor": "Net Deposit", "mid": "MID"})
STATEMENT = Mapping(kind="bank_deposits", currency="USD", name="sample-bank-statement", columns={
    "posted_on": "Date", "description": "Description", "amount_minor": "Amount", "fitid": "Reference"})
SEED = (("sample-processor-batches.csv", BATCHES_CSV, BATCHES),
        ("sample-bank-statement.csv", STATEMENT_CSV, STATEMENT))
#: The week's last day, as the samples have it.
LAST_DAY = date(2026, 10, 16)

#: A day as the sample files write it, in whichever column holds it.
_DAY = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")


def moved(text: str, today: date) -> str:
    """The file with each row's day moved forward by whole weeks, so the
    week's last day falls within the seven days up to `today`."""
    weeks = max(0, (today - LAST_DAY).days // 7)

    def shift(m: re.Match) -> str:
        day = date(int(m[3]), int(m[1]), int(m[2])) + timedelta(weeks=weeks)
        return f"{day:%m/%d/%Y}"

    return _DAY.sub(shift, text)


async def seed(tenant_id: str, now: datetime | None = None) -> None:
    """Import the sample week into a sandbox tenant, as an upload would
    store it. Only a sandbox is seeded."""
    store = get_store()
    tenant = await store.get_tenant(tenant_id)
    if tenant is None or not tenant.sandbox:
        raise SandboxRefused("only a sandbox is given the sample week")
    today = (now or datetime.now(UTC)).date()
    for filename, text, mapping in SEED:
        prepared = prepare(moved(text, today).encode(), filename, mapping)
        n = prepared.normalized
        await store.add_import(
            tenant_id, kind=mapping.kind, filename=filename, format=prepared.format, layout=mapping.name,
            mapping=mapping.model_dump(mode="json"), sha256=prepared.sha256, rows=n.rows, skipped=n.skipped,
            card_columns=n.card_columns, first_date=prepared.first_date, last_date=prepared.last_date)


async def seeded(tenant_id: str) -> None:
    """Seed a tenant just made; if that fails, the tenant goes too, so the
    next request starts again rather than leaving one without its week."""
    try:
        await seed(tenant_id)
    except BaseException:
        await get_store().delete_tenant(tenant_id)
        raise


def _out(t) -> dict[str, Any]:
    return {"id": t.id, "name": t.name, "sandbox_of": t.sandbox_of, "api_version": t.api_version,
            "created_at": t.created_at.isoformat()}


async def _account(actor):
    """The live account asking, or the reason it cannot."""
    if actor.sandbox:
        raise HTTPException(403, "this is the sandbox: its live account manages it")
    if actor.parent_id is not None:
        raise HTTPException(403, "the sandbox is the account's: use the account's admin key or the dashboard")
    return actor


class SandboxKeyIn(BaseModel):
    scope: str = Field(READ, pattern=f"^({READ}|{ADMIN})$",
                       description="`read` (the default) or `admin`, as a live key; it starts `okw_test_`")


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/sandbox", tags=["sandbox"])

    @router.post("")
    async def make(request: Request, response: Response, actor=Depends(admin_actor)) -> dict[str, Any]:
        """The account's sandbox: made the first time (201), with the sample
        week in it, and the same one after (200). Its keys are issued here."""
        account = await _account(actor)
        enforce(request, (SANDBOX_CHANGE, account.id))
        made, new = await get_store().make_sandbox(account.id)
        if new:
            await seeded(made.id)
        response.status_code = 201 if new else 200
        return _out(made)

    @router.get("")
    async def show(actor=Depends(admin_actor)) -> dict[str, Any]:
        """The account's sandbox, or 404 when it has none yet."""
        account = await _account(actor)
        found = await get_store().sandbox_for(account.id)
        if found is None:
            raise HTTPException(404, "no sandbox yet: POST /v1/sandbox makes it")
        return _out(found)

    @router.post("/keys", status_code=201)
    async def issue(request: Request, body: SandboxKeyIn | None = None,
                    actor=Depends(admin_actor)) -> dict[str, Any]:
        """A test key for the sandbox (`okw_test_…`), shown once. It reaches
        the sandbox and nothing else."""
        account = await _account(actor)
        enforce(request, (SANDBOX_CHANGE, account.id))
        store = get_store()
        found = await store.sandbox_for(account.id)
        if found is None:
            raise HTTPException(404, "no sandbox yet: POST /v1/sandbox makes it")
        full, record = await store.issue_key(found.id, scope=(body.scope if body else READ))
        return {"key_id": record.id, "prefix": record.prefix, "scope": record.scope, "tenant_id": found.id,
                "secret": full, "note": "shown once — store it now"}

    @router.delete("/keys/{key_id}", status_code=204)
    async def revoke(key_id: str, actor=Depends(admin_actor)) -> Response:
        """Revoke a key of the sandbox or of any merchant in it, from the
        live account: a lost test key is stopped without it."""
        account = await _account(actor)
        store = get_store()
        found = await store.sandbox_for(account.id)
        owner = await store.key_owner(key_id)
        missing = HTTPException(404, f"no such key: {key_id}")
        if found is None or owner is None:
            raise missing
        if owner != found.id and found.id not in await ancestors(store, owner):
            raise missing
        await store.revoke_key(key_id)
        return Response(status_code=204)

    return router
