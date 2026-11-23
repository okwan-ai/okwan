"""What an attacker's pass found in the open-items mail, turned around.

The daily mail made from the open items (okwan_api.schedule, the exception
queue's step 4) was attacked before it was merged, by a fresh agent with an
attacker's brief and none of the author's conclusions (docs/audit/red-team.md,
2026-11-23). It proved nine faults, each with an exploit that ran. Eight are
closed here, each exploit kept below, turned around: a test passes only when
the attack fails. The ninth, an item that falls past a stored result's
1,000-row cut and comes back being first seen again, is answered as G29.

1. The mail's read held every row of every merchant at once and stalled
   the event loop for each chunk: one account could grow the API's memory
   without bound.
2. An account with nothing new cost seconds of database time every hour.
3. Twenty-five busier merchants kept one merchant's news out of every mail.
4. A large open item was named only when its own merchant had news.
5. The subject counted only the merchants the mail named.
7. What the mail kept outlived the items it was about, gave their
   references back, and kept an item that opened again from being news.
8. Names that draw as nothing, and "tel:"/"mailto:" addresses, reached the
   mail.
9. Accounts that had turned daily checks off, under Settings that held the
   mail switch off with them, began to be mailed.
"""
from __future__ import annotations

import asyncio
import gc
import os
import re
import time
import uuid
from datetime import timedelta

import pytest
from okwan_api import schedule
from okwan_recon.exceptions import carry
from okwan_vault import ADMIN, EnvMasterKey, PostgresStore, new_key

from tests.test_daily_check_attacks import (  # noqa: F401  (fixtures)
    NOW,
    client,
    make_world,
    week,
    world,
)

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
FILE_ONLY = ("batches_deposits", "delivery_payout_lines", "delivery_payouts_deposits")


def _rss_mb() -> int:
    with open("/proc/self/status") as f:
        return int(next(line for line in f if line.startswith("VmRSS")).split()[1]) // 1024


def _unmatched(tid: str, name: str, n: int) -> list[dict]:
    """What record_run stores for a file check whose batches have no
    deposit, references at the importer's 200-character limit."""
    out = [{"status": "unmatched_left", "rule": None, "confidence": None, "discrepancy": None,
            "explained_by": None, "reason": "no record on the other side matched", "candidates": None,
            "members": None,
            "evidence": {"record": {"amount_minor": 100_000 + i, "currency": "USD", "when": "2026-11-09"}},
            "left_ref": (f"B-{i:06d}-" + "x" * 200)[:200], "right_ref": None} for i in range(n)]
    return carry(tid, name, "pair", out, None, NOW)


# ── 1 and 2. the mail's read is bounded, a few merchants at a time ───

@pytest.mark.skipif(not DSN, reason="no vault database configured")
async def test_the_mails_read_holds_one_merchant_at_a_time_and_never_the_event_loop(monkeypatch):
    """The exploit: 60 merchants with three file checks of 1,000 findings
    each held 180,000 items at once (+330 MB) and stalled the event loop
    0.3 to 0.4 s a chunk. Now a look reads a few merchants at a time, works
    each one's items out off the loop, keeps figures only, and reads no
    more than LOOK_ITEMS before it stops; the next look goes on from there."""
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    w = make_world(monkeypatch, store)
    root = await w.account("Exhaust ISV", f"owner-{uuid.uuid4().hex[:8]}@exhaust.test")
    monkeypatch.setattr(schedule, "LOOK_ITEMS", 60_000, raising=False)
    try:
        await store.set_notify(root.id, daily_checks=False)
        for m in range(60):
            t = await store.create_tenant(f"Store {m:03d}", parent_id=root.id)
            for name in FILE_ONLY:
                await store.add_run(t.id, kind="pair", name=name, surface="rest", status="ok",
                                    started_at=NOW, finished_at=NOW, summary={"unmatched_left": 1000},
                                    rows=_unmatched(t.id, name, 1000), error=None, reads_files=False)
        gaps: list[float] = []
        peak = [0]
        done = False

        async def ticker():
            last = time.perf_counter()
            while not done:
                await asyncio.sleep(0.005)
                now = time.perf_counter()
                gaps.append(now - last)
                peak[0] = max(peak[0], _rss_mb())
                last = now

        gc.collect()
        before = _rss_mb()
        tick = asyncio.create_task(ticker())
        r = await schedule.run_due(NOW)
        done = True
        await tick
        assert r.mailed == 1
        assert max(gaps) < 0.2, f"the event loop waited {max(gaps):.3f}s"
        assert peak[0] - before < 120, f"memory grew {peak[0] - before} MB"
        state = schedule._looks[root.id]
        assert 0 < state.cursor < 61, "the look stopped at its budget, and the next goes on from there"
        [mail] = w.outbox.sent
        assert mail["subject"].startswith("Okwan: ") and "across" in mail["subject"]
    finally:
        await store.pool.execute("DELETE FROM tenants WHERE parent_id = $1", root.id)
        await store.pool.execute("DELETE FROM tenants WHERE id = $1", root.id)
        await store.close()


@pytest.mark.skipif(not DSN, reason="no vault database configured")
async def test_a_quiet_account_is_looked_at_in_small_reads_and_not_every_hour(monkeypatch):
    """The exploit: 20,000 merchants, 5,000 with a stored result, nothing new:
    one statement over every merchant cost 5.8 s of database time each hour.
    Now every read names a few merchants, and a look that read them all and
    found nothing is not made again for hours."""
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    w = make_world(monkeypatch, store)
    root = await w.account("Wide ISV", f"owner-{uuid.uuid4().hex[:8]}@wide.test")
    try:
        await store.set_notify(root.id, daily_checks=False)
        ids = [f"ten_{uuid.uuid4().hex[:16]}" for _ in range(3000)]
        await store.pool.executemany("INSERT INTO tenants (id, name, parent_id) VALUES ($1, 'Store', $2)",
                                     [(i, root.id) for i in ids])
        await store.pool.executemany(
            "INSERT INTO reconciliation_runs (id, tenant_id, kind, name, surface, status, started_at, finished_at, "
            "summary, rows) VALUES ($1, $2, 'pair', 'batches_deposits', 'rest', 'ok', $3, $3, '{}', '[]')",
            [(f"run_{uuid.uuid4().hex[:16]}", i, NOW) for i in ids[:1000]])
        widest: list[int] = []
        mine = {root.id, *ids}
        for method in ("newest_runs_all", "mail_told", "daily_checks"):
            real = getattr(store, method)

            def wrap(real=real):
                async def seen(tids, **kw):
                    if mine & set(tids):                  # this account's, not another test's
                        widest.append(len(tids))
                    return await real(tids, **kw)
                return seen

            monkeypatch.setattr(store, method, wrap())
        hours = [await schedule.run_due(NOW + timedelta(hours=h, minutes=1)) for h in range(3)]
        assert w.outbox.sent == [] and all(r.mailed == 0 for r in hours)
        assert max(widest) <= max(schedule.READ_CHUNK, getattr(schedule, "STATE_CHUNK", 0)), "no read names every merchant"
        looks = len(widest)
        await schedule.run_due(NOW + timedelta(hours=3, minutes=1))
        assert len(widest) == looks, "the account was read every merchant through, and is quiet for hours"
    finally:
        await store.pool.execute("DELETE FROM tenants WHERE parent_id = $1", root.id)
        await store.pool.execute("DELETE FROM tenants WHERE id = $1", root.id)
        await store.close()


# ── 3. every merchant's news has its turn ────────────────────────────

CLEAN = ("#2043", "#2046", "#2047")


def _busy(day: int) -> dict:
    rows = week(without=CLEAN)
    for d in range(day + 1):
        for n in range(2):
            rows["shopify"].append({**rows["shopify"][0], "id": 900000 + 10 * d + n, "name": f"#9{d:03d}{n}"})
    return rows


async def test_busier_merchants_cannot_keep_one_merchants_news_out_of_the_mail(world, client):  # noqa: F811
    """The exploit: a merchant's admin key made 25 sub-merchants with two new
    items a day, and a sibling's item was never mailed in ten days. Now the
    merchants told longest ago go first: the sibling is named by the next
    day's mail at the latest."""
    victim = await world.merchant("Victim Store", rows=week(without=("#2043", "#2046")), parent=world.root)
    platform = await world.store.create_tenant("Platform X", parent_id=world.root.id)
    key, _ = await world.store.issue_key(platform.id, scope=ADMIN)
    subs = []
    for i in range(schedule.MAIL_MERCHANTS):
        made = await client.post("/v1/tenants", headers={"Authorization": f"Bearer {key}"}, json={"name": f"Sub {i:02d}"})
        sub = await world.store.get_tenant(made.json()["id"])
        await world.connect(sub.id, _busy(0))
        subs.append(sub)
    for day in range(2):
        now = NOW + timedelta(days=day, minutes=day)
        for sub in subs:
            world.set_rows(sub, _busy(day))
        world.clock(now)
        assert (await schedule.run_due(now)).mailed == 1
    assert any("Victim Store\n  New: 1 item" in m["text"] for m in world.outbox.sent)
    assert (await world.store.mail_told([victim.id]))[victim.id]["through"]


# ── 4 and 5. what the mail says is what was promised ────────────────

async def test_a_large_open_item_is_named_in_a_mail_sent_for_another_merchant(world):  # noqa: F811
    await world.store.set_notify(world.root.id, large_amount=300)
    await world.merchant("Big Store", rows=week(without=("#2047",)), parent=world.root)
    world.clock(NOW)
    await schedule.run_due(NOW)
    await world.merchant("New Store", rows=week(without=("#2043",)), parent=world.root)
    day = NOW + timedelta(days=1, minutes=1)
    world.clock(day)
    assert (await schedule.run_due(day)).mailed == 1
    second = world.outbox.sent[1]["text"]
    assert "New Store\n  New:" in second
    assert "Still open at 300 or more of their currency:\n  Big Store: 1 item, $312.00 at stake\n" in second
    assert "Big Store\n  New:" not in second, "named as large, not as news"


async def test_the_subject_counts_every_merchants_news(world):  # noqa: F811
    for i in range(schedule.MAIL_MERCHANTS + 1):
        await world.merchant(f"Store {i:02d}", parent=world.root)
    world.clock(NOW)
    assert (await schedule.run_due(NOW)).mailed == 1
    [mail] = world.outbox.sent
    assert mail["subject"] == "Okwan: 78 new open items across 26 merchants"
    assert mail["text"].count("\n  New: 3 items") == schedule.MAIL_MERCHANTS
    assert "1 more merchant has something new" in mail["text"]


# ── 7. what the mail keeps is times, and an item back is news ───────

def _batch(ref: str, amount: int, when: str) -> dict:
    return {"status": "unmatched_left", "left_ref": ref, "right_ref": None,
            "evidence": {"record": {"amount_minor": amount, "currency": "USD", "when": when}}}


async def _file_run(world, tenant, rows, at):  # noqa: F811
    """A stored result of the batches check, carried from the one before as
    record_run carries it."""
    before = (await world.store.newest_runs([tenant.id], "pair", "batches_deposits", ok_only=True,
                                            with_rows=True)).get(tenant.id)
    return await world.store.add_run(
        tenant.id, kind="pair", name="batches_deposits", surface="dashboard", status="ok",
        started_at=at, finished_at=at, summary={"unmatched_left": len(rows)},
        rows=carry(tenant.id, "batches_deposits", "pair", rows, before.rows if before else None, at),
        error=None, reads_files=True)


async def test_what_the_mail_kept_gives_back_no_item(world):  # noqa: F811
    """The exploit: an item key kept for the mail, after its file and run
    were gone, gave back the batch and its day by counting through refs.
    Now the mail keeps when it named the merchant and through which result
    of each check: no item, key or reference to count against."""
    shop = await world.store.create_tenant("File Shop", parent_id=world.root.id)
    await _file_run(world, shop, [_batch("B-517", 3884, "2026-11-09")], NOW)
    world.clock(NOW)
    assert (await schedule.run_due(NOW)).mailed == 1
    told = (await world.store.mail_told([shop.id]))[shop.id]
    assert set(told) == {"at", "through"} and set(told["through"]) == {"pair/batches_deposits"}
    kept = repr(told)
    assert not re.search(r"[0-9a-f]{20}", kept) and "B-517" not in kept, "times and a check's name only"


async def test_an_item_that_opened_again_while_the_mail_was_off_is_news(world):  # noqa: F811
    """The exploit: an item mailed, its file deleted, uploaded again while
    the mail was off: open again, and never mailed. It is first seen again
    when it comes back, and that is after the mail last told of the check."""
    shop = await world.store.create_tenant("File Shop", parent_id=world.root.id)
    await _file_run(world, shop, [_batch("B-517", 3884, "2026-11-09")], NOW)
    world.clock(NOW)
    assert (await schedule.run_due(NOW)).mailed == 1
    await world.store.set_notify(world.root.id, finding_emails=False)
    await _file_run(world, shop, [], NOW + timedelta(days=1))  # the batch is gone from the files
    back = NOW + timedelta(days=2)
    await _file_run(world, shop, [_batch("B-517", 3884, "2026-11-09")], back)
    await world.store.set_notify(world.root.id, finding_emails=True)
    later = NOW + timedelta(days=3)
    world.clock(later)
    assert (await schedule.run_due(later)).mailed == 1
    assert "File Shop\n  New: 1 item, $38.84 at stake" in world.outbox.sent[1]["text"]


# ── 8. a name is visible words, and no address to press ─────────────

DRAWN_AS_NOTHING = ["ㅤ", "⠀⠀⠀", "ᅟᅠ", "ﾠ", "Kofi͏️"]


@pytest.mark.parametrize("name", DRAWN_AS_NOTHING)
async def test_a_name_drawn_as_nothing_is_refused_and_never_reaches_the_mail(world, client, name):  # noqa: F811
    top = await world.store.create_tenant("Merchant with an admin key", parent_id=world.root.id)
    key, _ = await world.store.issue_key(top.id, scope=ADMIN)
    made = await client.post("/v1/tenants", headers={"Authorization": f"Bearer {key}"}, json={"name": name})
    assert made.status_code == 422, made.text
    # One stored before the rule reaches the mail as visible words only.
    shown = schedule.shown(name)
    assert all(not schedule._unseen(c) for c in shown) and shown in ("(no name)", "Kofi")


async def test_an_emoji_drawn_as_one_is_still_a_name(world, client):  # noqa: F811
    auth = await world.session_for(world.root)
    made = await client.post("/v1/tenants", headers=auth, json={"name": "Café ☕️ Bar"})
    assert made.status_code == 201 and made.json()["name"] == "Café ☕️ Bar"


def test_an_address_scheme_without_slashes_is_broken_in_the_mail():
    said = schedule.shown("Refunds: tel:+18005550199 mailto:refunds@okwan-help")
    assert not re.search(r"(?i)\b(tel|mailto|sms):\S", said)
    assert said == "Refunds: tel: +18005550199 mailto: refunds@okwan-help"
    assert schedule.shown("sms:+1800") == "sms: +1800"
    assert schedule.shown("10:30 Store") == "10:30 Store", "a time is not a scheme"
    assert schedule.shown("evil․com and evil﹒com and shop.͏com") == "evil. com and evil. com and shop. com"


# ── 9. the mail stays off for an account that never chose it alone ──

@pytest.mark.skipif(not DSN, reason="no vault database configured")
async def test_an_account_that_had_daily_checks_off_keeps_its_mail_off():
    """Before this release Settings held the mail's switch off while daily
    checks were off, so such an account never chose the mail by itself.
    The release that makes the mail its own choice turns it off for them,
    once, and leaves every other account as it chose."""
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    off = await store.create_tenant(f"Checks off {uuid.uuid4().hex[:6]}")
    on = await store.create_tenant(f"Checks on {uuid.uuid4().hex[:6]}")
    try:
        await store.set_notify(off.id, daily_checks=False)
        await store.set_notify(on.id, daily_checks=True)
        # The database as the release before left it.
        await store.pool.execute("ALTER TABLE notify_settings DROP COLUMN aging_days, DROP COLUMN large_amount")
        await store._migrate()
        assert (await store.get_notify(off.id))["finding_emails"] is False
        kept = await store.get_notify(on.id)
        assert (kept["finding_emails"], kept["aging_days"], kept["large_amount"]) == (True, 7, 1000)
        await store.set_notify(off.id, finding_emails=True)          # the account's own choice, later
        await store._migrate()
        assert (await store.get_notify(off.id))["finding_emails"] is True, "once, never again"
    finally:
        for t in (off, on):
            await store.pool.execute("DELETE FROM tenants WHERE id = $1", t.id)
        await store.close()


# ── and what the pass left unproven, closed anyway ───────────────────

async def test_a_merchant_deleted_while_its_mail_goes_does_not_lose_the_others_record(world, monkeypatch):  # noqa: F811
    a = await world.merchant("A Store", parent=world.root)
    b = await world.merchant("B Store", parent=world.root)
    real = world.store.set_mail_told

    async def gone_first(tid, record):
        if tid == min(a.id, b.id):
            raise KeyError(tid)
        return await real(tid, record)

    monkeypatch.setattr(world.store, "set_mail_told", gone_first)
    world.clock(NOW)
    assert (await schedule.run_due(NOW)).mailed == 1
    assert max(a.id, b.id) in await world.store.mail_told([a.id, b.id])


@pytest.mark.skipif(not DSN, reason="no vault database configured")
async def test_the_last_releases_table_is_kept_and_the_first_form_of_this_one_mended():
    """A release still serving while this one starts, or one rolled back
    to, reads `daily_checks` as it knows it: its old columns stay, emptied.
    A database a build of this change ran on (item keys in `mail_told`, the
    old columns dropped) is brought to the same shape."""
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    t = await store.create_tenant(f"Forms {uuid.uuid4().hex[:6]}")
    try:
        await store.claim_daily_check(t.id, NOW)
        await store.pool.execute("ALTER TABLE daily_checks DROP COLUMN told, DROP COLUMN news")
        await store.pool.execute("DROP TABLE mail_told")
        await store.pool.execute("CREATE TABLE mail_told (tenant_id text PRIMARY KEY REFERENCES tenants(id) "
                                 "ON DELETE CASCADE, marks jsonb NOT NULL DEFAULT '[]', updated_at timestamptz)")
        await store._migrate()
        cols = {(r["table_name"], r["column_name"]) for r in await store.pool.fetch(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name IN ('daily_checks', 'mail_told')")}
        assert {("daily_checks", "told"), ("daily_checks", "news"), ("mail_told", "told")} <= cols
        assert ("mail_told", "marks") not in cols
        assert await store.daily_checks([t.id]) and await store.mail_told([t.id]) == {}
        await store.set_mail_told(t.id, {"at": NOW.isoformat(), "through": {}})
        await store.close()
        store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()   # row-level security still holds
        assert (await store.mail_told([t.id]))[t.id]["at"] == NOW.isoformat()
    finally:
        await store.pool.execute("DELETE FROM tenants WHERE id = $1", t.id)
        await store.close()
