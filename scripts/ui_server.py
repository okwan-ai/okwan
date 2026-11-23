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
Suggestions on open items are answered by a scripted model that cites the
item itself (SeededModel), so the Decide panel's suggestion renders and can
be used or turned down; no model is called.
The workspace has three webhook addresses, one delivered to, one failing
and one turned off, so each state of Settings › Webhooks renders. Names
ending `.example` stand in for public hosts when an address is added
(`layout_dns`): they resolve to a documentation address no packet reaches,
so a test ping fails as an unreachable address does.
Dummy credentials; nothing leaves the machine; nothing is written to disk.

    python scripts/ui_server.py    # listens on 127.0.0.1:8000, prints "seeded"
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import uvicorn
from okwan_api import support, webhooks
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
#: A batch and its deposit from before the 120 days the check reads: on
#: file, in no line of the result, and said so on the tab.
OLD, OLD_PAID = (f"{datetime.now(UTC).date() - timedelta(days=n):%m/%d/%Y}" for n in (150, 149))

IMPORTS = [
    ("Processor batches.csv",
     ("Batch ID,Close Date,Gross,Fees,MID\n"
      f"B-1900,{OLD},500.00,14.50,MID-1\n"
      f"B-2001,{FRI},1840.00,53.36,MID-1\nB-2002,{SAT},920.50,26.69,MID-1\n"
      f"B-2003,{SUN},410.00,11.89,MID-1\nB-2004,{MON},1275.25,36.98,MID-1\n").encode(),
     {"kind": "settlement_batches", "name": "Processor batch report", "currency": "USD",
      "columns": {"batch_id": "Batch ID", "closed_on": "Close Date", "gross_minor": "Gross",
                  "fees_minor": "Fees", "mid": "MID"}}),
    ("Operating account statement.csv",
     (f"Date,Description,Amount\n{OLD_PAID},MERCHANT DEPOSIT,485.50\n{MON},MERCHANT DEPOSIT,1786.64\n"
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
    await seed_webhooks(store, isv.id)


async def seed_webhooks(store, account_id: str) -> None:
    """Three addresses: one a delivery reached, one failing for two hours
    (its next try a day away), one Okwan turned off. Each event is made
    here, not by a run, so the checks' runs above keep their own."""
    now = datetime.now(UTC)
    urls = ["https://hooks.acme-isv.example/okwan/events",
            ("https://ops-notifications.kofis-store-holdings.example/api/v2/integrations/okwan/webhooks/receive"
             "?team=finance-operations&region=west-africa&format=standard-webhooks"),
            "https://old-receiver.acme-isv.example/hooks"]
    made = [await store.add_webhook_endpoint(account_id, url=u, secret=webhooks.new_secret(), created_by="session",
                                             at=now - timedelta(days=3 - n)) for n, u in enumerate(urls)]
    await store.disable_webhook_endpoint(account_id, made[2].id, now - timedelta(days=1),
                                         "every delivery failed for three days (last: timed out)")
    events = [{"id": f"evt_{n:024x}", "tenant_id": account_id, "type": "open_item.opened", "kind": "pair",
               "check_name": "batches_deposits", "item_key": f"seed{n}", "reads_files": True,
               "body": webhooks.envelope(f"evt_{n:024x}", "open_item.opened", account_id, now, {})} for n in (1, 2)]
    await store.add_webhook_events(account_id, None, events, now - timedelta(hours=2))
    await store.webhook_delivered(account_id, made[0].id, events[0]["id"], at=now - timedelta(hours=2), status=200)
    await store.webhook_delivered(account_id, made[0].id, events[1]["id"], at=now - timedelta(hours=2), status=200)
    await store.webhook_delivered(account_id, made[1].id, events[0]["id"], at=now - timedelta(hours=2), status=200)
    await store.webhook_failed(account_id, made[1].id, events[1]["id"], at=now - timedelta(hours=2), status=503,
                               error="answered 503", next_at=now + timedelta(days=1), hold_until=None)


_real_resolve = webhooks.resolve_public


async def layout_dns(host: str, port: int, **kw) -> str:
    """A name ending `.example` stands in for a public host: it resolves to
    192.0.2.10 (TEST-NET-1), which no packet reaches. Every other name goes
    through the API's own check."""
    return "192.0.2.10" if host.endswith(".example") else await _real_resolve(host, port, **kw)


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


class SeededModel:
    """`client.beta.messages.create` for the layout check: a suggestion that
    explains the item from its own record, and a plain answer otherwise."""

    def __init__(self) -> None:
        self.beta = SimpleNamespace(messages=self)
        self.asked = 0

    async def create(self, **kwargs):
        if "format" not in (kwargs.get("output_config") or {}):
            text = "This is the layout check's assistant; it answers nothing."
        else:
            self.asked += 1
            shown = kwargs["messages"][0]["content"][0]["text"]
            evidence = [{"ref": "F1", "why": "The batch has no deposit in the days the statement covers."}]
            if '"ref": "D1"' in shown:
                evidence.append({"ref": "D1", "why": "Someone already looked into it."})
            text = json.dumps({
                "suggestion": "explained", "confidence": "medium",
                "note": f"Layout check suggestion {self.asked}: the batch closed on a Monday and is paid in the "
                        "deposit after the last day the statement covers.",
                "evidence": evidence})
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5",
                               content=[{"type": "text", "text": text}])


async def main() -> None:
    support.set_client(SeededModel())
    webhooks.resolve_public = layout_dns
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
