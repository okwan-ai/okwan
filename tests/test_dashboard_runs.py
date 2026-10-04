"""Reconciliation runs from the dashboard, as a merchant in the caller's subtree.

POST /v1/tenants/{id}/reconciliations[/across]/{name} take a session, so
an ISV sees a merchant's finding without issuing that merchant a key.
What would fail silently: running with the ISV's own vault instead of
the merchant's, metering the wrong tenant, a foreign id that answers
anything but 404, a plan gate the dashboard path skips, and a session
that slips onto the key-only data routes.

Only transport is fake: rows go through the real declarations, match
engine and fold.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from okwan_core import UpstreamError
from okwan_recon import across
from okwan_recon.coverage import Coverage
from okwan_recon.engine import ReconResult
from okwan_vault import EnvMasterKey, MemoryStore, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token

AT = "2026-08-27T15:00:00Z"
EPOCH = int(datetime(2026, 8, 27, 15, 1, tzinfo=UTC).timestamp())

MERCHANT_VAULT = {"stripe": {"secret_key": "sk_merchant"},
                  "paypal": {"client_id": "pp_merchant", "client_secret": "pp_merchant_s"},
                  "shopify": {"access_token": "shpat_merchant",
                              "shop_domain": "kofi.myshopify.com"}}
ISV_VAULT = {"stripe": {"secret_key": "sk_isv_own"}}


def order(name: str, total: int) -> dict:
    return {"name": name, "currency": "USD", "created_at": AT,
            "total_price_minor": total, "total_received_minor": total,
            "total_refunded_minor": 0, "net_payment_minor": total}


def pp(ref: str, gross: int) -> dict:
    return {"invoice_id": ref, "currency": "USD", "initiated_at": AT,
            "amount_minor": gross, "fee_minor": -300, "net_minor": gross - 300}


def st(ref: str, gross: int) -> dict:
    return {"order_ref": ref, "currency": "usd", "created": EPOCH, "amount": gross,
            "amount_refunded": 0, "fee_minor": 300, "net_minor": gross - 300}


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
    """An ISV with its own Stripe key, a merchant holding three rails, and
    a second, unrelated ISV."""
    isv = await store.create_tenant("ISV")
    merchant = await store.create_tenant("Kofi's Store", parent_id=isv.id)
    other = await store.create_tenant("Other ISV")
    for tenant, vault in ((isv, ISV_VAULT), (merchant, MERCHANT_VAULT)):
        for connector, fields in vault.items():
            for field, value in fields.items():
                await store.put_credential(tenant.id, connector, field, value)
    return {"isv": isv, "merchant": merchant, "other": other,
            "isv_auth": await _session(store, isv.id),
            "other_auth": await _session(store, other.id)}


@pytest.fixture
def rails(monkeypatch):
    """Fake transport. Records the credentials each side was resolved with."""
    seen: dict[str, dict[str, str]] = {}

    async def side(ref, resolver, cap, overrides):
        from okwan_core import get

        fields = get(ref.connector).auth.required_fields
        seen[ref.connector] = dict(resolver(ref.connector, fields))
        return ROWS[ref.connector], Coverage(source=ref.qualified, records=1, cap=cap, truncated=False)

    monkeypatch.setattr(across, "fetch_side", side)
    return seen


def _across(tenant_id: str, **params) -> tuple[str, dict]:
    return f"/v1/tenants/{tenant_id}/reconciliations/across/rails", params


async def _usage(store, tenant) -> dict[str, int]:
    out: dict[str, int] = {}
    for (tid, _, surface), n in store._usage.items():
        if tid == tenant.id:
            out[surface] = out.get(surface, 0) + n
    return out


# ── happy path ──────────────────────────────────────────────────────

async def test_runs_as_the_merchant_and_finds_the_double_collection(client, tree, rails):
    path, _ = _across(tree["merchant"].id)
    r = client.post(path, params={"limit": 1000}, headers=tree["isv_auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["orders"] == 2
    assert body["summary"]["collected_twice"] == 1
    assert body["summary"]["collected_twice_minor"] == 99900
    # The dashboard gets trimmed rows: a reference and a currency, never the
    # ledger or rail record itself.
    by_order = {row["order"]["ref"]: row["outcome"] for row in body["data"]}
    assert by_order == {"#1002": "collected_twice", "#1003": "collected"}
    assert set(body["data"][0]["order"]) == {"ref", "currency"}
    assert all("record" not in rail for row in body["data"] for rail in row["rails"])
    assert body["run_id"].startswith("run_")
    # The merchant's vault, not the ISV's own Stripe key.
    assert rails["stripe"]["secret_key"] == "sk_merchant"
    assert rails["paypal"]["client_id"] == "pp_merchant"


async def test_outcome_filter_matches_the_key_route(client, tree, rails):
    path, _ = _across(tree["merchant"].id)
    r = client.post(path, params={"outcome": "collected_twice"}, headers=tree["isv_auth"])
    assert [row["order"]["ref"] for row in r.json()["data"]] == ["#1002"]
    bad = client.post(path, params={"outcome": "paid"}, headers=tree["isv_auth"])
    assert bad.status_code == 422


async def test_the_two_sided_run(client, tree, monkeypatch):
    from okwan_api import admin

    resolved: list[str] = []

    async def ok(spec, resolver):
        resolved.append(resolver("stripe", ("secret_key",))["secret_key"])
        return ReconResult(name=spec.name)

    monkeypatch.setattr(admin, "run", ok)
    r = client.post(f"/v1/tenants/{tree['merchant'].id}/reconciliations/shopify_stripe",
                    headers=tree["isv_auth"])
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["reconciliation"] == "shopify_stripe"
    assert resolved == ["sk_merchant"]


async def test_unknown_declaration_is_404(client, tree, rails):
    mid = tree["merchant"].id
    auth = tree["isv_auth"]
    assert client.post(f"/v1/tenants/{mid}/reconciliations/across/nope",
                       headers=auth).status_code == 404
    assert client.post(f"/v1/tenants/{mid}/reconciliations/nope",
                       headers=auth).status_code == 404


# ── the subtree guard ───────────────────────────────────────────────

@pytest.mark.parametrize("route", ["across/rails", "shopify_stripe"])
async def test_a_foreign_tenant_is_404_and_nothing_is_read(client, tree, rails, route):
    r = client.post(f"/v1/tenants/{tree['merchant'].id}/reconciliations/{route}",
                    headers=tree["other_auth"])
    assert r.status_code == 404
    assert r.json()["detail"] == f"no such tenant: {tree['merchant'].id}"
    assert rails == {}


async def test_an_unknown_tenant_reads_like_a_foreign_one(client, tree, rails):
    r = client.post("/v1/tenants/ten_nope/reconciliations/across/rails",
                    headers=tree["isv_auth"])
    assert r.status_code == 404
    assert r.json()["detail"] == "no such tenant: ten_nope"


async def test_a_merchant_cannot_run_its_parent(client, store, tree, rails):
    auth = await _session(store, tree["merchant"].id)
    r = client.post(f"/v1/tenants/{tree['isv'].id}/reconciliations/across/rails",
                    headers=auth)
    assert r.status_code == 404


# ── quota ───────────────────────────────────────────────────────────

@pytest.fixture
async def exhausted(store, tree):
    """The ISV's allowance spent. A merchant shares it: usage rolls up."""
    from okwan_vault.usage import PLANS

    for _ in range(PLANS["free"]):
        await store.record_request(tree["isv"].id, "rest:query")


async def test_quota_exhaustion_is_402(client, tree, exhausted, rails):
    path, _ = _across(tree["merchant"].id)
    r = client.post(path, headers=tree["isv_auth"])
    assert r.status_code == 402
    assert "monthly request limit" in r.json()["detail"]
    assert rails == {}


async def test_a_foreign_id_learns_nothing_about_quota(client, tree, exhausted, rails):
    path, _ = _across(tree["merchant"].id)
    assert client.post(path, headers=tree["other_auth"]).status_code == 404


# ── metering ────────────────────────────────────────────────────────

async def test_metered_on_the_merchant(client, store, tree, rails, monkeypatch):
    from okwan_api import admin

    async def ok(spec, resolver):
        return ReconResult(name=spec.name)

    monkeypatch.setattr(admin, "run", ok)
    mid = tree["merchant"].id
    client.post(f"/v1/tenants/{mid}/reconciliations/across/rails", headers=tree["isv_auth"])
    client.post(f"/v1/tenants/{mid}/reconciliations/shopify_stripe", headers=tree["isv_auth"])
    assert await _usage(store, tree["merchant"]) == {
        "dashboard:across": 1, "dashboard:reconcile": 1,
    }
    assert await _usage(store, tree["isv"]) == {}


async def test_an_upstream_failure_maps_like_rest_and_is_not_metered(
    client, store, tree, monkeypatch
):
    async def down(ref, resolver, cap, overrides):
        raise UpstreamError(503, "paypal is down")

    monkeypatch.setattr(across, "fetch_side", down)
    path, _ = _across(tree["merchant"].id)
    r = client.post(path, headers=tree["isv_auth"])
    assert r.status_code == 503
    assert r.json()["detail"] == "paypal is down"
    assert await _usage(store, tree["merchant"]) == {}


async def test_an_upstream_error_never_carries_a_stored_value(client, store, tree, monkeypatch):
    """Some rails quote the key they rejected. The connection test already
    redacts it; a run shown to an ISV operator must too, since the value
    is a merchant's and was never meant to be seen again."""
    secret = MERCHANT_VAULT["stripe"]["secret_key"]

    async def rejected(ref, resolver, cap, overrides):
        raise UpstreamError(401, '{"error":{"message":"Invalid API Key provided: ' + secret + '"}}')

    monkeypatch.setattr(across, "fetch_side", rejected)
    path, _ = _across(tree["merchant"].id)
    r = client.post(path, headers=tree["isv_auth"])
    assert r.status_code == 401
    detail = r.json()["detail"]
    assert secret not in detail
    assert "[redacted]" in detail
    assert await _usage(store, tree["merchant"]) == {}


# ── data routes stay key-only ───────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/v1/reconciliations/across/rails",
    "/v1/reconciliations/shopify_stripe",
])
async def test_a_session_is_refused_on_the_data_routes(client, tree, rails, path):
    r = client.get(path, headers=tree["isv_auth"])
    assert r.status_code == 401
    assert rails == {}
