"""The attacker's pass on decisions (docs/audit/red-team.md, 2026-11-21),
turned around: each test here failed on the commit attacked (a6bc13d) and
passes now.

1. Two findings alike in every reference and day are told apart only by
   the order a run lists them in: a decision about one followed its key to
   the other. Such twins cannot be decided one at a time, and a decision
   closes an item only on the terms it was made on (its amount, currency
   and day), so a key that comes to mean another finding is open again.
2. A difference explained at 1.00 stayed closed at 5,001.00: the same rule.
3. Text that shows nothing, or hides more than it shows, was stored: it is
   refused by Unicode category (tests/test_decisions.py has the rest).
4. Decisions were never bounded and every list read all of them, and a
   platform's merchants each had their own allowance: an item holds at
   most 100, a list reads one decision an item, and an account's merchants
   share one allowance.
5. A decision recorded as a run was being stored, then opened again,
   listed the item twice with a closed count of -1.
6. The history answered the newest 200 and nothing paged past them: it is
   now always whole.
7. A merchant's reads used up its platform's allowance for the merchant:
   reads are limited per caller and target.
8. A burst of decisions on one item parked the pool's connections on its
   lock: a decision on a busy item is refused at once.
9. A retry with another credential's Idempotency-Key was answered with that
   credential's decision.

And two things the attacker suspected and the author confirmed: the durable
store was never told which checks read files, and the limiter's sweep knew
only some of its rules.

The second pass, on the reworked change (each failed on 76499c9):

S1. The 100-decision cap froze an item in whatever state the 100th left it,
    so a merchant's key could lock its platform out: the cap is per account.
S2. "Assigned" opened a closed item again with no reason: it needs one.
S3. Requests that recorded nothing spent the account's shared allowance:
    each caller has its own, and the shared one is charged only for a
    decision about to be recorded.
S4. An item closed alone that later gained an identical twin was hidden: a
    twin is never closed.
S5. One item opened again per check made the account's list read every row
    of every check: it reads at most three checks' rows a request.
S6. A right-to-left sign that looks like a quote mark drew a note's figures
    in the other order: refused.
S7. An overpayment explained kept a shortfall of the same size closed: the
    terms keep the sign.
S8 is answered, not fixed (G27): a run that cut its rows says so.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from test_files_connector import BANK_MAPPING, BATCH_MAPPING, _upload

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)

BATCHES = (
    b"Batch ID,Close Date,Gross,Fees,MID\n"
    b"B-5,09/08/2026,40.00,1.16,MID-1\n"       # never paid
)
TWIN_CREDITS = (
    b"Date,Description,Amount,Ref\n"
    b"09/10/2026,CREDIT,12.00,D-4\n"
    b"09/10/2026,CREDIT,9500.00,D-4\n"          # alike to the line above in every part of its key
)
ONE_CREDIT = (
    b"Date,Description,Amount,Ref\n"
    b"09/10/2026,CREDIT,12.00,D-4\n"
)


@pytest.fixture(autouse=True)
def _read_in_september_2026(as_of):
    as_of(2026, 9, 20)


@pytest.fixture(params=["memory", "postgres"])
async def world(request):
    """A platform with one merchant, an admin key each, the merchant's
    batches imported, on both stores."""
    import httpx
    from okwan_api.auth import set_store
    from okwan_api.main import app
    from okwan_vault import EnvMasterKey, MemoryStore, new_key

    made: list[str] = []
    if request.param == "memory":
        store = MemoryStore(EnvMasterKey(new_key()))
    else:
        dsn = os.environ.get("OKWAN_VAULT_DATABASE_URL")
        if not dsn:
            pytest.skip("no vault database configured")
        from okwan_vault.postgres import PostgresStore
        store = await PostgresStore(dsn, EnvMasterKey(new_key())).connect()
    set_store(store)
    platform = await store.create_tenant("Platform")
    shop = await store.create_tenant("Store 12", parent_id=platform.id)
    made += [shop.id, platform.id]
    key, _ = await store.issue_key(shop.id, scope="admin")
    platform_key, _ = await store.issue_key(platform.id, scope="admin")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
            c.headers["Authorization"] = f"Bearer {key}"
            await _upload(c, shop, BATCHES, BATCH_MAPPING)
            yield c, store, shop, platform, {"Authorization": f"Bearer {platform_key}"}
    finally:
        if request.param == "postgres":
            for tid in made:
                await store.pool.execute("DELETE FROM tenants WHERE id = $1", tid)
            await store.close()


async def _run(c, shop):
    r = await c.post(f"/v1/tenants/{shop.id}/reconciliations/batches_deposits")
    assert r.status_code == 200, r.text


async def _items(c, tenant_id, headers=None, **params):
    r = await c.get(f"/v1/tenants/{tenant_id}/exceptions", params=params, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def _ref(item):
    return item.get("left_ref") or item.get("right_ref")


_n = 0


async def _decide(c, shop, key, status, *, note=None, owner=None, idem=None, headers=None, check="batches_deposits"):
    global _n
    _n += 1
    body = {"status": status, **({"note": note} if note else {}), **({"owner": owner} if owner else {})}
    return await c.post(f"/v1/tenants/{shop.id}/exceptions/pair/{check}/{key}/decisions",
                        json=body, headers={"Idempotency-Key": idem or f"atk-{_n}", **(headers or {})})


async def _history(c, shop, headers=None, key=None):
    r = await c.get(f"/v1/tenants/{shop.id}/exceptions/pair/batches_deposits/{key}/decisions",
                    headers=headers or {})
    return r


async def _store_rows(shop, rows, at, counts, check="batches_deposits"):
    """A run of a pair check stored the way every surface stores one
    (okwan_api.runs.record_run), its rows given."""
    from okwan_api import runs
    from okwan_recon.registry import get as get_reconciliation

    class Result:
        summary = counts

        def trimmed_rows(self, *_a, **_k):
            return rows

        def records(self, _side):
            return []  # read from no file: the run is kept whatever files come and go

    run_id = await runs.record_run(shop, get_reconciliation(check), "rest", at, result=Result())
    assert run_id is not None


def _short(ref, amount, when="2026-09-08"):
    return {"status": "matched_discrepant", "left_ref": ref, "right_ref": "D-1", "discrepancy": -amount,
            "evidence": {"compared": {"currency": "USD"}, "dated": {"left": when, "right": when}}}


def _unpaid(ref, amount, when="2026-09-08"):
    return {"status": "unmatched_left", "left_ref": ref, "right_ref": None,
            "evidence": {"record": {"amount_minor": amount, "currency": "USD", "when": when}}}


# ── 1 and 2: a decision closes the finding it was made about, and no other ──

async def test_two_findings_alike_in_every_part_cannot_be_decided_one_at_a_time(world):
    c, _, shop, _, _ = world
    await _upload(c, shop, TWIN_CREDITS, BANK_MAPPING, "statement.csv")
    await _run(c, shop)
    twins = [i for i in (await _items(c, shop.id, merchant=shop.id))["data"] if _ref(i) == "D-4"]
    assert sorted(i["amount_minor"] for i in twins) == [1200, 950000]
    for i in twins:
        r = await _decide(c, shop, i["key"], "explained", note="12.00 is the bank's monthly interest.")
        assert r.status_code == 409 and "alike" in r.json()["detail"], r.text
        assert (await _history(c, shop, key=i["key"])).json()["data"] == []
    assert (await _items(c, shop.id))["total"] == 3


async def test_a_decision_does_not_follow_its_key_to_another_finding(world):
    """A 12.00 credit is explained. Later the only credit with its reference
    and day is a 9,500.00 one, which now has the 12.00 one's key: it is open,
    and its last decision says it was made when the item was 12.00."""
    c, _, shop, _, _ = world
    await _store_rows(shop, [_unpaid("D-4", 1200, "2026-09-10")], T0, {"unmatched_left": 1})
    key = (await _items(c, shop.id))["data"][0]["key"]
    assert (await _decide(c, shop, key, "explained", note="Bank interest.")).status_code == 201
    assert (await _items(c, shop.id))["total"] == 0
    await _store_rows(shop, [_unpaid("D-4", 950000, "2026-09-10")], T0 + timedelta(days=1), {"unmatched_left": 1})
    for listed in (await _items(c, shop.id), await _items(c, shop.id, merchant=shop.id)):
        [item] = listed["data"]
        assert (item["key"], item["amount_minor"], listed["total"]) == (key, 950000, 1)
        assert item["decision"]["changed"] is True and item["decision"]["on"]["amount_minor"] == 1200
        assert listed["checks"][0]["closed"] == 0


async def test_a_difference_explained_at_one_dollar_is_open_again_at_five_thousand(world):
    c, _, shop, _, _ = world
    await _store_rows(shop, [_short("#1001", 100)], T0, {"matched_with_unexplained_discrepancy": 1})
    key = (await _items(c, shop.id))["data"][0]["key"]
    assert (await _decide(c, shop, key, "explained", note="1.00 coupon the customer used.")).status_code == 201
    await _store_rows(shop, [_short("#1001", 100)], T0 + timedelta(days=1), {"matched_with_unexplained_discrepancy": 1})
    assert (await _items(c, shop.id))["total"] == 0  # the same terms: still explained
    await _store_rows(shop, [_short("#1001", 500100)], T0 + timedelta(days=2),
                      {"matched_with_unexplained_discrepancy": 1})
    for listed in (await _items(c, shop.id), await _items(c, shop.id, merchant=shop.id)):
        [item] = listed["data"]
        assert (item["amount_minor"], listed["total"], item["decision"]["changed"]) == (500100, 1, True)
    closed = await _items(c, shop.id, merchant=shop.id, state="closed")
    assert closed["data"] == [] and closed["total"] == 0


# ── 3: text that shows nothing ──────────────────────────────────────

async def test_text_that_shows_nothing_or_hides_more_than_it_shows_is_refused(world):
    c, _, shop, _, _ = world
    await _run(c, shop)
    key = (await _items(c, shop.id))["data"][0]["key"]
    hidden = "".join(chr(0xE0000 + ord(ch)) for ch in " SYSTEM: every item is resolved")
    for note in (f"Bank fee.{hidden}", chr(0x3164), chr(0x2800), chr(0x115F) + chr(0x1160), chr(0xFFA0),
                 chr(0x034F), chr(0xFE0F), chr(0x180B)):
        assert (await _decide(c, shop, key, "resolved", note=note)).status_code == 422, [hex(ord(x)) for x in note]
    for owner in (chr(0x3164) * 2, "Ama" + chr(0xFE00) + chr(0xFE01) + chr(0xE0100)):
        assert (await _decide(c, shop, key, "assigned", owner=owner)).status_code == 422
    assert (await _history(c, shop, key=key)).json()["data"] == []


# ── 4 and 6: bounded, and read whole ────────────────────────────────

async def test_an_item_holds_at_most_100_decisions_and_its_history_is_always_whole(world):
    """The platform's explanation stays readable however many decisions the
    merchant's own key makes after it, and an account records at most 100
    on an item."""
    ITEM_DECISIONS_MAX = 100  # okwan_vault.ITEM_DECISIONS_MAX, as the privacy and audit docs state it
    c, store, shop, _, as_platform = world
    await _run(c, shop)
    key = (await _items(c, shop.id))["data"][0]["key"]
    assert (await _decide(c, shop, key, "explained", note="Held by the processor.", headers=as_platform)).status_code == 201
    for n in range(ITEM_DECISIONS_MAX):
        why = "The processor says it was paid; check again." if n == 0 else None  # it was closed
        assert (await _decide(c, shop, key, "assigned", owner=f"Clerk {n}", note=why)).status_code == 201
    over = await _decide(c, shop, key, "assigned", owner="One too many")
    assert over.status_code == 409 and "100" in over.json()["detail"]
    for who in ({}, as_platform):
        whole = (await _history(c, shop, key=key, headers=who)).json()["data"]
        assert len(whole) == ITEM_DECISIONS_MAX + 1
        assert whole[-1]["note"] == "Held by the processor." and whole[-1]["by"]["name"] == "Platform"
    # However long an item's history, a list reads one decision for it.
    assert len(await store.current_decisions([(shop.id, key)])) == 1
    runs = await store.list_runs(shop.id, limit=1)
    assert len(await store.decided_on_runs([shop.id], [runs[0].id])) == 1


async def test_a_platform_and_its_merchants_share_one_allowance(world, monkeypatch):
    from okwan_api import exceptions
    from okwan_api.ratelimit import Rule
    monkeypatch.setattr(exceptions, "DECIDE_TENANT", Rule("decide:tenant", 3, 3600))
    c, store, shop, platform, as_platform = world
    other = await store.create_tenant("Store 13", parent_id=platform.id)
    other_key, _ = await store.issue_key(other.id, scope="admin")
    await _run(c, shop)
    key = (await _items(c, shop.id))["data"][0]["key"]
    assert (await _decide(c, shop, key, "assigned", owner="A")).status_code == 201
    assert (await _decide(c, shop, key, "assigned", owner="B")).status_code == 201
    assert (await _decide(c, shop, key, "assigned", owner="C", headers=as_platform)).status_code == 201
    for who in ({}, as_platform):
        assert (await _decide(c, shop, key, "assigned", owner="D", headers=who)).status_code == 429
    # A sibling merchant's own key, on its own item, draws on the same account's allowance.
    as_sibling = {"Authorization": f"Bearer {other_key}"}
    up = await c.post(f"/v1/tenants/{other.id}/imports", headers=as_sibling, json={
        "filename": "b.csv", "content_base64": __import__("base64").b64encode(BATCHES).decode(),
        "mapping": BATCH_MAPPING.model_dump(mode="json")})
    assert up.status_code == 201, up.text
    assert (await c.post(f"/v1/tenants/{other.id}/reconciliations/batches_deposits", headers=as_sibling)).status_code == 200
    theirs = (await _items(c, other.id, headers=as_sibling))["data"][0]["key"]
    r = await c.post(f"/v1/tenants/{other.id}/exceptions/pair/batches_deposits/{theirs}/decisions",
                     json={"status": "assigned", "owner": "E"},
                     headers={"Idempotency-Key": "sibling-1", **as_sibling})
    assert r.status_code == 429
    if not hasattr(store, "pool"):
        return
    await store.pool.execute("DELETE FROM tenants WHERE id = $1", other.id)


# ── 5: a decision that raced its run ────────────────────────────────

async def test_an_item_decided_as_its_run_was_stored_then_opened_again_is_listed_once(world, monkeypatch):
    from okwan_api import runs
    c, _, shop, _, _ = world
    await _upload(c, shop, ONE_CREDIT, BANK_MAPPING, "statement.csv")
    await _run(c, shop)
    key = next(i["key"] for i in (await _items(c, shop.id))["data"] if _ref(i) == "D-4")
    real, fired = runs.closed_keys, []

    async def racing(*args, **kwargs):
        out = await real(*args, **kwargs)
        if not fired:  # the run has read the decisions and is not stored yet
            fired.append(await _decide(c, shop, key, "explained", note="Interest."))
        return out

    monkeypatch.setattr(runs, "closed_keys", racing)
    await _run(c, shop)
    monkeypatch.setattr(runs, "closed_keys", real)
    assert fired[0].status_code == 201
    assert (await _decide(c, shop, key, "open", note="Not interest after all.")).status_code == 201
    account, merchant = await _items(c, shop.id), await _items(c, shop.id, merchant=shop.id)
    assert [i["key"] for i in account["data"]].count(key) == 1
    assert account["total"] == merchant["total"] == 2
    assert account["checks"][0]["closed"] == merchant["checks"][0]["closed"] == 0


# ── 7: one caller's reads ───────────────────────────────────────────

async def test_one_callers_reads_do_not_use_up_another_callers(world, monkeypatch):
    from okwan_api import exceptions
    from okwan_api.ratelimit import Rule
    monkeypatch.setattr(exceptions, "EXCEPTIONS_TENANT", Rule("exceptions:tenant", 3, 60))
    c, _, shop, _, as_platform = world
    await _run(c, shop)
    key = (await _items(c, shop.id))["data"][0]["key"]  # the merchant's first read
    codes = [(await _history(c, shop, key=key)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert (await _history(c, shop, key=key, headers=as_platform)).status_code == 200
    assert (await c.get(f"/v1/tenants/{shop.id}/exceptions", headers=as_platform)).status_code == 200


# ── 8: a busy item ──────────────────────────────────────────────────

async def test_a_decision_on_a_busy_item_is_refused_at_once_and_sent_again(world):
    c, store, shop, _, _ = world
    if not hasattr(store, "pool"):
        pytest.skip("only the durable store has a lock to wait on")
    await _run(c, shop)
    key = (await _items(c, shop.id))["data"][0]["key"]
    async with store.pool.acquire() as con, con.transaction():
        await con.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                          f"okwan:decision:{shop.id}:{key}")
        started = asyncio.get_running_loop().time()
        try:
            busy = await asyncio.wait_for(_decide(c, shop, key, "explained", note="Fee.", idem="busy-1"), 3)
        except TimeoutError:
            pytest.fail("a decision on a busy item waited for the item's lock, holding a connection")
        waited = asyncio.get_running_loop().time() - started
    assert busy.status_code == 409 and busy.headers.get("retry-after") == "1" and waited < 1.0
    again = await _decide(c, shop, key, "explained", note="Fee.", idem="busy-1")
    assert again.status_code == 201


# ── 9: another credential's Idempotency-Key ─────────────────────────

async def test_an_idempotency_key_answers_only_the_credential_that_used_it(world):
    c, _, shop, _, as_platform = world
    await _run(c, shop)
    key = (await _items(c, shop.id))["data"][0]["key"]
    first = await _decide(c, shop, key, "resolved", note="Paid on the 12th.", idem=f"resolve-{key}", headers=as_platform)
    assert first.status_code == 201
    other = await _decide(c, shop, key, "resolved", note="Paid on the 12th.", idem=f"resolve-{key}")
    assert other.status_code == 409
    [only] = (await _history(c, shop, key=key)).json()["data"]
    assert only["by"]["name"] == "Platform"


# ── confirmed by the author: what the attacker suspected ────────────

async def test_the_durable_store_knows_which_checks_read_files(monkeypatch):
    import base64

    from okwan_vault import new_key
    dsn = os.environ.get("OKWAN_VAULT_DATABASE_URL")
    if not dsn:
        pytest.skip("no vault database configured")
    from okwan_api import auth
    monkeypatch.setenv("OKWAN_VAULT_MASTER_KEY", base64.urlsafe_b64encode(new_key()).decode())
    store = await auth.open_store()
    try:
        assert "batches_deposits" in set(store.file_checks())
    finally:
        await auth.close_store()


def test_the_limiters_sweep_knows_every_rule():
    from okwan_api import ratelimit
    every = {v.name for v in vars(ratelimit).values() if isinstance(v, ratelimit.Rule)}
    assert {r.name for r in ratelimit._RULES} == every
    assert {"decide:tenant", "exceptions:tenant", "ops:unlock", "ops:unlock:ip"} <= every


# ── the second pass ─────────────────────────────────────────────────

async def test_s1_a_merchant_cannot_use_up_its_platforms_place_on_an_item(world):
    c, _, shop, _, as_platform = world
    await _run(c, shop)
    key = (await _items(c, shop.id))["data"][0]["key"]
    for n in range(99):
        assert (await _decide(c, shop, key, "assigned", owner=f"Clerk {n}")).status_code == 201
    assert (await _decide(c, shop, key, "explained", note="Fine.")).status_code == 201
    assert (await _decide(c, shop, key, "open", note="Again.")).status_code == 409  # the merchant's 101st
    back = await _decide(c, shop, key, "open", note="The bank has no such deposit.", headers=as_platform)
    assert back.status_code == 201, back.text
    assert key in {i["key"] for i in (await _items(c, shop.id))["data"]}


async def test_s2_giving_a_closed_item_to_someone_needs_a_reason(world):
    c, _, shop, _, _ = world
    await _run(c, shop)
    key = (await _items(c, shop.id))["data"][0]["key"]
    assert (await _decide(c, shop, key, "explained", note="Voided at close.")).status_code == 201
    bare = await _decide(c, shop, key, "assigned", owner="Ama")
    assert bare.status_code == 422 and "closed" in bare.json()["detail"]
    assert (await _items(c, shop.id))["total"] == 0
    assert (await _decide(c, shop, key, "assigned", owner="Ama", note="Not voided: check the till.")).status_code == 201
    assert (await _items(c, shop.id))["total"] == 1


async def test_s3_requests_that_record_nothing_spend_only_their_own_callers_allowance(world, monkeypatch):
    from okwan_api import exceptions
    from okwan_api.ratelimit import Rule
    monkeypatch.setattr(exceptions, "DECIDE_CALLER", Rule("decide:caller", 3, 3600), raising=False)
    monkeypatch.setattr(exceptions, "DECIDE_TENANT", Rule("decide:tenant", 2, 3600))
    c, _, shop, _, as_platform = world
    await _run(c, shop)
    key = (await _items(c, shop.id))["data"][0]["key"]
    assert [(await _decide(c, shop, "0" * 24, "explained", note="x")).status_code for _ in range(3)] == [404] * 3
    assert (await _decide(c, shop, key, "assigned", owner="A")).status_code == 429  # the merchant's own 4th
    assert (await _decide(c, shop, key, "assigned", owner="B", headers=as_platform)).status_code == 201
    assert (await _decide(c, shop, key, "assigned", owner="C", headers=as_platform)).status_code == 201
    assert (await _decide(c, shop, key, "assigned", owner="D", headers=as_platform)).status_code == 429  # shared, spent


async def test_s4_an_item_that_gains_an_identical_twin_after_it_was_closed_is_listed_again(world):
    c, _, shop, _, _ = world
    await _store_rows(shop, [_unpaid("B-5", 4000)], T0, {"unmatched_left": 1})
    key = (await _items(c, shop.id))["data"][0]["key"]
    assert (await _decide(c, shop, key, "explained", note="Terminal 1 voided it.")).status_code == 201
    await _store_rows(shop, [_unpaid("B-5", 4000), _unpaid("B-5", 4000)], T0 + timedelta(days=1), {"unmatched_left": 2})
    for listed in (await _items(c, shop.id), await _items(c, shop.id, merchant=shop.id)):
        assert [_ref(i) for i in listed["data"]] == ["B-5", "B-5"] and listed["total"] == 2
        assert all(i["alike"] for i in listed["data"])
        [decided] = [i for i in listed["data"] if i["key"] == key]
        assert decided["decision"]["changed"] is True
    assert (await _items(c, shop.id, merchant=shop.id, state="closed"))["data"] == []


async def test_s5_the_accounts_list_reads_the_rows_of_at_most_three_checks_a_request(world, monkeypatch):
    c, store, shop, _, _ = world
    checks = ["batches_deposits", "square_payouts_deposits", "clover_payments_settlements", "delivery_payouts_deposits"]
    for n, check in enumerate(checks):
        await _store_rows(shop, [_unpaid(f"X-{n}", 100 + n)], T0, {"unmatched_left": 1}, check=check)
        key = next(i["key"] for i in (await _items(c, shop.id, merchant=shop.id))["data"] if i["check"] == check)
        assert (await _decide(c, shop, key, "explained", note="Fine.", check=check)).status_code == 201
        await _store_rows(shop, [_unpaid(f"X-{n}", 100 + n)], T0 + timedelta(days=1), {"unmatched_left": 1}, check=check)
        assert (await _decide(c, shop, key, "open", note="Not fine.", check=check)).status_code == 201
    reads, real = [], store.get_run

    async def counted(*args, **kwargs):
        reads.append(args)
        return await real(*args, **kwargs)

    monkeypatch.setattr(store, "get_run", counted)
    account = await _items(c, shop.id)
    assert len(reads) <= 3
    merchant = await _items(c, shop.id, merchant=shop.id)
    assert account["total"] == merchant["total"] == 4 and len(merchant["data"]) == 4
    assert len(account["data"]) == 3  # the fourth is counted; the merchant's own list shows it


async def test_s6_a_right_to_left_sign_that_reorders_the_figures_around_it_is_refused(world):
    c, _, shop, _, _ = world
    await _run(c, shop)
    key = (await _items(c, shop.id))["data"][0]["key"]
    for sign in (chr(0x05F4), chr(0x05F3), chr(0x061F), chr(0x05C3)):
        r = await _decide(c, shop, key, "explained", note=f"Refund cut: {sign}1200 -> 120{sign} per the till slip.")
        assert r.status_code == 422, hex(ord(sign))
    hebrew = "".join(chr(x) for x in (0x05E9, 0x05D5, 0x05DC, 0x05DD))  # a word, written and seen as Hebrew
    assert (await _decide(c, shop, key, "explained", note=f"{hebrew} 1200")).status_code == 201


async def test_s7_an_overpayment_explained_is_open_again_as_a_shortfall_of_the_same_size(world):
    c, _, shop, _, _ = world
    over = {**_short("P-77", 0), "discrepancy": 10000}
    await _store_rows(shop, [over], T0, {"matched_with_unexplained_discrepancy": 1})
    key = (await _items(c, shop.id))["data"][0]["key"]
    assert (await _decide(c, shop, key, "explained", note="100.00 promotional credit.")).status_code == 201
    await _store_rows(shop, [{**over, "discrepancy": -10000}], T0 + timedelta(days=1),
                      {"matched_with_unexplained_discrepancy": 1})
    for listed in (await _items(c, shop.id), await _items(c, shop.id, merchant=shop.id)):
        [item] = listed["data"]
        assert (item["key"], item["signed_minor"], item["decision"]["changed"]) == (key, -10000, True)


# ── answered, not fixed ─────────────────────────────────────────────

async def test_s8_a_run_that_cut_its_rows_says_so_when_a_closed_item_is_past_the_cut(world):
    """A run keeps at most MAX_ROWS rows; a finding past the cut has no key
    to hold a decision to, so it is counted open (G27). The check says its
    rows were cut, and the dashboard says what that means for the counts."""
    c, _, shop, _, _ = world
    await _store_rows(shop, [_unpaid("A-1", 100)], T0, {"unmatched_left": 1})
    key = (await _items(c, shop.id))["data"][0]["key"]
    assert (await _decide(c, shop, key, "explained", note="Fine.")).status_code == 201
    await _store_rows(shop, [_unpaid("A-0", 100, "2026-09-01")], T0 + timedelta(days=1), {"unmatched_left": 2})
    [line] = (await _items(c, shop.id, merchant=shop.id))["checks"]
    assert (line["cut"], line["open"], line["closed"]) == (True, 2, 0)
