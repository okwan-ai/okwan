"""Every run as a tenant is stored; the dashboard reads results instead
of running them.

What would fail silently: a run that reaches the store with a raw rail
record on it (a customer's email, a card token), a failed run stored
with the credential a rail echoed, a stored run readable through another
tenant's id, a read that moves the meter, a recording failure that fails
the run, or a surface (REST, MCP) that runs without recording.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from okwan_core import UpstreamError
from okwan_recon import across
from okwan_recon.coverage import Coverage
from okwan_vault import EnvMasterKey, MemoryStore, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token

AT = "2026-08-27T15:00:00Z"
EPOCH = int(datetime(2026, 8, 27, 15, 1, tzinfo=UTC).timestamp())
SECRET = "sk_merchant_SECRET_9f8e7d"
EMAIL = "kofi.customer@example.com"
CARD = "tok_card_4242_fingerprint_ZZ"

VAULT = {"stripe": {"secret_key": SECRET},
         "paypal": {"client_id": "pp_merchant", "client_secret": "pp_merchant_s"},
         "shopify": {"access_token": "shpat_merchant", "shop_domain": "kofi.myshopify.com"}}


def order(name: str, total: int) -> dict:
    return {"name": name, "currency": "USD", "created_at": AT, "email": EMAIL,
            "total_price_minor": total, "total_received_minor": total,
            "total_refunded_minor": 0, "net_payment_minor": total}


def pp(ref: str, gross: int) -> dict:
    return {"invoice_id": ref, "currency": "USD", "initiated_at": AT, "payer_email": EMAIL,
            "amount_minor": gross, "fee_minor": -300, "net_minor": gross - 300}


def st(ref: str, gross: int) -> dict:
    return {"order_ref": ref, "currency": "usd", "created": EPOCH, "amount": gross,
            "customer": CARD, "amount_refunded": 0, "fee_minor": 300, "net_minor": gross - 300}


ROWS = {
    "shopify": [order("#1002", 99900), order("#1003", 15000)],
    "paypal": [pp("#1002", 99900)],
    "stripe": [st("#1002", 99900), st("#1003", 15000)],
}


@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


@pytest.fixture
def client(store):
    from okwan_api.auth import set_store
    from okwan_api.main import app

    set_store(store)
    return TestClient(app)


async def _session(store, tenant_id: str) -> dict[str, str]:
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(tenant_id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    return {"Authorization": f"Bearer {full}"}


@pytest.fixture
async def tree(store):
    isv = await store.create_tenant("ISV")
    merchant = await store.create_tenant("Kofi's Store", parent_id=isv.id)
    other = await store.create_tenant("Other ISV")
    for connector, fields in VAULT.items():
        for field, value in fields.items():
            await store.put_credential(merchant.id, connector, field, value)
    key, _ = await store.issue_key(merchant.id)
    return {"isv": isv, "merchant": merchant, "other": other,
            "isv_auth": await _session(store, isv.id),
            "other_auth": await _session(store, other.id),
            "merchant_key": {"Authorization": f"Bearer {key}"}}


@pytest.fixture
def rails(monkeypatch):
    async def side(ref, resolver, cap, overrides):
        return ROWS[ref.connector], Coverage(source=ref.qualified, records=1, cap=cap, truncated=False)

    monkeypatch.setattr(across, "fetch_side", side)


@pytest.fixture
def rejected(monkeypatch):
    async def side(ref, resolver, cap, overrides):
        raise UpstreamError(401, '{"error":{"message":"Invalid API Key provided: ' + SECRET + '"}}')

    monkeypatch.setattr(across, "fetch_side", side)


def _run_path(tree) -> str:
    return f"/v1/tenants/{tree['merchant'].id}/reconciliations/across/rails"


async def _usage(store) -> int:
    return sum(store._usage.values())


# ── the dashboard's session route stores what it ran ───────────────

async def test_a_session_run_is_stored_and_read_back(client, store, tree, rails):
    r = client.post(_run_path(tree), headers=tree["isv_auth"])
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    listed = client.get(f"/v1/tenants/{tree['merchant'].id}/runs", headers=tree["isv_auth"]).json()["data"]
    assert [x["id"] for x in listed] == [run_id]
    assert listed[0]["surface"] == "dashboard" and listed[0]["status"] == "ok"
    assert listed[0]["kind"] == "across" and listed[0]["name"] == "rails"
    assert "rows" not in listed[0] and listed[0]["summary"]["collected_twice"] == 1
    assert listed[0]["rows_total"] == 2
    assert listed[0]["twice_currency"] == "USD"
    assert r.json()["twice_currency"] == "USD"

    one = client.get(f"/v1/tenants/{tree['merchant'].id}/runs/{run_id}", headers=tree["isv_auth"]).json()
    assert one["has_more"] is False
    assert [row["order"]["ref"] for row in one["rows"]] == ["#1002", "#1003"]  # worst first
    assert one["rows"][0]["outcome"] == "collected_twice"

    latest = client.get(f"/v1/tenants/{tree['isv'].id}/runs/latest", headers=tree["isv_auth"]).json()["data"]
    assert set(latest) == {tree["merchant"].id}
    assert latest[tree["merchant"].id]["id"] == run_id
    assert len(latest[tree["merchant"].id]["rows"]) == 2


async def test_a_stored_run_never_carries_a_rail_record(client, store, tree, rails):
    """The order carried an email; the Stripe record a card token; PayPal a
    payer email. None of them may be written down, on any row or summary."""
    run_id = client.post(_run_path(tree), headers=tree["isv_auth"]).json()["run_id"]
    rec = await store.get_run(tree["merchant"].id, run_id)
    blob = json.dumps({"rows": rec.rows, "summary": rec.summary})
    assert EMAIL not in blob
    assert CARD not in blob
    assert SECRET not in blob
    assert '"record"' not in blob and "payer_email" not in blob
    # And what the dashboard received was the same trimmed shape.
    assert set(rec.rows[0]["order"]) == {"ref", "currency"}


async def test_a_failed_run_is_stored_with_the_error_scrubbed(client, store, tree, rejected):
    r = client.post(_run_path(tree), headers=tree["isv_auth"])
    assert r.status_code == 401
    listed = client.get(f"/v1/tenants/{tree['merchant'].id}/runs", headers=tree["isv_auth"]).json()["data"]
    assert len(listed) == 1
    assert listed[0]["status"] == "failed" and listed[0]["summary"] is None
    assert "[redacted]" in listed[0]["error"] and SECRET not in listed[0]["error"]
    rec = await store.get_run(tree["merchant"].id, listed[0]["id"])
    assert rec.rows is None
    # A failed run is not metered, as before.
    assert await _usage(store) == 0


async def test_reading_stored_runs_is_free(client, store, tree, rails):
    run_id = client.post(_run_path(tree), headers=tree["isv_auth"]).json()["run_id"]
    before = await _usage(store)
    assert before == 1
    client.get(f"/v1/tenants/{tree['merchant'].id}/runs", headers=tree["isv_auth"])
    client.get(f"/v1/tenants/{tree['merchant'].id}/runs/{run_id}", headers=tree["isv_auth"])
    client.get(f"/v1/tenants/{tree['isv'].id}/runs/latest", headers=tree["isv_auth"])
    assert await _usage(store) == before


@pytest.mark.parametrize("suffix", ["/runs", "/runs/latest", "/runs/run_0123456789abcdef"])
async def test_a_foreign_or_unknown_tenant_is_404(client, tree, suffix):
    foreign = client.get(f"/v1/tenants/{tree['merchant'].id}{suffix}", headers=tree["other_auth"])
    unknown = client.get(f"/v1/tenants/ten_nope{suffix}", headers=tree["isv_auth"])
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["detail"] == f"no such tenant: {tree['merchant'].id}"
    assert unknown.json()["detail"] == "no such tenant: ten_nope"


async def test_a_run_is_not_readable_under_a_sibling_id(client, store, tree, rails):
    """A run id is scoped to its tenant: the right id under the wrong tenant
    reads as nonexistent, like everything outside a subtree."""
    run_id = client.post(_run_path(tree), headers=tree["isv_auth"]).json()["run_id"]
    sibling = await store.create_tenant("Sibling", parent_id=tree["isv"].id)
    r = client.get(f"/v1/tenants/{sibling.id}/runs/{run_id}", headers=tree["isv_auth"])
    assert r.status_code == 404
    assert r.json()["detail"] == f"no such run: {run_id}"


async def test_a_recording_failure_never_fails_the_run(client, store, tree, rails, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("database away")

    monkeypatch.setattr(store, "add_run", broken)
    r = client.post(_run_path(tree), headers=tree["isv_auth"])
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] is None
    assert r.json()["summary"]["collected_twice"] == 1
    assert await _usage(store) == 1


PAYER_A = "payer-a@example.com"
PAYER_B = "payer-b@example.com"


@pytest.fixture
def ambiguous_rails(monkeypatch):
    """One 450.00 order, two 450.00 PayPal payments with no invoice id: the
    engine can't say which paid it and reports the row ambiguous with both
    candidates. Those candidates are rail records."""
    from okwan_recon import runner

    def payment(payer, amount=45000, invoice_id=None, fee=-300):
        return {"invoice_id": invoice_id, "currency": "USD", "initiated_at": AT, "payer_email": payer,
                "amount_minor": amount, "fee_minor": fee, "net_minor": amount + fee}

    # ... and one clean match (#2002, no fee), so the order of the stored rows shows.
    rows = {
        "shopify": [order("#2002", 12000), order("#2001", 45000)],
        "paypal": [payment(EMAIL, 12000, "#2002", fee=0), payment(PAYER_A), payment(PAYER_B)],
    }

    async def side(ref, resolver, cap, overrides):
        return rows[ref.connector], Coverage(source=ref.qualified, records=1, cap=cap, truncated=False)

    monkeypatch.setattr(runner, "fetch_side", side)


async def test_a_stored_pair_run_never_carries_a_candidate_record(client, store, tree, ambiguous_rails):
    path = f"/v1/tenants/{tree['merchant'].id}/reconciliations/shopify_paypal"
    r = client.post(path, headers=tree["isv_auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["ambiguous"] == 1
    amb = [row for row in body["data"] if row["status"] == "ambiguous"]
    assert amb and amb[0]["candidates"] == 2 and amb[0]["left_ref"] == "#2001"
    for blob in (r.text, json.dumps((await store.get_run(tree["merchant"].id, body["run_id"])).rows)):
        assert PAYER_A not in blob and PAYER_B not in blob and EMAIL not in blob
    got = client.get(f"/v1/tenants/{tree['merchant'].id}/runs/{body['run_id']}", headers=tree["isv_auth"]).json()
    assert PAYER_A not in json.dumps(got)
    # Findings first, the clean match last: a cut at the row limit would
    # drop the match, never the ambiguity.
    statuses = [row["status"] for row in got["rows"]]
    assert statuses[-1] == "matched" and "matched" not in statuses[:-1] and "ambiguous" in statuses


async def test_a_rail_that_cannot_be_reached_is_a_stored_failed_run(client, store, tree, monkeypatch):
    import httpx

    async def gone(ref, resolver, cap, overrides):
        raise httpx.ConnectTimeout("stripe.example took too long")

    monkeypatch.setattr(across, "fetch_side", gone)
    r = client.post(_run_path(tree), headers=tree["isv_auth"])
    assert r.status_code == 502, r.text
    listed = client.get(f"/v1/tenants/{tree['merchant'].id}/runs", headers=tree["isv_auth"]).json()["data"]
    assert listed and listed[0]["status"] == "failed" and "ConnectTimeout" in listed[0]["error"]


# ── every surface records ───────────────────────────────────────────

async def test_the_rest_key_route_records_with_surface_rest(client, store, tree, rails):
    r = client.get("/v1/reconciliations/across/rails", headers=tree["merchant_key"])
    assert r.status_code == 200, r.text
    # The key holder still gets the full rows; what is stored is trimmed.
    assert "name" in r.json()["data"][0]["order"]
    listed = await store.list_runs(tree["merchant"].id)
    assert [x.surface for x in listed] == ["rest"] and listed[0].status == "ok"
    rec = await store.get_run(tree["merchant"].id, listed[0].id)
    assert EMAIL not in json.dumps(rec.rows)


async def test_the_rest_key_route_records_a_failure_scrubbed(client, store, tree, rejected):
    r = client.get("/v1/reconciliations/across/rails", headers=tree["merchant_key"])
    assert r.status_code == 401
    listed = await store.list_runs(tree["merchant"].id)
    assert listed[0].surface == "rest" and listed[0].status == "failed"
    assert SECRET not in listed[0].error and "[redacted]" in listed[0].error


async def test_the_hosted_mcp_tool_records_with_surface_mcp(client, store, tree, rails):
    from okwan_query.mcp_http import _reconcile_tool

    tool = _reconcile_tool()
    ctx = SimpleNamespace(headers={"authorization": tree["merchant_key"]["Authorization"]})
    out = await tool(ctx=ctx, name="rails")
    assert "error" not in out and out["summary"]["collected_twice"] == 1
    listed = await store.list_runs(tree["merchant"].id)
    assert [x.surface for x in listed] == ["mcp"] and listed[0].status == "ok"


async def test_the_hosted_mcp_tool_records_a_failure_and_scrubs_what_the_agent_sees(client, store, tree, rejected):
    from okwan_query.mcp_http import _reconcile_tool

    tool = _reconcile_tool()
    ctx = SimpleNamespace(headers={"authorization": tree["merchant_key"]["Authorization"]})
    out = await tool(ctx=ctx, name="rails")
    assert "error" in out and SECRET not in out["error"] and "[redacted]" in out["error"]
    listed = await store.list_runs(tree["merchant"].id)
    assert listed[0].surface == "mcp" and listed[0].status == "failed"
    assert SECRET not in listed[0].error
