"""Findings that last from one run to the next, and one list of what is open.

A check's stored run listed a batch never deposited; run it again and the
same batch was listed again with nothing to say it was yesterday's, so a
person re-checked what they had already looked at. Each finding now has a
key made from what it is about, carries when it was first seen from one
stored run to the next, and `GET /v1/tenants/{id}/exceptions` lists every
open finding of a tenant and its merchants, across checks, oldest first.

What would fail silently: a key made from the run, so every run's findings
look new; a first-seen time reset by a failed run between two good ones;
two findings alike in every reference folded into one; a finding that went
away still listed; a merchant's findings shown to another account; a stored
row carrying more than it did before.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from okwan_recon import exceptions as exc
from okwan_recon.registry import get as get_reconciliation
from test_files_connector import BANK_MAPPING, BATCH_MAPPING, BATCHES, STATEMENT, _upload

T1 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 13, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _read_in_september_2026(as_of):
    as_of(2026, 9, 20)


@pytest.fixture
async def api():
    """A dealer group with one store, each with an admin key, on the
    in-memory store, as in test_files_connector."""
    import httpx
    from okwan_api.auth import set_store
    from okwan_api.main import app
    from okwan_vault import EnvMasterKey, MemoryStore, new_key

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    root = await store.create_tenant("Dealer group")
    shop = await store.create_tenant("Store 12", parent_id=root.id)
    key, _ = await store.issue_key(shop.id, scope="admin")
    root_key, _ = await store.issue_key(root.id, scope="admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        c.headers["Authorization"] = f"Bearer {key}"
        yield c, store, shop, root_key


def _unpaid(ref: str, amount: int, when: str) -> dict:
    return {"status": "unmatched_left", "left_ref": ref, "right_ref": None,
            "evidence": {"record": {"amount_minor": amount, "currency": "USD", "when": when}}}


# ── the key ─────────────────────────────────────────────────────────

def test_a_finding_has_the_same_key_in_every_run_and_a_clean_row_has_none():
    first = exc.keyed("batches_deposits", "pair", [_unpaid("B-5", 3884, "2026-09-08"),
                                                   {"status": "matched", "left_ref": "B-4", "right_ref": "D-2"}])
    again = exc.keyed("batches_deposits", "pair", [{"status": "matched_explained"}, _unpaid("B-5", 3884, "2026-09-08")])
    assert first[1][0] is None and again[0][0] is None
    assert first[0][0] == again[1][0] and len(first[0][0]) == 24


def test_the_key_is_what_the_finding_is_about_not_its_amount_or_wording():
    """A batch's amount or the reason given can be corrected between runs;
    it is the same finding."""
    a = exc.keyed("batches_deposits", "pair", [_unpaid("B-5", 3884, "2026-09-08")])[0][0]
    b = exc.keyed("batches_deposits", "pair", [{**_unpaid("B-5", 4000, "2026-09-08"), "reason": "new"}])[0][0]
    other_check = exc.keyed("square_payouts_deposits", "pair", [_unpaid("B-5", 3884, "2026-09-08")])[0][0]
    other_side = exc.keyed("batches_deposits", "pair", [
        {"status": "unmatched_right", "left_ref": None, "right_ref": "B-5"}])[0][0]
    assert a == b and len({a, other_check, other_side}) == 3


def test_two_findings_alike_in_every_reference_stay_two():
    """Two bank lines with no reference, the same amount on the same day:
    the second must not swallow the first."""
    line = {"status": "unmatched_right", "left_ref": None, "right_ref": None,
            "evidence": {"record": {"amount_minor": 1200, "currency": "USD", "when": "2026-09-10"}}}
    other = {**line, "evidence": {"record": {"amount_minor": 1300, "currency": "USD", "when": "2026-09-10"}}}
    keys = [k for k, _ in exc.keyed("batches_deposits", "pair", [line, line, other])]
    assert len(set(keys)) == 3


def test_a_reference_reads_the_same_fresh_and_as_postgres_stored_it():
    """Postgres keeps a NUL in a reference as U+FFFD; the key must not
    change between the result and the run read back."""
    fresh = exc.keyed("batches_deposits", "pair", [_unpaid("B\x005", 1, "2026-09-08")])[0][0]
    stored = exc.keyed("batches_deposits", "pair", [_unpaid("B�5", 1, "2026-09-08")])[0][0]
    assert fresh == stored


def test_a_folds_finding_is_its_order_and_outcome():
    row = {"outcome": "collected_twice", "order": {"ref": "#1002", "currency": "USD"}}
    a = exc.keyed("rails", "across", [row, {"outcome": "collected", "order": {"ref": "#1003"}}])
    b = exc.keyed("rails", "across", [{**row, "collected_minor": 1}])
    assert a[1][0] is None and a[0][0] == b[0][0]


# ── carried from run to run ─────────────────────────────────────────

def test_a_finding_keeps_the_time_it_was_first_seen_and_a_new_one_starts_now():
    first = exc.carry("batches_deposits", "pair", [_unpaid("B-5", 3884, "2026-09-08")], None, T1)
    assert first[0]["first_seen"] == T1.isoformat()
    second = exc.carry("batches_deposits", "pair",
                       [_unpaid("B-5", 3884, "2026-09-08"), _unpaid("B-6", 100, "2026-09-11")], first, T2)
    assert [r["first_seen"] for r in second] == [T1.isoformat(), T2.isoformat()]
    assert second[0]["key"] == first[0]["key"]


def test_carrying_adds_a_key_and_a_time_to_findings_and_nothing_else():
    clean = {"status": "matched", "left_ref": "B-4", "right_ref": "D-2"}
    out = exc.carry("batches_deposits", "pair", [clean, _unpaid("B-5", 3884, "2026-09-08")], None, T1)
    assert out[0] is clean
    assert set(out[1]) - set(_unpaid("B-5", 3884, "2026-09-08")) == {"key", "first_seen"}
    assert exc.carry("batches_deposits", "pair", None, None, T1) is None


def test_a_first_seen_time_that_is_not_text_is_not_carried():
    bad = [{**_unpaid("B-5", 3884, "2026-09-08"), "first_seen": 12345}]
    assert exc.carry("batches_deposits", "pair", [_unpaid("B-5", 3884, "2026-09-08")], bad, T2)[0]["first_seen"] == T2.isoformat()


# ── the items a person works from ───────────────────────────────────

def test_an_unpaid_batch_is_its_amount_its_day_and_the_days_since():
    rows = exc.carry("batches_deposits", "pair", [_unpaid("B-5", -3884, "2026-09-08")], None, T1)
    (item,) = exc.open_items("batches_deposits", "pair", rows, datetime(2026, 9, 20, 15, tzinfo=UTC))
    assert {k: item[k] for k in ("finding", "left_ref", "right_ref", "amount_minor", "currency", "dated_on",
                                 "first_seen", "open_days")} == {
        "finding": "unmatched_left", "left_ref": "B-5", "right_ref": None, "amount_minor": 3884,
        "currency": "USD", "dated_on": "2026-09-08", "first_seen": T1.isoformat(), "open_days": 12}


def test_amounts_that_differ_put_the_difference_at_stake():
    row = {"status": "matched_discrepant", "left_ref": "B-9", "right_ref": "D-9", "discrepancy": -250,
           "evidence": {"compared": {"currency": "USD"}, "dated": {"left": "2026-09-01", "right": "2026-09-03"}}}
    (item,) = exc.open_items("batches_deposits", "pair", [row], datetime(2026, 9, 4, tzinfo=UTC))
    assert (item["amount_minor"], item["currency"], item["dated_on"], item["open_days"]) == (250, "USD", "2026-09-01", 3)


def test_a_finding_with_no_day_is_aged_from_when_it_was_first_seen():
    row = {"status": "ambiguous", "left_ref": "B-7", "right_ref": None, "first_seen": T1.isoformat()}
    (item,) = exc.open_items("batches_deposits", "pair", [row], datetime(2026, 9, 15, tzinfo=UTC))
    assert (item["dated_on"], item["open_days"], item["amount_minor"]) == (None, 3, None)


def test_a_fold_finding_puts_the_excess_or_the_order_at_stake():
    twice = {"outcome": "collected_twice", "order": {"ref": "#1002", "currency": "USD"},
             "order_total_minor": 99_900, "collected_minor": 199_800, "collected_on": "2026-09-02"}
    never = {"outcome": "uncollected", "order": {"ref": "#1006", "currency": "USD"},
             "order_total_minor": 7_500, "collected_minor": 0}
    items = exc.open_items("rails", "across", [twice, never], datetime(2026, 9, 5, tzinfo=UTC))
    assert [(i["order_ref"], i["amount_minor"], i["dated_on"]) for i in items] == [
        ("#1002", 99_900, "2026-09-02"), ("#1006", 7_500, None)]


def test_a_check_is_versioned_by_its_declaration():
    from okwan_recon.declarations import batches_deposits as spec
    assert exc.version(spec) == exc.version(spec)
    changed = spec.model_copy(update={"lookback": "90d"})
    assert exc.version(changed) != exc.version(spec)
    assert exc.version(spec, [spec]) != exc.version(spec)


# ── through the API ─────────────────────────────────────────────────

async def _run(client, shop):
    r = await client.post(f"/v1/tenants/{shop.id}/reconciliations/batches_deposits")
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


async def test_a_stored_run_carries_its_findings_first_seen_and_its_checks_version(api):
    client, store, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    first = await store.get_run(shop.id, await _run(client, shop))
    second = await store.get_run(shop.id, await _run(client, shop))

    def seen(rec):
        return {r["left_ref"] or r["right_ref"]: r["first_seen"] for r in rec.rows if "key" in r}

    assert seen(first) == seen(second) and set(seen(first)) == {"B-5", "D-4"}
    assert second.started_at > first.started_at
    assert first.summary["check_version"] == second.summary["check_version"] == exc.version(
        get_reconciliation("batches_deposits"))
    # Clean matches carry nothing new, and no row carries a record.
    assert all("key" not in r for r in second.rows if r["status"] == "matched")
    assert "MERCHANT DEPOSIT" not in json.dumps(second.rows)


async def test_a_failed_run_between_two_good_ones_does_not_reset_first_seen(api, monkeypatch):
    client, store, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    first = await store.get_run(shop.id, await _run(client, shop))
    from okwan_api import runs
    await runs.record_run(shop, get_reconciliation("batches_deposits"), "rest", runs.run_clock(),
                          error=RuntimeError("upstream down"))
    third = await store.get_run(shop.id, await _run(client, shop))
    assert {r["first_seen"] for r in third.rows if "key" in r} == {r["first_seen"] for r in first.rows if "key" in r}


async def test_the_list_holds_every_open_finding_with_its_age_and_what_is_at_stake(api, monkeypatch):
    client, _, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    run_id = await _run(client, shop)
    from okwan_api import exceptions as route
    monkeypatch.setattr(route, "now", lambda: datetime(2026, 9, 20, 15, tzinfo=UTC))

    r = await client.get(f"/v1/tenants/{shop.id}/exceptions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    got = [(i["finding"], i["left_ref"], i["right_ref"], i["amount_minor"], i["dated_on"], i["open_days"],
            i["merchant"], i["check"], i["run_id"]) for i in body["data"]]
    assert got == [
        ("unmatched_left", "B-5", None, 3_884, "2026-09-08", 12, "Store 12", "batches_deposits", run_id),
        ("unmatched_right", None, "D-4", 1_200, "2026-09-10", 10, "Store 12", "batches_deposits", run_id),
    ]
    (check,) = body["checks"]
    assert (check["check"], check["title"], check["open"], check["cut"], check["last_run"]["status"]) == (
        "batches_deposits", "Processor batches vs bank deposits", 2, False, "ok")


async def test_a_finding_that_went_away_is_no_longer_listed_and_the_rest_keep_their_age(api):
    client, _, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    await _run(client, shop)
    before = {i["left_ref"] or i["right_ref"]: i for i in
              (await client.get(f"/v1/tenants/{shop.id}/exceptions")).json()["data"]}
    # The bank paid B-5 after all: a later statement shows the deposit.
    late = b"Date,Description,Amount,Ref\n09/09/2026,MERCHANT DEPOSIT,38.84,D-5\n"
    await _upload(client, shop, late, BANK_MAPPING, "late.csv")
    await _run(client, shop)
    after = {i["left_ref"] or i["right_ref"]: i for i in
             (await client.get(f"/v1/tenants/{shop.id}/exceptions")).json()["data"]}
    assert set(before) == {"B-5", "D-4"} and set(after) == {"D-4"}
    assert after["D-4"]["first_seen"] == before["D-4"]["first_seen"] and after["D-4"]["key"] == before["D-4"]["key"]


async def test_an_account_sees_its_merchants_findings_and_no_one_elses(api):
    client, store, shop, root_key = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    await _run(client, shop)
    root = await store.get_tenant(shop.parent_id)
    as_root = {"Authorization": f"Bearer {root_key}"}
    mine = (await client.get(f"/v1/tenants/{root.id}/exceptions", headers=as_root)).json()
    assert {i["tenant_id"] for i in mine["data"]} == {shop.id} and mine["total"] == 2

    # The merchant cannot read its account, another account cannot read either.
    assert (await client.get(f"/v1/tenants/{root.id}/exceptions")).status_code == 404
    other = await store.create_tenant("Someone else")
    other_key, _ = await store.issue_key(other.id, scope="admin")
    as_other = {"Authorization": f"Bearer {other_key}"}
    for target in (shop.id, root.id, "ten_nobody"):
        r = await client.get(f"/v1/tenants/{target}/exceptions", headers=as_other)
        assert r.status_code == 404, (target, r.text)
    # A read key reads data, not the run history this is made from.
    read_key, _ = await store.issue_key(shop.id, scope="read")
    r = await client.get(f"/v1/tenants/{shop.id}/exceptions", headers={"Authorization": f"Bearer {read_key}"})
    assert r.status_code == 403


async def test_a_deleted_file_takes_its_findings_off_the_list(api):
    client, _, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    statement = await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    await _run(client, shop)
    assert (await client.get(f"/v1/tenants/{shop.id}/exceptions")).json()["total"] == 2
    assert (await client.delete(f"/v1/tenants/{shop.id}/imports/{statement['id']}")).status_code == 204
    body = (await client.get(f"/v1/tenants/{shop.id}/exceptions")).json()
    assert body == {**body, "data": [], "total": 0, "checks": []}


async def test_when_the_newest_run_failed_the_list_keeps_the_last_good_runs_findings_and_says_so(api):
    client, _, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    good = await _run(client, shop)
    from okwan_api import runs
    failed = await runs.record_run(shop, get_reconciliation("batches_deposits"), "rest", runs.run_clock(),
                                   error=RuntimeError("upstream down"))
    body = (await client.get(f"/v1/tenants/{shop.id}/exceptions")).json()
    (check,) = body["checks"]
    assert (check["last_run"]["id"], check["last_run"]["status"], check["findings_from"]) == (failed, "failed", good)
    assert body["total"] == 2 and {i["run_id"] for i in body["data"]} == {good}
