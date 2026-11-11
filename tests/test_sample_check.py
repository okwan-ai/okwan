"""The sample check: a first result in seconds, from the real engine.

A new account has nothing to look at until a merchant's Shopify, PayPal and
Stripe are all connected. The sample is the same fold over a made-up
store's week, so the first result takes one click. What must stay true:
the figures are the engine's own (only the read is replaced), nothing of
any tenant is read, stored or metered, and the response is the one the
real route gives, so an integrator can build against it.
"""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import okwan_api.main  # noqa: F401  (registers the connectors and declarations)
import pytest
from okwan_recon import across, sample
from okwan_recon.registry import get_across
from okwan_vault import EnvMasterKey, MemoryStore, new_key

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
PATH = "/v1/sample/reconciliations/across/rails"


@pytest.fixture
async def api(monkeypatch):
    from okwan_api import sample as sample_api
    from okwan_api.auth import set_store
    from okwan_api.main import app

    monkeypatch.setattr(sample_api, "_held", None)
    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        yield c, store


# ── the week, through the real fold ────────────────────────────────────

async def test_the_sample_week_is_judged_by_the_real_fold():
    result = await sample.run(NOW)
    s = result.summary
    assert (s["orders"], s["collected"], s["collected_twice"], s["split_tender"], s["uncollected"]) == (8, 4, 2, 1, 1)
    assert (s["collected_twice_minor"], s["overcollected_minor"]) == (50199, 50199), "312.00 and 189.99 taken twice"
    rows = {r["order"]["ref"]: r for r in result.trimmed_rows(get_across("rails").ledger_currency)}
    assert {ref: r["outcome"] for ref, r in rows.items()} == {
        "#2041": "collected", "#2042": "collected", "#2043": "collected_twice", "#2044": "collected",
        "#2045": "split_tender", "#2046": "uncollected", "#2047": "collected_twice", "#2048": "collected"}
    twice = rows["#2043"]
    assert (twice["order_total_minor"], twice["collected_minor"], twice["collected_on"]) == (31200, 62400, ["paypal", "stripe"])
    assert (rows["#2045"]["collected_minor"], rows["#2046"]["collected_minor"]) == (24000, None)


async def test_only_the_read_is_replaced(monkeypatch):
    """The sample never reaches a connector, and what judges it is the
    same `match` and `fold` a real merchant's check runs."""
    async def no_rail(*args, **kwargs):
        raise AssertionError("the sample read a connector")

    monkeypatch.setattr(across, "fetch_side", no_rail)
    judged: list[str] = []
    real_fold = across.fold
    monkeypatch.setattr(across, "fold", lambda spec, *a, **k: judged.append(spec.name) or real_fold(spec, *a, **k))
    await sample.run(NOW)
    assert judged == ["rails"]


def test_the_week_is_always_the_last_one():
    for now in (NOW, datetime(2027, 3, 2, 8, 30, tzinfo=UTC)):
        orders = sample.rows(now)["shopify"]
        days_ago = [(now - datetime.fromisoformat(o["created_at"])).days for o in orders]
        assert min(days_ago) >= 1 and max(days_ago) <= 7
    assert {r["invoice_id"] for r in sample.rows(NOW)["paypal"]} <= {o["name"] for o in sample.rows(NOW)["shopify"]}


# ── the route ──────────────────────────────────────────────────────────

async def test_the_sample_needs_no_key_and_says_it_is_a_sample(api):
    client, _ = api
    r = await client.get(PATH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["sample"], body["store"]) == (True, "Sample store")
    assert body["summary"]["collected_twice"] == 2 and body["total_rows"] == 8


async def test_it_answers_as_the_real_route_does(api):
    """Same page keys and row keys as GET /v1/reconciliations/across/rails,
    so the only change for an integrator is dropping `/sample`."""
    from okwan_recon.emitters.mcp import paged
    from okwan_recon.emitters.rest import rest_page

    client, _ = api
    result = await sample.run(NOW)
    real = rest_page(paged(result.summary, result.rows(), "outcome", "all", 100, None))
    got = (await client.get(PATH)).json()
    assert set(got) - {"sample", "store"} == set(real)
    assert set(got["data"][0]) == set(real["data"][0])
    assert set(got["summary"]) == set(real["summary"])


async def test_it_filters_and_pages_like_the_real_route(api):
    client, _ = api
    twice = (await client.get(PATH, params={"outcome": "collected_twice"})).json()
    assert [r["order"]["name"] for r in twice["data"]] == ["#2043", "#2047"]
    first = (await client.get(PATH, params={"limit": 3})).json()
    assert len(first["data"]) == 3 and first["has_more"] and first["next_cursor"]
    rest = (await client.get(PATH, params={"limit": 1000, "cursor": first["next_cursor"]})).json()
    assert len(rest["data"]) == 5 and not rest["has_more"]
    assert (await client.get(PATH, params={"outcome": "nonsense"})).status_code == 422


async def test_a_cursor_it_did_not_issue_is_refused_not_served(api):
    client, _ = api
    for cursor in ("not-a-cursor", "eyJvIjogOTk5OSwgImYiOiAieCIsICJ2IjogImFsbCJ9", "A" * 5000):
        r = await client.get(PATH, params={"cursor": cursor})
        assert r.status_code == 409, f"{cursor[:20]}: {r.status_code}"


async def test_trimmed_rows_are_what_the_dashboard_draws(api):
    client, _ = api
    body = (await client.get(PATH, params={"trim": "true", "limit": 1000})).json()
    assert body["twice_currency"] == "USD"
    row = body["data"][0]
    assert {"outcome", "order", "order_total_minor", "collected_minor", "collected_on", "unverified_rails",
            "reason", "rails"} <= set(row)
    assert row["order"] == {"ref": "#2043", "currency": "USD"}, "no raw order record in a trimmed row"


async def test_only_the_rails_fold_has_a_sample(api):
    client, _ = api
    r = await client.get("/v1/sample/reconciliations/across/shopify_stripe")
    assert r.status_code == 404 and "rails" in r.text


async def test_nothing_is_stored_metered_or_logged(api):
    client, store = api
    shop = await store.create_tenant("Corner Café")
    key, _ = await store.issue_key(shop.id, scope="read")
    for headers in ({}, {"Authorization": f"Bearer {key}"}):
        assert (await client.get(PATH, headers=headers)).status_code == 200
    assert await store.list_runs(shop.id) == []
    assert await store.usage_since(shop.id, datetime(2000, 1, 1, tzinfo=UTC)) == 0
    assert await store.access_entries(shop.id, since=datetime(2000, 1, 1, tzinfo=UTC)) == []


async def test_one_run_an_hour_however_many_ask(api, monkeypatch):
    client, _ = api
    runs = 0
    real = sample.run

    async def counted(now=None):
        nonlocal runs
        runs += 1
        return await real(now)

    monkeypatch.setattr(sample, "run", counted)
    for _ in range(5):
        assert (await client.get(PATH)).status_code == 200
    assert runs == 1


async def test_one_address_cannot_hammer_it(api):
    from okwan_api.ratelimit import SAMPLE_IP

    client, _ = api
    codes = [(await client.get(PATH)).status_code for _ in range(SAMPLE_IP.limit + 1)]
    assert codes[:SAMPLE_IP.limit] == [200] * SAMPLE_IP.limit and codes[-1] == 429
