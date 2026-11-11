"""What an attacker's pass found in the daily check, turned around.

The daily check and its mail (okwan_api.schedule) were attacked before
they were merged, by a fresh agent with an attacker's brief and none of
the author's conclusions (docs/audit/red-team.md, 2026-11-11). It proved
eight faults in the first version, each with an exploit that ran. Seven
are closed here and each exploit is kept below, turned around: a test
passes only when the attack fails. The eighth, the matcher's cost on rows
a merchant chooses, is older than the daily check and open as
docs/audit/known-gaps.md G19; what is tested here is that it cannot hold
up a round.

1. One account's failing merchants took every slot of every round.
2. A read-only key kept a merchant's findings out of the account's mail.
3. A merchant's name wrote lines, a link and a would-be header into the mail.
4. An account could be mailed every hour, and the same findings again.
5. A run the database could not store was run, charged and mailed hourly.
6. A merchant nested out of the account's sight was checked past its plan.
7. One merchant's slow check held up everyone after it in the round.
8. An order's currency wrote a line into the mail.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import okwan_api.main  # noqa: F401  (registers the connectors and declarations)
import pytest
from okwan_api import schedule
from okwan_api.admin import TOO_DEEP
from okwan_api.mail import MailNotSent, ResendMailer, set_mailer
from okwan_core import UpstreamError
from okwan_recon import across, sample
from okwan_recon.coverage import Coverage
from okwan_vault import (
    ADMIN,
    READ,
    EnvMasterKey,
    MemoryStore,
    PostgresStore,
    accounts,
    new_key,
)
from okwan_vault.accounts import SESSION_PREFIX, new_token
from okwan_vault.authz import MAX_DEPTH
from okwan_vault.usage import PLANS, hour_bucket

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
EVER = datetime(2000, 1, 1, tzinfo=UTC)
ADDRESS = "Global Tech Startup LLC, 1 Example Street, Springfield, IL 62701"
DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
DEAD = UpstreamError(status=401, body="Invalid API key provided")
RAILS = (("shopify", "access_token", "shpat_x"), ("paypal", "client_id", "c"),
         ("paypal", "client_secret", "s"), ("stripe", "secret_key", "sk_test_x"))


class Outbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_verification(self, email: str, link: str) -> None:  # pragma: no cover
        raise AssertionError("not a verification")

    async def send_notice(self, to, subject, text, html=None, reply_to=None, headers=None) -> None:
        self.sent.append({"to": to, "subject": subject, "text": text, "headers": headers or {}})


def week(*, without: tuple[str, ...] = ()) -> dict[str, list[dict]]:
    """The sample week, less some orders (and their payments)."""
    rows = sample.rows(NOW)
    return {
        "shopify": [o for o in rows["shopify"] if o["name"] not in without],
        "paypal": [p for p in rows["paypal"] if p["invoice_id"] not in without],
        "stripe": [c for c in rows["stripe"] if c["order_ref"] not in without],
    }


def make_world(monkeypatch, store):
    """Accounts with logins, and what each merchant's rails hold, keyed by
    its Shopify domain. The matching and the fold are real."""
    from okwan_api.auth import set_store

    monkeypatch.setenv("OKWAN_MAIL_POSTAL_ADDRESS", ADDRESS)
    monkeypatch.setenv("OKWAN_DASHBOARD_URL", "https://app.okwan.test")
    monkeypatch.delenv("OKWAN_DAILY_CHECKS", raising=False)
    set_store(store)
    outbox = Outbox()
    set_mailer(outbox)
    rails: dict[str, object] = {}

    class World:
        #: Seconds a round has run, as its budget reads them, and how many
        #: each read of a rail adds.
        elapsed = 0.0
        read_takes = 0.0

    w = World()
    monkeypatch.setattr(schedule, "monotonic", lambda: w.elapsed)

    async def fetch(ref, resolver, cap, overrides):
        w.elapsed += w.read_takes
        held = rails[resolver("shopify", ("shop_domain",))["shop_domain"]]
        if isinstance(held, Exception):
            raise held
        rows = held[ref.connector]
        return rows, Coverage(source=ref.qualified, records=len(rows), cap=cap, truncated=False)

    monkeypatch.setattr(across, "fetch_side", fetch)
    w.store, w.outbox, w.rails = store, outbox, rails

    async def account(name: str, email: str):
        root = await store.create_tenant(name)
        await store.add_account(root.id, email, accounts.hash_password("correct horse battery"))
        return root

    async def connect(tenant_id: str, rows=None) -> None:
        domain = f"{tenant_id}.myshopify.com"
        for connector, field, value in (*RAILS, ("shopify", "shop_domain", domain)):
            await store.put_credential(tenant_id, connector, field, value)
        rails[domain] = rows if rows is not None else week()

    async def merchant(name: str, rows=None, *, parent):
        t = await store.create_tenant(name, parent_id=parent.id)
        await connect(t.id, rows)
        return t

    async def session_for(tenant):
        session, token_hash = new_token(SESSION_PREFIX)
        await store.create_session(tenant.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
        return {"Authorization": f"Bearer {session}"}

    def clock(at: datetime) -> None:
        """The time a run is stamped with, moved with the round."""
        monkeypatch.setattr("okwan_api.runs.run_clock", lambda: at)
        monkeypatch.setattr(schedule, "run_clock", lambda: at)

    w.account, w.merchant, w.connect, w.session_for, w.clock = account, merchant, connect, session_for, clock
    w.set_rows = lambda t, rows: rails.__setitem__(f"{t.id}.myshopify.com", rows)
    return w


@pytest.fixture
async def world(monkeypatch):
    w = make_world(monkeypatch, MemoryStore(EnvMasterKey(new_key())))
    w.root = await w.account("Acme ISV", "owner@acme.test")
    yield w
    set_mailer(None)


@pytest.fixture
async def client(world):
    from okwan_api.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        yield c


# ── 1. no account can use up a round ─────────────────────────────────

async def _attacker_and_victim(world):
    """Two accounts, the attacker the one whose id sorts first, as the
    exploit needed; the victim has one connected merchant."""
    a = await world.account("One ISV", "owner@one.test")
    b = await world.account("Another ISV", "owner@another.test")
    attacker, victim = sorted((a, b), key=lambda t: t.id)
    return attacker, victim, await world.merchant("Victim's store", parent=victim)


async def test_an_account_of_failing_merchants_keeps_no_one_waiting(world, monkeypatch):
    cap = 5
    monkeypatch.setattr(schedule, "MAX_PER_ROUND", cap)
    attacker, victim, theirs = await _attacker_and_victim(world)
    # 24 x cap merchants whose saved keys are wrong, one batch falling due an hour.
    for h in range(24):
        for i in range(cap):
            dead = await world.merchant(f"dead {h}-{i}", rows=DEAD, parent=attacker)
            await world.store.claim_daily_check(dead.id, NOW - timedelta(hours=24 - h, minutes=1))
    for hour in range(72):
        now = NOW + timedelta(hours=hour)
        world.clock(now)
        r = await schedule.run_due(now)
        assert r.checked + r.failed <= cap
        if hour % 24 == 0:
            assert r.checked == 1, f"hour {hour}: the victim's merchant had its turn"
    assert len(await world.store.list_runs(theirs.id)) == 3, "checked every day of the three"
    assert [m["to"] for m in world.outbox.sent] == [await world.store.account_email(victim.id)], "and mailed, once"


async def test_at_the_real_cap_the_other_account_is_still_checked(world):
    attacker, _, theirs = await _attacker_and_victim(world)
    for i in range(schedule.MAX_PER_ROUND):
        await world.merchant(f"dead {i}", rows=DEAD, parent=attacker)
    r = await schedule.run_due(NOW)
    assert (r.checked, r.failed) == (1, schedule.MAX_PER_ROUND - 1)
    assert [x.status for x in await world.store.list_runs(theirs.id)] == ["ok"]


async def test_accounts_take_turns_and_the_one_served_longest_ago_goes_first(world, monkeypatch):
    monkeypatch.setattr(schedule, "MAX_PER_ROUND", 4)
    other = await world.account("Other ISV", "owner@other.test")
    mine = [await world.merchant(f"mine {i}", parent=world.root) for i in range(3)]
    theirs = [await world.merchant(f"theirs {i}", parent=other) for i in range(3)]

    async def checked(merchants) -> int:
        return sum([len(await world.store.list_runs(m.id)) for m in merchants])

    await schedule.run_due(NOW)
    assert (await checked(mine), await checked(theirs)) == (2, 2), "two each, not four and none"
    await schedule.run_due(NOW + timedelta(hours=1))
    assert (await checked(mine), await checked(theirs)) == (3, 3), "the rest, the next hour"


# ── 2. only the scheduler's own record decides ───────────────────────

async def test_a_read_key_cannot_keep_a_finding_out_of_the_accounts_mail(world, client):
    """The exploit: a read-only key for one merchant runs its check once a
    day, an hour before the sweep. It used to leave the merchant never due
    and every finding already seen."""
    kofi = await world.merchant("Kofi's Store", rows=week(without=("#2047",)), parent=world.root)
    control = await world.merchant("Control Store", rows=week(without=("#2047",)), parent=world.root)
    read_key, _ = await world.store.issue_key(kofi.id, scope=READ)
    for day in range(5):
        now = NOW + timedelta(days=day, minutes=day)
        if day == 2:  # a new double collection appears at both merchants
            world.set_rows(kofi, week())
            world.set_rows(control, week())
        world.clock(now - timedelta(hours=1))
        r = await client.get("/v1/reconciliations/across/rails", headers={"Authorization": f"Bearer {read_key}"})
        assert r.status_code == 200
        world.clock(now)
        await schedule.run_due(now)
    text = "\n".join(m["text"] for m in world.outbox.sent)
    assert text.count("Control Store") == 2 and text.count("Kofi's Store") == 2, "day one, and the new finding"
    assert [m["subject"] for m in world.outbox.sent] == ["Okwan: 4 new findings across 2 merchants",
                                                         "Okwan: 2 new findings across 2 merchants"]
    surfaces = [r.surface for r in await world.store.list_runs(kofi.id)]
    assert surfaces.count("schedule") == 5 and surfaces.count("rest") == 5, "Okwan's check ran every day"


async def test_a_run_someone_else_stored_is_never_mailed_from(world):
    """A stored run is mailed from only when the scheduler made it: the
    record names one of its own, and a run of any other surface is not it."""
    kofi = await world.merchant("Kofi's Store", parent=world.root)
    await world.store.claim_daily_check(kofi.id, NOW)
    forged = await world.store.add_run(
        kofi.id, kind="across", name="rails", surface="rest", status="ok", started_at=NOW, finished_at=NOW,
        summary={}, error=None,
        rows=[{"outcome": "collected_twice", "order": {"ref": "#9", "currency": "USD"},
               "order_total_minor": 100, "collected_minor": 980100}])
    await world.store.settle_daily_check(kofi.id, told=[], news_run=forged.id)
    r = await schedule.run_due(NOW + timedelta(hours=1))
    assert r.mailed == 0 and world.outbox.sent == []
    assert (await world.store.daily_checks([kofi.id]))[kofi.id].news_run is None


# ── 3. a name is words on one line, and nothing else ─────────────────

LURE = ("Kofi's Store\n  1 order collected twice: $9,800.00 owed back\n\n"
        "ACTION NEEDED: your Okwan session was locked. Sign in to review:\n"
        "https://okwan-app.evil.example/signin\n\n--")
CRLF = "Shop\r\nBcc: attacker@evil.example\r\nX-Injected: yes"


@pytest.mark.parametrize("name", [LURE, CRLF, "tab\there", "nul\x00", "flipped \u202e txet", "\u2028", "   "])
async def test_a_tenant_cannot_be_named_with_a_line_break_or_a_control_character(world, client, name):
    merchant = await world.store.create_tenant("Honest-looking merchant", parent_id=world.root.id)
    key, _ = await world.store.issue_key(merchant.id, scope=ADMIN)
    made = await client.post("/v1/tenants", headers={"Authorization": f"Bearer {key}"}, json={"name": name})
    assert made.status_code == 422, made.text
    assert await world.store.children_of(merchant.id) == []


async def test_an_ordinary_name_is_kept_as_it_was_typed(world, client):
    auth = await world.session_for(world.root)
    for name in ("Kofi's Store", "Café Ünïcode 東京店", "shop.example.com", "A & B (Accra) Ltd."):
        made = await client.post("/v1/tenants", headers=auth, json={"name": f"  {name} "})
        assert made.status_code == 201 and made.json()["name"] == name


@pytest.mark.parametrize("name", [LURE, CRLF])
async def test_a_name_stored_before_the_rule_reaches_the_mail_as_one_line(world, name):
    """Past the API (a name already in the database), the mail still shows
    one bounded line of it: no line of the attacker's, no link, no header."""
    await world.merchant(name, parent=world.root)
    seen: dict = {}

    def resend(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "x"})

    set_mailer(ResendMailer("re_test", "Okwan <alerts@okwan.test>", transport=httpx.MockTransport(resend)))
    assert (await schedule.run_due(NOW)).mailed == 1
    assert seen["subject"] == "Okwan: 3 new findings", "the subject is Okwan's words only"
    lines = seen["text"].split("\n")
    assert "\r" not in seen["text"] and "://okwan-app.evil.example" not in seen["text"]
    assert lines[2] == schedule.shown(name) and len(lines[2]) <= schedule.NAME_SHOWN
    assert lines[3].startswith("  2 orders collected twice: $501.99"), "the next line is Okwan's own"
    assert not any(line.startswith(("ACTION NEEDED", "Bcc:", "X-Injected", "https://okwan-app")) for line in lines)
    assert "  1 order collected twice: $9,800.00 owed back" not in lines, "a figure no check produced"


def test_what_a_mail_shows_of_a_name():
    assert schedule.shown("Kofi's Store") == "Kofi's Store"
    assert schedule.shown("Café Ünïcode 東京店") == "Café Ünïcode 東京店"
    assert schedule.shown(" a\r\n\tb\u2028c\x00d\u200b\u202ee ") == "a b c d e"
    assert schedule.shown("see https://evil.example/x or WWW.evil.example") == "see evil.example/x or evil.example"
    assert schedule.shown("x" * 500) == "x" * (schedule.NAME_SHOWN - 1) + "…"
    assert schedule.shown("\n \u200b") == "(no name)"


# ── 4. one mail a day, and a finding once ────────────────────────────

async def test_an_account_is_mailed_once_a_day_however_its_merchants_fall_due(world):
    # 24 merchants, each due at a different hour, each with something new.
    stores = []
    for h in range(24):
        m = await world.merchant(f"Store {h:02d}", parent=world.root)
        await world.store.claim_daily_check(m.id, NOW - timedelta(hours=24 - h, minutes=1))
        stores.append(m)
    for h in range(24):
        now = NOW + timedelta(hours=h)
        world.clock(now)
        assert (await schedule.run_due(now)).checked == 1
    assert len(world.outbox.sent) == 1, "one day, one account, one mail"
    world.clock(NOW + timedelta(hours=24))
    await schedule.run_due(NOW + timedelta(hours=24))
    first, second = world.outbox.sent
    assert first["subject"] == "Okwan: 3 new findings" and "Store 00" in first["text"]
    assert second["subject"] == "Okwan: 69 new findings across 23 merchants"
    assert all(f"Store {h:02d}" in second["text"] for h in range(1, 24)), "none of the day's findings was lost"
    assert "Store 00" not in second["text"], "the first store's were told on day one"


@pytest.mark.parametrize("failed_runs", [10, 60])
async def test_failed_runs_never_make_a_finding_news_again(world, client, failed_runs):
    """The exploit: ten failed runs pushed the check before out of sight
    and the same findings were mailed again. Sixty push every stored run
    of the merchant out of history (RUNS_KEPT); what the account was told
    is not kept there."""
    kofi = await world.merchant("Kofi's Store", parent=world.root)
    key, _ = await world.store.issue_key(kofi.id, scope=ADMIN)
    good = world.rails[f"{kofi.id}.myshopify.com"]
    await schedule.run_due(NOW)
    at = NOW
    for _ in range(3):
        world.clock(at + timedelta(hours=1))
        world.set_rows(kofi, DEAD)
        for _ in range(failed_runs):
            r = await client.post(f"/v1/tenants/{kofi.id}/reconciliations/across/rails",
                                  headers={"Authorization": f"Bearer {key}"})
            assert r.status_code == 401
        world.set_rows(kofi, good)
        at += timedelta(hours=25, minutes=1)
        world.clock(at)
        got = await schedule.run_due(at)
        assert (got.checked, got.mailed) == (1, 0)
    assert [m["subject"] for m in world.outbox.sent] == ["Okwan: 3 new findings"], "told once"


# ── 5. a run that cannot be stored is not a check ────────────────────

async def test_a_check_whose_result_cannot_be_stored_is_tried_once_a_day_and_not_charged(world, monkeypatch):
    kofi = await world.merchant("Kofi's Store", parent=world.root)

    add_run, broken = world.store.add_run, [True]

    async def refuse(*args, **kwargs):
        if broken[0]:
            raise RuntimeError("the database refused the row")
        return await add_run(*args, **kwargs)

    monkeypatch.setattr(world.store, "add_run", refuse)
    rounds = [await schedule.run_due(NOW + timedelta(hours=h)) for h in range(6)]
    assert [(r.checked, r.failed, r.mailed) for r in rounds] == [(0, 1, 0)] + [(0, 0, 0)] * 5
    assert world.outbox.sent == [] and await world.store.usage_since(world.root.id, EVER) == 0
    assert (await world.store.daily_checks([kofi.id]))[kofi.id].told == ()
    broken[0] = False
    mended = await schedule.run_due(NOW + timedelta(hours=24, minutes=1))
    assert (mended.checked, mended.mailed) == (1, 1), "nothing was marked as told meanwhile"


async def test_the_round_stops_when_the_scheduler_cannot_keep_its_own_record(world, monkeypatch):
    """If the slot cannot be written down, no rail is read: otherwise the
    same merchant would be read again every hour for as long as it lasted."""
    kofi = await world.merchant("Kofi's Store", parent=world.root)

    async def down(*args, **kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(world.store, "claim_daily_check", down)
    with pytest.raises(schedule.RecordUnavailable):
        await schedule.run_due(NOW)
    assert await world.store.list_runs(kofi.id) == [] and world.outbox.sent == []


async def test_one_merchant_or_account_that_breaks_does_not_end_the_round_for_the_rest(world, monkeypatch):
    """Anything else that goes wrong for one merchant, or in one account's
    mail, is that one's alone: the others are checked and mailed."""
    other = await world.account("Other ISV", "owner@other.test")
    broken = await world.merchant("Broken Store", parent=world.root)
    fine = await world.merchant("Fine Store", parent=other)
    resolve = schedule.vault_resolver

    async def resolver(tenant):
        if tenant.id == broken.id:
            raise RuntimeError("this merchant's vault cannot be read")
        return await resolve(tenant)

    monkeypatch.setattr(schedule, "vault_resolver", resolver)
    r = await schedule.run_due(NOW)
    assert (r.checked, r.failed, r.mailed) == (1, 1, 1)
    assert [x.status for x in await world.store.list_runs(fine.id)] == ["ok"]
    assert [x.status for x in await world.store.list_runs(broken.id)] == ["failed"]

    # And the mail: one account's cannot be put together; the other's still goes.
    world.outbox.sent.clear()
    later = NOW + timedelta(hours=25)
    world.set_rows(fine, week(without=("#2047",)))
    world.set_rows(broken, week())
    monkeypatch.setattr(schedule, "vault_resolver", resolve)
    await world.store.set_notify(world.root.id, finding_emails=True)
    email = world.store.account_email

    async def no_email(tenant_id):
        if tenant_id == world.root.id:
            raise RuntimeError("this account cannot be looked up")
        return await email(tenant_id)

    monkeypatch.setattr(world.store, "account_email", no_email)
    await schedule.run_due(later)                         # Fine Store: nothing new; Broken Store: three, unsendable
    world.set_rows(fine, week())
    again = await schedule.run_due(later + timedelta(hours=25))
    assert again.mailed == 1 and [m["to"] for m in world.outbox.sent] == ["owner@other.test"]


@pytest.mark.skipif(not DSN, reason="no vault database configured")
@pytest.mark.parametrize("poison", ["\x00", "\ud800"], ids=["nul", "lone-surrogate"])
async def test_an_order_reference_the_database_refuses_no_longer_loses_the_run(monkeypatch, poison):
    """The exploit: one such character in one order's name, and the run
    could not be stored, so the check ran, was charged and was mailed
    every hour. Now the run is stored, with the character replaced."""
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    root = None
    try:
        world = make_world(monkeypatch, store)
        root = await world.account(f"RT {uuid.uuid4().hex[:6]}", f"rt-{uuid.uuid4().hex[:8]}@acme.test")
        everyone = store.list_tenants

        async def only_mine():  # the test database may hold other accounts
            mine = {root.id} | {t.id for t in await store.children_of(root.id)}
            return [t for t in await everyone() if t.id in mine]

        monkeypatch.setattr(store, "list_tenants", only_mine)
        rows = copy.deepcopy(week())
        next(o for o in rows["shopify"] if o["name"] == "#2047")["name"] = f"#2047{poison}"
        kofi = await world.merchant("Kofi's Store", rows=rows, parent=root)

        rounds = [await schedule.run_due(NOW + timedelta(hours=h)) for h in range(6)]
        assert [(r.checked, r.failed, r.mailed) for r in rounds] == [(1, 0, 1)] + [(0, 0, 0)] * 5
        assert [m["subject"] for m in world.outbox.sent] == ["Okwan: 3 new findings"]
        [run] = await store.list_runs(kofi.id)
        stored = (await store.get_run(kofi.id, run.id)).rows
        assert any(str(r["order"]["ref"]).startswith("#2047") for r in stored), "the order is in the stored run"
        assert await store.usage_since(root.id, EVER) == 1, "charged once"
        # The next day the same order is the same finding, not a new one.
        tomorrow = await schedule.run_due(NOW + timedelta(hours=24, minutes=1))
        assert (tomorrow.checked, tomorrow.mailed) == (1, 0)
    finally:
        set_mailer(None)
        if root is not None:
            await store.delete_tenant(root.id)
        await store.close()


# ── 6. nothing is nested out of its account's sight ──────────────────

async def test_a_tenant_cannot_be_made_deeper_than_its_account_can_see(world, client):
    """The exploit built a chain nine levels down with a merchant's admin
    key, where the account could not open the tenant and its plan did not
    reach. The ninth level is now refused."""
    top = await world.store.create_tenant("Merchant with an admin key", parent_id=world.root.id)
    key, _ = await world.store.issue_key(top.id, scope=ADMIN)
    current, owner = top.id, await world.session_for(world.root)
    for level in range(2, MAX_DEPTH + 2):
        auth = {"Authorization": f"Bearer {key}"}
        made = await client.post("/v1/tenants", headers=auth, json={"name": f"level {level}"})
        if level > MAX_DEPTH:
            assert (made.status_code, made.json()["detail"]) == (400, TOO_DEEP)
            break
        assert made.status_code == 201, made.text
        current = made.json()["id"]
        key = (await client.post(f"/v1/tenants/{current}/keys", headers=auth, json={"scope": "admin"})).json()["secret"]
    # The deepest tenant that can exist is one the account opens, and pays for.
    assert (await client.get(f"/v1/tenants/{current}/runs", headers=owner)).status_code == 200
    await world.connect(current)
    r = await schedule.run_due(NOW)
    assert (r.checked, r.mailed) == (1, 1)
    assert await world.store.usage_since(world.root.id, EVER) == 1


async def _nine_down(world):
    """A tenant nine levels down, put straight into the store: what the
    route now refuses, and what a database made before the rule could hold."""
    parent = world.root
    for level in range(1, MAX_DEPTH + 2):
        parent = await world.store.create_tenant(f"level {level}", parent_id=parent.id)
    await world.connect(parent.id)
    return parent


async def test_a_tenant_out_of_the_accounts_sight_is_not_checked_or_mailed_about(world, client):
    deep = await _nine_down(world)
    owner = await world.session_for(world.root)
    assert (await client.get(f"/v1/tenants/{deep.id}/runs", headers=owner)).status_code == 404
    r = await schedule.run_due(NOW)
    assert (r.checked, r.failed, r.mailed) == (0, 0, 0)
    assert await world.store.list_runs(deep.id) == [] and world.outbox.sent == []


async def test_no_daily_check_runs_on_a_spent_plan_at_any_depth(world):
    deep = await _nine_down(world)
    eight = await world.store.get_tenant(deep.parent_id)
    await world.connect(eight.id)                                # the deepest the account can see
    world.store._usage[(world.root.id, hour_bucket(), "rest:query")] = PLANS["free"]
    r = await schedule.run_due(NOW)
    assert (r.checked, r.out_of_plan) == (0, 1)
    assert await world.store.list_runs(eight.id) == [] and await world.store.list_runs(deep.id) == []
    assert await world.store.usage_since(world.root.id, EVER) == PLANS["free"], "nothing was added to it"


# ── 7. a slow check cannot hold up a round ───────────────────────────

async def test_no_check_starts_once_a_round_has_used_its_time(world, monkeypatch):
    """One merchant's check may be slow (its own rows decide: known-gaps
    G19). The round stops starting checks at its budget, and the merchant
    that waited is the first one checked next hour."""
    slow = await world.merchant("Slow Store", parent=world.root)
    other = await world.account("Other ISV", "owner@other.test")
    waited = [await world.merchant(f"Waiting {i}", parent=other) for i in range(2)]
    # Every merchant is due; the other account was served longest ago.
    for merchant, hours in ((slow, 25), (waited[0], 48), (waited[1], 26)):
        await world.store.claim_daily_check(merchant.id, NOW - timedelta(hours=hours))
    world.read_takes = schedule.ROUND_BUDGET / 5      # three reads a check: two checks pass the budget
    first = await schedule.run_due(NOW)
    assert first.checked == 2, "the budget ran out before the third"
    done = [len(await world.store.list_runs(m.id)) for m in (waited[0], slow, waited[1])]
    assert done == [1, 1, 0], "one from each account: the account, and the merchant, served longest ago first"
    world.elapsed, world.read_takes = 0.0, 0.0
    second = await schedule.run_due(NOW + timedelta(hours=1))
    assert second.checked == 1 and len(await world.store.list_runs(waited[1].id)) == 1


# ── 8. only a currency code reaches the mail ─────────────────────────

POISON = "USD\n  WIRE YOUR REFUND TO ACCT 12345 - OKWAN PAYOUTS"


def test_a_crafted_currency_gives_no_total_and_no_line():
    unpaid = {"outcome": "uncollected", "order": {"ref": "#1", "currency": POISON}, "order_total_minor": 9800}
    assert schedule.lines([unpaid]) == ["1 order with no payment found"]
    for odd in ("usd", "US", "USDX", "U$D", "", None, 840):
        assert schedule.lines([{**unpaid, "order": {"ref": "#1", "currency": odd}}]) == ["1 order with no payment found"]


async def test_a_crafted_currency_never_reaches_the_sent_mail(world):
    rows = week()
    for order in rows["shopify"]:
        if order["name"] == "#2046":  # the order with no payment
            order["currency"] = POISON
    await world.merchant("Hostile Store", rows=rows, parent=world.root)
    assert (await schedule.run_due(NOW)).mailed == 1
    body = world.outbox.sent[0]["text"]
    assert "WIRE YOUR REFUND" not in body.upper()
    assert "  1 order with no payment found\n" in body, "the finding is still reported, without a figure"


# ── and what held is still held ──────────────────────────────────────

async def test_a_failed_mail_is_not_counted_as_the_days_mail(world):
    class Failing(Outbox):
        async def send_notice(self, *args, **kwargs) -> None:
            raise MailNotSent("resend answered 500 unknown")

    await world.merchant("Kofi's Store", parent=world.root)
    set_mailer(Failing())
    assert (await schedule.run_due(NOW)).mailed == 0
    assert (await world.store.get_notify(world.root.id))["mailed_at"] is None
