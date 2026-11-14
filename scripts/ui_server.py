"""Serve the API with a seeded, in-memory vault, for the dashboard's layout
check (CI, `web (dashboard)`; docs/ops/RUNBOOK.md, "Layout check").

One workspace, *Acme ISV*, signs in as UI_CHECK_EMAIL with UI_CHECK_PASSWORD
(a throwaway the job generates). It has two merchants: *Kofi's Store*, with
every rail connected and one real cross-rail check whose rails are fixture
rows (six orders, #1002 and #1004 collected twice), and *Accra Coffee Co.*,
with only Stripe, so "needs you" states render too. Kofi's Store has
also imported a processor batch report and a bank statement, with their
mappings saved, and has run the batches-against-deposits check on them
(one batch paid alone, two paid as one deposit after a weekend, one never
paid); Accra has no files. Each check runs through
the API's own route, so the stored run is exactly what production stores.
The files' dates are a few weeks back from today, so the check, which
reads a fixed number of days, always has them to read.
The access log has a week of reads by Kofi's Store's key from its usual
network, today's reads from a new one with the alert they raised, and the
dashboard session's own run, so every column of Settings › Access log fills.
Dummy credentials; nothing leaves the machine; nothing is written to disk.

    python scripts/ui_server.py    # listens on 127.0.0.1:8000, prints "seeded"
"""
from __future__ import annotations

import asyncio
import base64
import os
from datetime import UTC, datetime, timedelta

import httpx
import uvicorn
from okwan_api.auth import get_store
from okwan_api.main import app
from okwan_recon import across
from okwan_recon.coverage import Coverage
from okwan_vault import cli
from okwan_vault.access import network_of
from okwan_vault.accounts import SESSION_PREFIX, new_token

EMAIL = os.environ.get("UI_CHECK_EMAIL", "layout@okwan.test")
PASSWORD = os.environ["UI_CHECK_PASSWORD"]
PORT = int(os.environ.get("UI_CHECK_API_PORT", "8000"))

AT = "2026-10-05T15:00:00Z"
EPOCH = int(datetime(2026, 10, 5, 15, 1, tzinfo=UTC).timestamp())


def order(name: str, total: int) -> dict:
    return {"name": name, "currency": "USD", "created_at": AT, "financial_status": "PAID",
            "fulfillment_status": "UNFULFILLED", "total_price_minor": total,
            "total_received_minor": total, "total_refunded_minor": 0, "net_payment_minor": total}


def paypal(ref: str, gross: int, txn: str) -> dict:
    fee = -(round(gross * 0.0349) + 49)
    return {"transaction_id": txn, "invoice_id": ref, "currency": "USD", "initiated_at": AT,
            "amount_minor": gross, "fee_minor": fee, "net_minor": gross + fee}


def stripe(ref: str, gross: int, charge: str) -> dict:
    fee = round(gross * 0.029) + 30
    return {"id": charge, "order_ref": ref, "currency": "usd", "created": EPOCH, "amount": gross,
            "amount_refunded": 0, "fee_minor": fee, "net_minor": gross - fee, "status": "succeeded"}


ROWS = {
    "shopify": [order("#1002", 99900), order("#1003", 15000), order("#1004", 45000),
                order("#1005", 12000), order("#1006", 7500), order("#1007", 26000)],
    "paypal": [paypal("#1002", 99900, "2WB33201VT644550V"), paypal("#1004", 45000, "1VC8010595264210L"),
               paypal("#1005", 12000, "5TY27149AB881320K"), paypal("#1007", 26000, "8HN61022CD447915M")],
    "stripe": [stripe("#1002", 99900, "ch_layout_1002"), stripe("#1003", 15000, "ch_layout_1003"),
               stripe("#1004", 45000, "ch_layout_1004"), stripe("#1006", 7500, "ch_layout_1006")],
}

def _week() -> list[str]:
    """A Friday two to three weeks back and the five days after it, as a
    US export writes dates: inside the check's lookback whenever this runs."""
    today = datetime.now(UTC).date()
    friday = today - timedelta(days=14 + (today.weekday() - 4) % 7)
    return [f"{friday + timedelta(days=n):%m/%d/%Y}" for n in range(6)]


FRI, SAT, SUN, MON, TUE, WED = _week()

IMPORTS = [
    ("Processor batches.csv",
     ("Batch ID,Close Date,Gross,Fees,MID\n"
      f"B-2001,{FRI},1840.00,53.36,MID-1\nB-2002,{SAT},920.50,26.69,MID-1\n"
      f"B-2003,{SUN},410.00,11.89,MID-1\nB-2004,{MON},1275.25,36.98,MID-1\n").encode(),
     {"kind": "settlement_batches", "name": "Processor batch report", "currency": "USD",
      "columns": {"batch_id": "Batch ID", "closed_on": "Close Date", "gross_minor": "Gross",
                  "fees_minor": "Fees", "mid": "MID"}}),
    ("Operating account statement.csv",
     (f"Date,Description,Amount\n{MON},MERCHANT DEPOSIT,1786.64\n"
      f"{TUE},MERCHANT DEPOSIT,1291.92\n{WED},MONTHLY FEE,-25.00\n").encode(),
     {"kind": "bank_deposits", "name": "Operating account", "currency": "USD",
      "columns": {"posted_on": "Date", "description": "Description", "amount_minor": "Amount"}}),
]

DUMMY = {"stripe": {"secret_key": "sk_test_dummy"},
         "paypal": {"client_id": "dummy", "client_secret": "dummy"},
         "shopify": {"access_token": "shpat_dummy", "shop_domain": "kofi.myshopify.com"}}


async def fixture_side(ref, resolver, cap, overrides):
    """The rails, replaced by fixture rows: the only thing not real here."""
    rows = ROWS[ref.connector]
    return rows, Coverage(source=ref.qualified, records=len(rows), cap=cap, truncated=False)


async def seed() -> None:
    store = get_store()
    isv = await store.create_tenant("Acme ISV")
    await cli.add_account(store, isv.id, EMAIL, PASSWORD)
    kofi = await store.create_tenant("Kofi's Store", parent_id=isv.id)
    for connector, fields in DUMMY.items():
        for field, value in fields.items():
            await store.put_credential(kofi.id, connector, field, value)
    accra = await store.create_tenant("Accra Coffee Co.", parent_id=isv.id)
    await store.put_credential(accra.id, "stripe", "secret_key", "sk_test_dummy")

    token, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(isv.id, token_hash, datetime.now(UTC) + timedelta(minutes=5))
    across.fetch_side = fixture_side
    auth = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{PORT}", timeout=30) as client:
        r = await client.post(f"/v1/tenants/{kofi.id}/reconciliations/across/rails", headers=auth)
        r.raise_for_status()
        # Kofi's Store has imported a batch report and a bank statement, and
        # saved the statement's mapping; Accra has none, so both states render.
        for name, data, mapping in IMPORTS:
            r = await client.put(f"/v1/tenants/{kofi.id}/import-layouts/{mapping['name']}",
                                 headers=auth, json=mapping)
            r.raise_for_status()
            r = await client.post(f"/v1/tenants/{kofi.id}/imports", headers=auth, json={
                "filename": name, "content_base64": base64.b64encode(data).decode(), "layout": mapping["name"]})
            r.raise_for_status()
        # And has checked the batches against the deposits: the Files tab
        # shows a stored result, with a batch that was never paid.
        r = await client.post(f"/v1/tenants/{kofi.id}/reconciliations/batches_deposits", headers=auth)
        r.raise_for_status()
    await store.delete_session(token_hash)
    await seed_access(store, kofi.id)


async def seed_access(store, tenant_id: str) -> None:
    """A key that has read from one office for a week, then from somewhere
    new today, and the alert that raised. Documentation addresses."""
    _, key = await store.issue_key(tenant_id, scope="read")
    now = datetime.now(UTC)
    reads = [("rest:stripe", "/v1/stripe/charges/list"), ("rest:query", "/v1/query"),
             ("mcp:reconcile", "okwan_reconcile shopify_stripe"), ("rest:paypal", "/v1/paypal/transactions/list")]
    for day in range(7, 0, -1):
        for n, (surface, target) in enumerate(reads):
            await store.record_access(tenant_id, credential=key.prefix, ip="198.51.100.20",
                                      network=network_of("198.51.100.20"), surface=surface, target=target,
                                      at=now - timedelta(days=day, minutes=n * 7))
    for n, (surface, target) in enumerate(reads[:2]):
        await store.record_access(tenant_id, credential=key.prefix, ip="203.0.113.7",
                                  network=network_of("203.0.113.7"), surface=surface, target=target,
                                  at=now - timedelta(minutes=40 - n * 5))
    alert = await store.add_access_alert(tenant_id, kind="new_network", credential=key.prefix, at=now - timedelta(minutes=40),
                                 detail={"ip": "203.0.113.7", "network": "203.0.113.0/24", "surface": "rest:stripe",
                                         "target": "/v1/stripe/charges/list",
                                         "at": (now - timedelta(minutes=40)).isoformat()})
    await store.set_alert_mailed(tenant_id, alert["id"])  # as production's mailer would


async def main() -> None:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        if serving.done():
            serving.result()  # startup failed: raise its error
        await asyncio.sleep(0.05)
    await seed()
    print("seeded", flush=True)
    await serving


if __name__ == "__main__":
    asyncio.run(main())
