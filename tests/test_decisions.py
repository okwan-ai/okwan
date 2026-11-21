"""A person's decision on each open item, and the record of it.

The matching is rules: they decide what is a finding. What a finding means
is a person's call. They give it to someone, explain it (nothing to fix),
resolve it (fixed where it started) or open it again, and say why. Each
decision is added to the item's history and never changed, with the
account and credential that made it, so when an accountant asks why an
item was closed there is an answer. A read key, the kind an AI client is
given, cannot make one.

What would fail silently: an explained item listed again by the next run,
so a person re-checks what they already closed; a count that still holds
it; a decision with no reason, or with text that reads differently from
what it shows; a retried request recorded twice; a decision on something
that is not a finding of the check; a decision outliving the file its note
may quote; another account's item closed or read; two decisions made at
once given one number, or a status before that is not the one before.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from okwan_recon import exceptions as exc
from test_files_connector import BANK_MAPPING, BATCH_MAPPING, BATCHES, STATEMENT, _upload

T0 = datetime(2026, 11, 21, 9, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _read_in_september_2026(as_of):
    as_of(2026, 9, 20)


@pytest.fixture
async def api():
    """A dealer group with one store, each with an admin key, on the
    in-memory store, the store's files imported."""
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
        await _upload(c, shop, BATCHES, BATCH_MAPPING)
        await _upload(c, shop, STATEMENT, BANK_MAPPING, "statement.csv")
        await _run(c, shop)
        yield c, store, shop, root, key, root_key


async def _run(client, shop):
    r = await client.post(f"/v1/tenants/{shop.id}/reconciliations/batches_deposits")
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


async def _items(client, tenant_id, **params):
    r = await client.get(f"/v1/tenants/{tenant_id}/exceptions", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _ref(item):
    return item.get("left_ref") or item.get("right_ref")


async def _key(client, shop, ref):
    return next(i["key"] for i in (await _items(client, shop.id))["data"] if _ref(i) == ref)


_n = 0


async def _decide(client, shop, key, status, *, note=None, owner=None, idem=None, headers=None,
                  check="batches_deposits", kind="pair", tenant=None):
    global _n
    _n += 1
    body = {"status": status, **({"note": note} if note is not None else {}),
            **({"owner": owner} if owner is not None else {})}
    h = {"Idempotency-Key": idem or f"test-{_n}", **(headers or {})}
    return await client.post(f"/v1/tenants/{(tenant or shop).id}/exceptions/{kind}/{check}/{key}/decisions",
                             json=body, headers=h)


async def _history(client, shop, key):
    r = await client.get(f"/v1/tenants/{shop.id}/exceptions/pair/batches_deposits/{key}/decisions")
    assert r.status_code == 200, r.text
    return r.json()


# ── the summary a run keeps ─────────────────────────────────────────

def test_a_closed_item_is_left_out_of_a_runs_summary_and_counted_apart():
    rows = exc.carry("ten_x", "batches_deposits", "pair", [
        {"status": "unmatched_left", "left_ref": "B-5", "right_ref": None,
         "evidence": {"record": {"amount_minor": 3884, "currency": "USD", "when": "2026-09-08"}}},
        {"status": "unmatched_right", "left_ref": None, "right_ref": "D-4",
         "evidence": {"record": {"amount_minor": 1200, "currency": "USD", "when": "2026-09-10"}}},
    ], None, T0)
    shut = rows[0]["key"]
    summary = exc.open_summary("ten_x", "batches_deposits", "pair", rows,
                               {"unmatched_left": 1, "unmatched_right": 1}, closed={shut})
    assert (summary["found"], summary["kept"], summary["closed"]) == (1, 1, 1)
    assert [i["key"] for i in summary["oldest"]] == [rows[1]["key"]]


# ── deciding, through the API ───────────────────────────────────────

async def test_a_person_explains_an_item_with_a_reason_and_it_leaves_the_list(api):
    client, _, shop, _, _, _ = api
    before = await _items(client, shop.id)
    assert {_ref(i) for i in before["data"]} == {"B-5", "D-4"} and before["total"] == 2
    key = await _key(client, shop, "D-4")

    r = await _decide(client, shop, key, "explained", note="Owner's transfer from savings; not a sale.")
    assert r.status_code == 201, r.text
    made = r.json()["decision"]
    assert (made["status"], made["prior_status"], made["seq"]) == ("explained", None, 1)

    after = await _items(client, shop.id)
    assert {_ref(i) for i in after["data"]} == {"B-5"} and after["total"] == 1
    [line] = after["checks"]
    assert (line["open"], line["closed"]) == (1, 1)
    closed = await _items(client, shop.id, merchant=shop.id, state="closed")
    assert [(_ref(i), i["decision"]["note"]) for i in closed["data"]] == [
        ("D-4", "Owner's transfer from savings; not a sale.")]
    assert closed["total"] == 1


async def test_a_closed_item_stays_closed_however_many_runs_find_it_again(api):
    client, store, shop, _, _, _ = api
    key = await _key(client, shop, "D-4")
    assert (await _decide(client, shop, key, "resolved", note="Bank reversed it on the 12th.")).status_code == 201
    for _ in range(2):
        rec = await store.get_run(shop.id, await _run(client, shop))
        assert rec.summary["open"]["closed"] == 1 and rec.summary["open"]["found"] == 1
        assert key in {r.get("key") for r in rec.rows}  # the rules still find it
        body = await _items(client, shop.id)
        assert {_ref(i) for i in body["data"]} == {"B-5"} and body["total"] == 1
        assert (await _items(client, shop.id, merchant=shop.id))["total"] == 1


async def test_opening_an_item_again_puts_it_back_and_its_history_says_why_each_time(api):
    client, _, shop, _, _, _ = api
    key = await _key(client, shop, "D-4")
    await _decide(client, shop, key, "explained", note="Looks like a refund.")
    await _run(client, shop)  # stored with the item closed
    r = await _decide(client, shop, key, "open", note="No refund matches it; Ama to look again.")
    assert r.status_code == 201 and r.json()["decision"]["prior_status"] == "explained"

    # Back in both lists at once, not after the next run.
    for listed in (await _items(client, shop.id), await _items(client, shop.id, merchant=shop.id)):
        assert {_ref(i) for i in listed["data"]} == {"B-5", "D-4"} and listed["checks"][0]["open"] == 2
        [back] = [i for i in listed["data"] if i["key"] == key]
        assert back["decision"]["status"] == "open"

    history = await _history(client, shop, key)
    assert history["status"] == "open"
    assert [(d["seq"], d["status"], d["prior_status"], d["note"]) for d in history["data"]] == [
        (2, "open", "explained", "No refund matches it; Ama to look again."),
        (1, "explained", None, "Looks like a refund."),
    ]


async def test_assigning_keeps_an_item_open_and_says_whom_it_is_given_to(api):
    client, _, shop, _, _, _ = api
    key = await _key(client, shop, "B-5")
    r = await _decide(client, shop, key, "assigned", owner="Ama (store 12 manager)")
    assert r.status_code == 201
    for listed in (await _items(client, shop.id), await _items(client, shop.id, merchant=shop.id)):
        [item] = [i for i in listed["data"] if i["key"] == key]
        assert item["decision"]["owner"] == "Ama (store 12 manager)" and listed["total"] == 2


async def test_each_decision_says_which_account_and_credential_made_it(api, monkeypatch):
    from okwan_vault.accounts import SESSION_PREFIX, new_token
    client, store, shop, root, key, root_key = api
    b5, d4 = await _key(client, shop, "B-5"), await _key(client, shop, "D-4")

    by_key = (await _decide(client, shop, b5, "assigned", owner="Ama")).json()["decision"]["by"]
    assert by_key == {"tenant_id": shop.id, "name": "Store 12", "credential": key[:12]}

    by_platform = (await _decide(client, shop, d4, "explained", note="Platform fee, booked monthly.",
                                 headers={"Authorization": f"Bearer {root_key}"})).json()["decision"]["by"]
    assert (by_platform["tenant_id"], by_platform["name"]) == (root.id, "Dealer group")

    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(shop.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    r = await _decide(client, shop, b5, "explained", note="Batch was voided at close.",
                      headers={"Authorization": f"Bearer {full}"})
    assert r.status_code == 201 and r.json()["decision"]["by"]["credential"] == "session"
    assert full not in r.text


async def test_a_read_key_the_kind_an_ai_client_is_given_cannot_decide_anything(api):
    client, store, shop, _, _, _ = api
    key = await _key(client, shop, "D-4")
    read_key, _ = await store.issue_key(shop.id, scope="read")
    as_reader = {"Authorization": f"Bearer {read_key}"}
    r = await _decide(client, shop, key, "explained", note="An assistant decided this.", headers=as_reader)
    assert r.status_code == 403
    assert (await client.get(f"/v1/tenants/{shop.id}/exceptions/pair/batches_deposits/{key}/decisions",
                             headers=as_reader)).status_code == 403
    assert (await _history(client, shop, key))["data"] == []
    assert (await _items(client, shop.id))["total"] == 2


async def test_a_reason_is_required_to_close_or_reopen_and_a_name_to_assign(api):
    client, _, shop, _, _, _ = api
    key = await _key(client, shop, "D-4")
    for status, extra in (("explained", {}), ("resolved", {"note": "   "}), ("open", {}),
                          ("assigned", {"note": "someone should"})):
        r = await _decide(client, shop, key, status, **extra)
        assert r.status_code == 422, (status, r.text)
    r = await client.post(f"/v1/tenants/{shop.id}/exceptions/pair/batches_deposits/{key}/decisions",
                          json={"status": "closed", "note": "x"}, headers={"Idempotency-Key": "k-bad-status"})
    assert r.status_code == 422
    r = await client.post(f"/v1/tenants/{shop.id}/exceptions/pair/batches_deposits/{key}/decisions",
                          json={"status": "explained", "note": "x" * 1001}, headers={"Idempotency-Key": "k-long"})
    assert r.status_code == 422
    assert (await _history(client, shop, key))["data"] == []


async def test_text_that_reads_differently_from_what_it_shows_is_refused(api):
    """A note is read by an accountant, a mail and an assistant: a mark that
    reverses the line, a zero-width space or a control character would make
    it say something other than what it shows."""
    client, _, shop, _, _, _ = api
    key = await _key(client, shop, "D-4")
    for sneaky in (chr(0x202E), chr(0x200B), chr(0x2066), chr(0x07), chr(0xFEFF), chr(0x00AD), chr(0x2028),
                   chr(0xE000), chr(0x034F), chr(0xFE0F), chr(0x2800), chr(0x0378)):
        r = await _decide(client, shop, key, "explained", note=f"Refund{sneaky} 1200")
        assert r.status_code == 422, hex(ord(sneaky))
    assert (await _decide(client, shop, key, "assigned", owner="Ama\nand Kofi")).status_code == 422
    ok = await _decide(client, shop, key, "explained", note="Café #1002: refunded\nat the till — see slip.")
    assert ok.status_code == 201 and ok.json()["decision"]["note"] == "Café #1002: refunded\nat the till — see slip."


async def test_a_tab_or_a_space_pasted_from_a_sheet_becomes_a_plain_space(api):
    client, _, shop, _, _, _ = api
    key = await _key(client, shop, "B-5")
    r = await _decide(client, shop, key, "assigned", owner=f"Ama{chr(0x00A0)}Mensah", note=f"Batch\t{chr(0x202F)}B-5")
    assert r.status_code == 201, r.text
    assert (r.json()["decision"]["owner"], r.json()["decision"]["note"]) == ("Ama Mensah", "Batch  B-5")


async def test_a_retry_with_the_same_idempotency_key_makes_one_decision(api):
    client, _, shop, _, _, _ = api
    key = await _key(client, shop, "D-4")
    first = await _decide(client, shop, key, "explained", note="Fee.", idem="retry-1")
    again = await _decide(client, shop, key, "explained", note="Fee.", idem="retry-1")
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["replayed"] is True and again.json()["decision"]["id"] == first.json()["decision"]["id"]
    other = await _decide(client, shop, key, "resolved", note="Something else.", idem="retry-1")
    assert other.status_code == 409
    assert len((await _history(client, shop, key))["data"]) == 1
    for bad in (None, "", "x" * 129, "has space", "semi;colon"):
        h = {} if bad is None else {"Idempotency-Key": bad}
        r = await client.post(f"/v1/tenants/{shop.id}/exceptions/pair/batches_deposits/{key}/decisions",
                              json={"status": "resolved", "note": "x"}, headers=h)
        assert r.status_code == 422, (bad, r.text)


async def test_a_retry_is_answered_even_after_a_new_run_no_longer_finds_the_item(api):
    """Retrying the same request returns the decision it made, as a retry
    must, whatever has run since."""
    client, store, shop, _, _, _ = api
    key = await _key(client, shop, "D-4")
    assert (await _decide(client, shop, key, "explained", note="Fee.", idem="late-retry")).status_code == 201
    for run in await store.list_runs(shop.id, limit=50):
        del store._runs[run.id]
    again = await _decide(client, shop, key, "explained", note="Fee.", idem="late-retry")
    assert again.status_code == 200 and again.json()["replayed"] is True


async def test_only_a_finding_of_the_checks_newest_result_can_be_decided(api):
    client, _, shop, _, _, _ = api
    assert (await _decide(client, shop, "0" * 24, "explained", note="x")).status_code == 404
    key = await _key(client, shop, "D-4")
    assert (await _decide(client, shop, key, "explained", note="x", check="no_such_check")).status_code == 404
    assert (await _decide(client, shop, key, "explained", note="x", kind="sql")).status_code == 404
    # A clean match has no key and is not an item.
    assert (await _decide(client, shop, "matched", "explained", note="x")).status_code == 404


async def test_a_platform_decides_for_its_merchant_and_no_one_else_can(api):
    client, store, shop, root, _, root_key = api
    key = await _key(client, shop, "D-4")
    rival = await store.create_tenant("Rival group")
    rival_key, _ = await store.issue_key(rival.id, scope="admin")
    r = await _decide(client, shop, key, "explained", note="Rival closing it.",
                      headers={"Authorization": f"Bearer {rival_key}"})
    assert r.status_code == 404 and "Store 12" not in r.text
    # The store cannot decide for the group above it.
    up = await _decide(client, shop, key, "explained", note="x", tenant=root)
    assert up.status_code == 404
    mine = await _decide(client, shop, key, "explained", note="Platform fee.",
                         headers={"Authorization": f"Bearer {root_key}"})
    assert mine.status_code == 201
    listed = await client.get(f"/v1/tenants/{root.id}/exceptions", headers={"Authorization": f"Bearer {root_key}"})
    assert {_ref(i) for i in listed.json()["data"]} == {"B-5"}


async def test_decisions_on_a_check_that_reads_files_go_with_the_merchants_files(api):
    """A note may quote a batch or a deposit: it goes when the files do, as
    the stored result that quotes them does."""
    client, store, shop, _, _, _ = api
    key = await _key(client, shop, "D-4")
    await _decide(client, shop, key, "explained", note="Deposit D-4 is the owner's transfer.")
    [first, *_] = await store.list_imports(shop.id)
    assert (await client.delete(f"/v1/tenants/{shop.id}/imports/{first.id}")).status_code == 204
    assert (await _history(client, shop, key))["data"] == []
    assert store._decisions == []


async def test_closed_items_are_listed_one_merchant_at_a_time(api):
    client, _, shop, _, _, _ = api
    r = await client.get(f"/v1/tenants/{shop.id}/exceptions", params={"state": "closed"})
    assert r.status_code == 422


async def test_the_account_list_and_the_merchant_list_agree_after_any_mix_of_decisions(api):
    """The account's list counts from stored summaries and the decisions
    made since; the merchant's reads the rows. They must say the same."""
    client, _, shop, _, _, _ = api
    b5, d4 = await _key(client, shop, "B-5"), await _key(client, shop, "D-4")
    steps = [(d4, "explained", "fee"), (None, None, None), (d4, "open", "not a fee"), (b5, "resolved", "paid late"),
             (None, None, None), (b5, "open", "bounced"), (d4, "resolved", "reversed"), (b5, "assigned", None)]
    for key, status, note in steps:
        if key is None:
            await _run(client, shop)
            continue
        r = await _decide(client, shop, key, status, note=note, owner="Ama" if status == "assigned" else None)
        assert r.status_code == 201, r.text
        account = await _items(client, shop.id)
        merchant = await _items(client, shop.id, merchant=shop.id)
        assert account["total"] == merchant["total"]
        assert account["checks"][0]["closed"] == merchant["checks"][0]["closed"]
        assert {i["key"] for i in account["data"]} == {i["key"] for i in merchant["data"]}
    assert {_ref(i) for i in (await _items(client, shop.id))["data"]} == {"B-5"}


# ── the store, on both stores ───────────────────────────────────────

@pytest.fixture(params=["memory", "postgres"])
async def both(request):
    import os

    from okwan_vault import EnvMasterKey, MemoryStore, new_key
    if request.param == "memory":
        yield MemoryStore(EnvMasterKey(new_key()))
        return
    dsn = os.environ.get("OKWAN_VAULT_DATABASE_URL")
    if not dsn:
        pytest.skip("no vault database configured")
    from okwan_vault.postgres import PostgresStore
    s = await PostgresStore(dsn, EnvMasterKey(new_key())).connect()
    made: list[str] = []
    create = s.create_tenant

    async def tracked(name, parent_id=None):
        t = await create(name, parent_id=parent_id)
        made.append(t.id)
        return t

    s.create_tenant = tracked
    try:
        yield s
    finally:
        for tid in reversed(made):
            await s.pool.execute("DELETE FROM tenants WHERE id = $1", tid)
        await s.close()


async def _stored_run(store, tenant_id, *, reads_files=False):
    return await store.add_run(tenant_id, kind="pair", name="batches_deposits", surface="rest", status="ok",
                               started_at=T0, finished_at=T0, summary={}, rows=[], error=None,
                               reads_files=reads_files)


def _args(tenant_id, run_id, key="k1", status="assigned", idem="i1", **over):
    base = {"kind": "pair", "name": "batches_deposits", "key": key, "status": status, "owner": "Ama",
            "note": None, "actor_tenant_id": tenant_id, "actor_name": "Store", "credential": "session",
            "run_id": run_id, "reads_files": False, "idempotency_key": idem, "request_hash": f"h-{idem}",
            "at": T0}
    base.update(over)
    return base


async def test_an_items_decisions_are_numbered_in_order_each_knowing_the_status_before(both):
    t = await both.create_tenant("Store")
    run = await _stored_run(both, t.id)
    for n, status in enumerate(("assigned", "explained", "open"), 1):
        d, again = await both.add_decision(t.id, **_args(t.id, run.id, status=status, idem=f"i{n}"))
        assert (d.seq, again) == (n, False)
    history = await both.decision_history(t.id, "pair", "batches_deposits", "k1")
    assert [(d.seq, d.status, d.prior_status) for d in history] == [
        (3, "open", "explained"), (2, "explained", "assigned"), (1, "assigned", None)]
    current = await both.current_decisions([(t.id, "k1"), (t.id, "k-none")])
    assert [d.seq for d in current.values()] == [3]
    assert [(d.seq, first) for d, first in await both.decided_on_runs([t.id], [run.id])] == [(3, False)]


async def test_an_idempotency_key_makes_one_decision_and_refuses_another(both):
    from okwan_vault import DecisionConflict
    t = await both.create_tenant("Store")
    run = await _stored_run(both, t.id)
    first, _ = await both.add_decision(t.id, **_args(t.id, run.id))
    again, replayed = await both.add_decision(t.id, **_args(t.id, run.id))
    assert replayed and again.id == first.id
    with pytest.raises(DecisionConflict):
        await both.add_decision(t.id, **_args(t.id, run.id, request_hash="other"))
    assert (await both.decision_by_idempotency(t.id, "i1")).id == first.id


async def test_a_decision_against_a_run_that_is_gone_is_not_stored(both):
    from okwan_vault import ItemGone
    t = await both.create_tenant("Store")
    with pytest.raises(ItemGone):
        await both.add_decision(t.id, **_args(t.id, "run_gone"))
    other = await both.create_tenant("Other")
    theirs = await _stored_run(both, other.id)
    with pytest.raises(ItemGone):  # another tenant's run is not this tenant's
        await both.add_decision(t.id, **_args(t.id, theirs.id))
    assert await both.decision_history(t.id, "pair", "batches_deposits", "k1") == []


async def test_a_tenant_reads_only_its_own_decisions(both):
    a, b = await both.create_tenant("A"), await both.create_tenant("B")
    ra, rb = await _stored_run(both, a.id), await _stored_run(both, b.id)
    await both.add_decision(a.id, **_args(a.id, ra.id, key="ka"))
    await both.add_decision(b.id, **_args(b.id, rb.id, key="kb"))
    assert set(await both.current_decisions([(a.id, "ka"), (a.id, "kb")])) == {(a.id, "pair", "batches_deposits", "ka")}
    assert await both.decided_on_runs([a.id], [rb.id]) == []
    assert await both.decision_by_idempotency(a.id, "i1") is not None
    assert (await both.decision_by_idempotency(a.id, "i1")).tenant_id == a.id


async def test_decisions_made_at_once_on_one_item_get_one_number_each(both):
    """Sent at once, one is recorded at a time: one that finds the item busy
    is refused at once, never kept waiting, and sent again, as a client
    does with its same Idempotency-Key."""
    from okwan_vault import ItemBusy
    t = await both.create_tenant("Store")
    run = await _stored_run(both, t.id)
    statuses = ["explained", "open"] * 10

    async def send(n, status):
        while True:
            try:
                return await both.add_decision(t.id, **_args(t.id, run.id, status=status, idem=f"c{n}"))
            except ItemBusy:
                await asyncio.sleep(0.01)

    made = await asyncio.gather(*(send(n, s) for n, s in enumerate(statuses)))
    history = sorted(await both.decision_history(t.id, "pair", "batches_deposits", "k1"), key=lambda d: d.seq)
    assert [d.seq for d in history] == list(range(1, 21)) and len(made) == 20
    assert all(later.prior_status == earlier.status for earlier, later in pairwise(history))
    assert history[0].prior_status is None


async def test_a_file_deleted_takes_the_decisions_of_checks_that_read_files(both):
    t = await both.create_tenant("Store")
    f = await both.add_import(
        t.id, kind="bank_deposits", filename="s.csv", format="csv", layout="custom", mapping={},
        sha256="sha-s", rows=[(2, T0.date(), {"fitid": "D-4"})], skipped=[], card_columns=[],
        first_date=T0.date(), last_date=T0.date())
    files_run = await _stored_run(both, t.id, reads_files=True)
    rails = await both.add_run(t.id, kind="across", name="rails", surface="rest", status="ok", started_at=T0,
                               finished_at=T0, summary={}, rows=[], error=None)
    await both.add_decision(t.id, **_args(t.id, files_run.id, reads_files=True))
    await both.add_decision(t.id, **_args(t.id, rails.id, kind="across", name="rails", key="k2", idem="i2"))
    assert await both.delete_import(t.id, f.id)
    assert await both.decision_history(t.id, "pair", "batches_deposits", "k1") == []
    assert [d.key for d in (await both.current_decisions([(t.id, "k1"), (t.id, "k2")])).values()] == ["k2"]
