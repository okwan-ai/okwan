"""The attacker's pass on the list of open items, each finding turned around.

A fresh agent with an attacker's brief broke the first version of the list
eleven ways (docs/audit/red-team.md, 2026-11-21). Every exploit here is one
of its own, set up the same way, now asserting the attack fails. Two
findings are answered rather than fixed and are tested as answered: a
finding's age restarts when a file its check read is removed (the privacy
policy promises the results go with the file), and a finding older than its
check's lookback leaves the list (G23).
"""
from __future__ import annotations

import asyncio
import base64
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_files_connector import BANK_MAPPING, BATCH_MAPPING, BATCHES, STATEMENT

AT = "2026-08-27T15:00:00Z"
OCT_1 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
NOV_1 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def order(name: str, total: int, at: str = AT) -> dict:
    return {"name": name, "currency": "USD", "created_at": at, "total_price_minor": total,
            "total_received_minor": total, "total_refunded_minor": 0, "net_payment_minor": total}


def charge(ref, gross: int, fee: int = 300) -> dict:
    epoch = int(datetime(2026, 8, 27, 15, 1, tzinfo=UTC).timestamp())
    return {"order_ref": ref, "currency": "usd", "created": epoch, "amount": gross,
            "amount_refunded": 0, "fee_minor": fee, "net_minor": gross - fee}


def paypal(ref, gross: int, fee: int = -300) -> dict:
    return {"invoice_id": ref, "currency": "USD", "initiated_at": AT,
            "amount_minor": gross, "fee_minor": fee, "net_minor": gross + fee}


class Rails:
    """What each rail would return, through the real declarations and engine
    (as tests/test_across_rails.py); `down` answers as an outage would, and
    `slow` holds a read after it has taken its rows."""

    def __init__(self, monkeypatch):
        from okwan_recon import across, runner

        self.rows: dict[str, list[dict]] = {"shopify": [], "stripe": [], "paypal": []}
        self.down = False
        self.slow: dict = {}
        monkeypatch.setattr(runner, "fetch_side", self.fetch_side)
        monkeypatch.setattr(across, "fetch_side", self.fetch_side)

    async def fetch_side(self, ref, resolver=None, max_records=500, overrides=None):
        from okwan_core import UpstreamError
        from okwan_recon.coverage import Coverage

        if self.down:
            raise UpstreamError(503, "service unavailable")
        rows = [dict(r) for r in self.rows[ref.connector]]
        gate = self.slow.pop(ref.connector, None)
        if gate is not None:
            await gate.wait()
        return rows, Coverage(source=f"{ref.connector}.{ref.resource}.{ref.operation}",
                              records=len(rows), cap=max_records, truncated=False)


class Clock:
    """The run clock and the list's clock, moved together."""

    def __init__(self, monkeypatch, at: datetime):
        from okwan_api import exceptions as route
        from okwan_api import runs

        self.at = at
        clock = self

        class Fixed(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.at if tz is None else clock.at.astimezone(tz)

        monkeypatch.setattr(runs, "datetime", Fixed)
        monkeypatch.setattr(route, "now", lambda: clock.at)

    def set(self, at: datetime) -> None:
        self.at = at


@asynccontextmanager
async def world():
    from okwan_api.auth import set_store
    from okwan_api.main import app
    from okwan_api.ratelimit import limiter
    from okwan_api.recon_reuse import cache
    from okwan_vault import EnvMasterKey, MemoryStore, new_key

    limiter.reset()
    cache.clear()
    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    root = await store.create_tenant("Dealer group")
    shop = await store.create_tenant("Store 12", parent_id=root.id)
    shop_key, _ = await store.issue_key(shop.id, scope="admin")
    root_key, _ = await store.issue_key(root.id, scope="admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        c.headers["Authorization"] = f"Bearer {shop_key}"
        yield {"client": c, "store": store, "root": root, "shop": shop,
               "as_root": {"Authorization": f"Bearer {root_key}"}}
    limiter.reset()
    cache.clear()


async def _upload(c, tenant_id, data, mapping, name="f.csv"):
    body = {"filename": name, "content_base64": base64.b64encode(data).decode(),
            "mapping": mapping.model_dump(mode="json")}
    r = await c.post(f"/v1/tenants/{tenant_id}/imports", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(autouse=True)
def _read_in_september_2026(as_of):
    as_of(2026, 9, 20)


# ── F1: findings past the stored cut are counted and said to be cut ──

async def test_findings_past_the_stored_cut_are_counted_and_the_check_says_so(monkeypatch):
    rails = Rails(monkeypatch)
    for i in range(700):
        rails.rows["shopify"].append(order(f"#A{i:04d}", 10_000))
        rails.rows["stripe"].append(charge(f"#A{i:04d}", 9_000, fee=0))
    for i in range(400):
        rails.rows["shopify"].append(order(f"#B{i:04d}", 25_000))
    async with world() as w:
        c, shop, root = w["client"], w["shop"], w["root"]
        assert (await c.post(f"/v1/tenants/{shop.id}/reconciliations/shopify_stripe")).status_code == 200
        body = (await c.get(f"/v1/tenants/{root.id}/exceptions", headers=w["as_root"])).json()
        (check,) = [k for k in body["checks"] if k["check"] == "shopify_stripe"]
        assert (check["open"], check["cut"]) == (1100, True)
        assert body["total"] == 1100


# ── F2: the list is bounded and never holds up the API ──────────────

async def test_the_accounts_list_reads_no_stored_rows_and_the_api_answers_meanwhile(monkeypatch):
    """150 merchants, each with a stored run of 1,000 findings: the list of
    the account reads each run's summary, never its rows, gives each merchant
    at most its oldest few items, and another request is answered while it
    is built."""
    from okwan_api import exceptions as route
    from okwan_api.runs import _keep
    async with world() as w:
        c, store, shop, root = w["client"], w["store"], w["shop"], w["root"]
        rows = [{"status": "unmatched_left", "left_ref": f"B-{i}", "right_ref": None,
                 "evidence": {"record": {"amount_minor": 100 + i, "currency": "USD", "when": "2026-09-01"}}}
                for i in range(1000)]
        started = datetime(2026, 9, 10, tzinfo=UTC)
        merchants = [shop] + [await store.create_tenant(f"Store {n}", parent_id=root.id) for n in range(149)]
        for m in merchants:
            kept, summary = _keep(m.id, "batches_deposits", "pair", rows, None, started, {"unmatched_left": 1000})
            await store.add_run(m.id, kind="pair", name="batches_deposits", surface="rest", status="ok",
                                started_at=started, finished_at=started, summary=summary, rows=kept, error=None)
        read_rows = []
        real_get_run = store.get_run

        async def counting_get_run(tid, run_id):
            read_rows.append(run_id)
            return await real_get_run(tid, run_id)

        monkeypatch.setattr(store, "get_run", counting_get_run)

        async def other():
            await asyncio.sleep(0.02)
            t0 = time.perf_counter()
            r = await c.get("/v1/tenants", headers=w["as_root"])
            return r.status_code, time.perf_counter() - t0

        listing, (status, waited) = await asyncio.gather(
            c.get(f"/v1/tenants/{root.id}/exceptions?limit=1000", headers=w["as_root"]), other())
        assert listing.status_code == 200 and status == 200
        # A page of merchants at a time: walk them all.
        pages, body, total, per = [listing.json()], listing.json(), 0, {}
        while body["next"]:
            body = (await c.get(f"/v1/tenants/{root.id}/exceptions?limit=1000&after={body['next']}",
                                headers=w["as_root"])).json()
            pages.append(body)
        for page in pages:
            total += page["total"]
            assert len({i["tenant_id"] for i in page["data"]}) <= route.MERCHANTS_PER_PAGE
            for i in page["data"]:
                per[i["tenant_id"]] = per.get(i["tenant_id"], 0) + 1
        assert read_rows == []
        assert total == 150 * 1000 and len(per) == 150 and len(pages) == 4  # the account and 150 merchants
        assert max(per.values()) <= route.PER_MERCHANT
        assert waited < 1.0, f"another request waited {waited:.2f}s"


async def test_the_list_has_a_limit_per_account(monkeypatch):
    from okwan_api import exceptions as route
    from okwan_api.ratelimit import Rule
    monkeypatch.setattr(route, "EXCEPTIONS_TENANT", Rule("exceptions:tenant:test", 2, 60))
    async with world() as w:
        c, root = w["client"], w["root"]
        codes = [(await c.get(f"/v1/tenants/{root.id}/exceptions", headers=w["as_root"])).status_code
                 for _ in range(3)]
        assert codes == [200, 200, 429]


# ── F3: a fold finding is dated by its order's day ──────────────────

async def test_an_order_collected_twice_is_aged_from_the_day_it_was_placed(monkeypatch):
    clock = Clock(monkeypatch, datetime(2026, 9, 10, 9, tzinfo=UTC))
    rails = Rails(monkeypatch)
    rails.rows["shopify"] = [order("#1002", 99_900)]
    rails.rows["paypal"] = [paypal("#1002", 99_900)]
    rails.rows["stripe"] = [charge("#1002", 99_900)]
    async with world() as w:
        c, shop, root = w["client"], w["shop"], w["root"]
        assert (await c.post(f"/v1/tenants/{shop.id}/reconciliations/across/rails")).status_code == 200
        clock.set(datetime(2026, 9, 20, 9, tzinfo=UTC))
        (item,) = (await c.get(f"/v1/tenants/{root.id}/exceptions", headers=w["as_root"])).json()["data"]
        assert (item["finding"], item["dated_on"], item["open_days"]) == ("collected_twice", "2026-08-27", 24)


# ── F5: failed runs neither empty the list nor restart an age ───────

async def test_failed_runs_in_an_outage_keep_the_last_good_findings_and_their_age(monkeypatch):
    clock = Clock(monkeypatch, OCT_1)
    rails = Rails(monkeypatch)
    rails.rows["shopify"] = [order("#1002", 99_900)]
    rails.rows["paypal"] = [paypal("#1002", 99_900)]
    rails.rows["stripe"] = [charge("#1002", 99_900)]
    async with world() as w:
        c, store, shop, root = w["client"], w["store"], w["shop"], w["root"]
        read_key, _ = await store.issue_key(shop.id, scope="read")
        as_reader = {"Authorization": f"Bearer {read_key}"}
        assert (await c.post(f"/v1/tenants/{shop.id}/reconciliations/across/rails")).status_code == 200
        rails.down = True
        for n in range(12):
            clock.set(NOV_1 + timedelta(minutes=n))
            assert (await c.get("/v1/reconciliations/across/rails", headers=as_reader)).status_code == 503
        during = (await c.get(f"/v1/tenants/{root.id}/exceptions", headers=w["as_root"])).json()
        (check,) = during["checks"]
        assert check["last_run"]["status"] == "failed" and check["findings_from"] is not None
        (item,) = during["data"]
        assert item["first_seen"] == OCT_1.isoformat()
        rails.down = False
        clock.set(NOV_1 + timedelta(minutes=30))
        assert (await c.get("/v1/reconciliations/across/rails", headers=as_reader)).status_code == 200
        (again,) = (await c.get(f"/v1/tenants/{root.id}/exceptions", headers=w["as_root"])).json()["data"]
        assert (again["key"], again["first_seen"]) == (item["key"], OCT_1.isoformat())


# ── F6: a key belongs to its tenant ──────────────────────────────────

def test_the_same_finding_in_two_accounts_has_two_keys():
    from okwan_recon import exceptions as exc
    row = {"status": "unmatched_left", "left_ref": None, "right_ref": None,
           "evidence": {"record": {"amount_minor": 123_456, "currency": "USD", "when": "2026-10-05"}}}
    (a, _), = exc.keyed("ten_a", "delivery_payouts_deposits", "pair", [row])
    (b, _), = exc.keyed("ten_b", "delivery_payouts_deposits", "pair", [row])
    assert a != b


# ── F7: an expired file's results go within minutes ─────────────────

def test_expired_files_are_swept_every_ten_minutes_at_most():
    from okwan_api import imports
    assert imports.SWEEP_SECONDS <= 600


# ── F8: a reused reference on another day is another finding ─────────

def test_a_batch_number_reused_on_another_day_is_a_new_finding():
    from okwan_recon import exceptions as exc

    def unpaid(day: str, amount: int) -> dict:
        return {"status": "unmatched_left", "left_ref": "001", "right_ref": None,
                "evidence": {"record": {"amount_minor": amount, "currency": "USD", "when": day}}}

    t1, t2 = datetime(2026, 9, 12, tzinfo=UTC), datetime(2026, 9, 20, tzinfo=UTC)
    first = exc.carry("ten_a", "batches_deposits", "pair", [unpaid("2026-09-01", 50_000)], None, t1)
    second = exc.carry("ten_a", "batches_deposits", "pair", [unpaid("2026-09-17", 4_000)], first, t2)
    assert second[0]["key"] != first[0]["key"] and second[0]["first_seen"] == t2.isoformat()
    # The same batch with its amount corrected is the same finding.
    third = exc.carry("ten_a", "batches_deposits", "pair", [unpaid("2026-09-01", 51_000)], first, t2)
    assert (third[0]["key"], third[0]["first_seen"]) == (first[0]["key"], t1.isoformat())


# ── F9: one merchant cannot push its siblings off the list ───────────

async def test_one_merchant_cannot_push_its_siblings_off_the_accounts_list():
    from okwan_api import exceptions as route
    async with world() as w:
        c, store, shop, root = w["client"], w["store"], w["shop"], w["root"]
        await _upload(c, shop.id, BATCHES, BATCH_MAPPING)
        await _upload(c, shop.id, STATEMENT, BANK_MAPPING, "statement.csv")
        assert (await c.post(f"/v1/tenants/{shop.id}/reconciliations/batches_deposits")).status_code == 200
        loud = await store.create_tenant("Store 13", parent_id=root.id)
        key, _ = await store.issue_key(loud.id, scope="admin")
        as_loud = {"Authorization": f"Bearer {key}"}
        many = b"Batch ID,Close Date,Gross,Fees,MID\n" + b"".join(
            f"L-{i},07/{1 + i % 28:02d}/2026,10.00,0.29,MID-9\n".encode() for i in range(600))
        r = await c.post(f"/v1/tenants/{loud.id}/imports", headers=as_loud, json={
            "filename": "many.csv", "content_base64": base64.b64encode(many).decode(),
            "mapping": BATCH_MAPPING.model_dump(mode="json")})
        assert r.status_code == 201, r.text
        await c.post(f"/v1/tenants/{loud.id}/imports", headers=as_loud, json={
            "filename": "statement.csv", "content_base64": base64.b64encode(STATEMENT).decode(),
            "mapping": BANK_MAPPING.model_dump(mode="json")})
        assert (await c.post(f"/v1/tenants/{loud.id}/reconciliations/batches_deposits",
                             headers=as_loud)).status_code == 200

        body = (await c.get(f"/v1/tenants/{root.id}/exceptions?limit=60", headers=w["as_root"])).json()
        listed = {i["tenant_id"] for i in body["data"]}
        assert shop.id in listed and loud.id in listed
        assert sum(1 for i in body["data"] if i["tenant_id"] == loud.id) <= route.PER_MERCHANT
        # One merchant's whole list, on its own.
        mine = (await c.get(f"/v1/tenants/{root.id}/exceptions?merchant={loud.id}&limit=1000",
                            headers=w["as_root"])).json()
        assert {i["tenant_id"] for i in mine["data"]} == {loud.id} and len(mine["data"]) > route.PER_MERCHANT
        # Only the target or a direct child may be named.
        other = await store.create_tenant("Someone else")
        assert (await c.get(f"/v1/tenants/{root.id}/exceptions?merchant={other.id}",
                            headers=w["as_root"])).status_code == 404
        assert (await c.get(f"/v1/tenants/{shop.id}/exceptions?merchant={loud.id}")).status_code == 404


# ── F10: a slow run that read earlier does not replace a newer one ───

async def test_a_slow_run_that_read_before_a_newer_one_does_not_take_its_place(monkeypatch):
    t = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    clock = Clock(monkeypatch, t)
    rails = Rails(monkeypatch)
    rails.rows["shopify"] = [order("#Y", 5_000)]
    async with world() as w:
        c, store, shop, root = w["client"], w["store"], w["shop"], w["root"]
        read_key, _ = await store.issue_key(shop.id, scope="read")

        async def listed():
            body = (await c.get(f"/v1/tenants/{root.id}/exceptions", headers=w["as_root"])).json()
            return {i["left_ref"]: i for i in body["data"] if i["check"] == "shopify_stripe"}

        gate = asyncio.Event()
        rails.slow["stripe"] = gate
        slow = asyncio.create_task(c.get("/v1/reconciliations/shopify_stripe",
                                         headers={"Authorization": f"Bearer {read_key}"}))
        await asyncio.sleep(0.05)
        clock.set(t + timedelta(minutes=2))
        rails.rows["stripe"] = [charge("#Y", 5_000, fee=0)]
        rails.rows["shopify"] = [order("#Y", 5_000), order("#X", 7_000)]
        assert (await c.post(f"/v1/tenants/{shop.id}/reconciliations/shopify_stripe")).status_code == 200
        truth = await listed()
        assert set(truth) == {"#X"}
        clock.set(t + timedelta(minutes=3))
        gate.set()
        assert (await slow).status_code == 200
        assert set(await listed()) == {"#X"}
        clock.set(t + timedelta(hours=1))
        assert (await c.post(f"/v1/tenants/{shop.id}/reconciliations/shopify_stripe")).status_code == 200
        later = await listed()
        assert (later["#X"]["key"], later["#X"]["first_seen"]) == (truth["#X"]["key"], truth["#X"]["first_seen"])


# ── The second pass (2026-11-21), on the rework ─────────────────────

async def test_one_page_of_the_accounts_list_reads_a_bounded_number_of_merchants_in_two_statements(monkeypatch):
    """Second pass, findings 1 and 2: on Postgres each statement's cost grew
    with the number of merchants it was scoped to, times the runs kept, and
    the list made 24 of them over every merchant of the account. A page now
    covers MERCHANTS_PER_PAGE merchants in two statements."""
    from okwan_api import exceptions as route
    async with world() as w:
        c, store, root = w["client"], w["store"], w["root"]
        for n in range(120):
            await store.create_tenant(f"Store {n}", parent_id=root.id)
        calls = []
        real = store.newest_runs_all

        async def counting(ids, **kw):
            calls.append(len(ids))
            return await real(ids, **kw)

        monkeypatch.setattr(store, "newest_runs_all", counting)
        body = (await c.get(f"/v1/tenants/{root.id}/exceptions", headers=w["as_root"])).json()
        assert calls == [route.MERCHANTS_PER_PAGE, route.MERCHANTS_PER_PAGE] and body["next"]


async def test_failed_runs_never_prune_the_newest_good_run(monkeypatch):
    """Second pass, findings 3 and 4: history was the newest 50 runs, failed
    ones included, so fifty failures in an outage pruned the last good run,
    and, with a slow run that read earlier but finished later, brought a
    resolved finding back. Good and failed runs are now kept apart, each the
    newest by when it read."""
    from okwan_vault.models import RUNS_KEPT
    async with world() as w:
        store, shop = w["store"], w["shop"]
        t0 = datetime(2026, 11, 2, 9, tzinfo=UTC)

        async def run(start, end, status="ok", rows=None):
            return await store.add_run(shop.id, kind="across", name="rails", surface="rest", status=status,
                                       started_at=t0 + timedelta(minutes=start), finished_at=t0 + timedelta(minutes=end),
                                       summary={} if status == "ok" else None,
                                       rows=rows if status == "ok" else None,
                                       error=None if status == "ok" else "down")

        stale = await run(0, 30, rows=[{"outcome": "collected_twice", "order": {"ref": "#1002"}}])
        fresh = await run(10, 11, rows=[])
        for n in range(RUNS_KEPT + 10):
            await run(40 + n, 41 + n, status="failed")
        good = await store.newest_runs([shop.id], "across", "rails", ok_only=True)
        assert good[shop.id].id == fresh.id
        kept = {r.id for r in await store.list_runs(shop.id, "rails", 200)}
        assert {stale.id, fresh.id} <= kept and len(kept) == 2 + RUNS_KEPT


async def test_a_listing_and_the_support_assistant_see_counts_not_a_files_references():
    """Second pass, finding 5: a run's summary keeps its open items, which
    quote the merchant's files; the support assistant's recent runs sent
    summaries to the model, and the transcript outlived the file. Listings
    and the assistant see the counts only."""
    from okwan_api import support
    async with world() as w:
        c, shop = w["client"], w["shop"]
        await _upload(c, shop.id, BATCHES, BATCH_MAPPING)
        await _upload(c, shop.id, STATEMENT, BANK_MAPPING, "statement.csv")
        assert (await c.post(f"/v1/tenants/{shop.id}/reconciliations/batches_deposits")).status_code == 200
        listing = (await c.get(f"/v1/tenants/{shop.id}/runs")).json()["data"]
        told = await support.run_tool(shop, None, None, "recent_runs", {"tenant_id": shop.id}, [])
        for seen in (listing, told):
            text = repr(seen)
            assert all(s not in text for s in ("B-5", "D-4", "3884", "2026-09-08")), text[:300]
            assert seen[0]["summary"]["open"]["found"] == 2


def test_one_check_of_a_merchant_cannot_keep_its_others_off_the_accounts_list():
    """Second pass, finding 6: a merchant's 50 oldest items could all be its
    own file findings, so its double collection was on no page. A merchant's
    items are taken in turn from each of its checks."""
    from okwan_api import exceptions as route
    files = [{"check": "batches_deposits", "key": f"k{i}", "dated_on": "2026-07-01", "amount_minor": 1,
              "first_seen": None} for i in range(80)]
    fold = [{"check": "rails", "key": "twice", "dated_on": "2026-11-01", "amount_minor": 99_900, "first_seen": None}]
    chosen = route._in_turn(files + fold)
    assert len(chosen) == route.PER_MERCHANT and any(i["key"] == "twice" for i in chosen)
