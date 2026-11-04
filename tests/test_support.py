"""The support assistant: answers from the caller's own account, never anyone
else's, changes nothing, never lets a secret reach the model or the database,
and hands what it cannot settle to a person.

The model is a scripted fake with the SDK's call shape, so these tests pin
the request Okwan sends and everything Okwan does with the reply, without a
network call. Both stores, like tests/test_tenant_isolation.py.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from okwan_api import support
from okwan_api.main import app
from okwan_vault import EnvMasterKey, MemoryStore, PostgresStore, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
NOW = datetime.now(UTC)


# ── a scripted model ───────────────────────────────────────────────────

def text(t: str) -> dict:
    return {"type": "text", "text": t}


def use(name: str, tool_input: dict | None = None, id: str = "toolu_1") -> dict:
    return {"type": "tool_use", "id": id, "name": name, "input": tool_input or {}}


THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc"}


def turn(*content, stop: str | None = None) -> SimpleNamespace:
    blocks = list(content)
    if stop is None:
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return SimpleNamespace(content=blocks, stop_reason=stop)


class FakeModel:
    """`client.beta.messages.create` that plays a script and records every call."""

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=self)

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(kwargs) if callable(step) else step

    def tool_results(self, call: int) -> list[dict]:
        last = self.calls[call]["messages"][-1]
        return [b for b in last["content"] if b.get("type") == "tool_result"]


@pytest.fixture
def model():
    holder = {}

    def install(*script) -> FakeModel:
        holder["m"] = FakeModel(*script)
        support.set_client(holder["m"])
        return holder["m"]

    yield install
    support.set_client(None)


class Outbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send_verification(self, email, link):  # pragma: no cover - not used here
        raise AssertionError("support never sends verification mail")

    async def send_notice(self, to, subject, body):
        self.sent.append((to, subject, body))


@pytest.fixture
def outbox(monkeypatch):
    from okwan_api import mail

    box = Outbox()
    mail.set_mailer(box)
    monkeypatch.setenv("OKWAN_SUPPORT_ESCALATION_TO", "owner@okwan.test")
    yield box
    mail._overridden = False
    mail._mailer = None


# ── the world: account A with a merchant, rival account B ──────────────

@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        yield MemoryStore(EnvMasterKey(new_key()))
        return
    if not DSN:
        pytest.skip("no vault database configured")
    s = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    created: list[str] = []
    create = s.create_tenant

    async def tracked(name, parent_id=None):
        tenant = await create(name, parent_id=parent_id)
        created.append(tenant.id)
        return tenant

    s.create_tenant = tracked
    try:
        yield s
    finally:
        for tenant_id in reversed(created):
            await s.pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await s.close()


@pytest.fixture
async def world(store):
    a = await store.create_tenant("acme@okwan.test")
    a1 = await store.create_tenant("Acme Shop One", parent_id=a.id)
    b = await store.create_tenant("rival@okwan.test")
    await store.put_credential(a.id, "stripe", "secret_key", "sk_test_ACMEsecretvalue123")
    await store.put_credential(b.id, "stripe", "secret_key", "sk_test_RIVALsecretvalue99")
    await store.add_run(a1.id, kind="across", name="rails", surface="dashboard", status="failed",
                        started_at=NOW, finished_at=NOW, summary=None, rows=None,
                        error="stripe answered 401 for key sk_live_LEAKEDinERRORtext123")
    await store.add_run(b.id, kind="across", name="rails", surface="rest", status="ok",
                        started_at=NOW, finished_at=NOW, summary={"orders": 7}, rows=[], error=None)
    await store.issue_key(a.id)
    await store.issue_key(b.id)
    sessions = {}
    for key, t in (("a", a), ("b", b)):
        full, token_hash = new_token(SESSION_PREFIX)
        await store.create_session(t.id, token_hash, NOW + timedelta(hours=1))
        sessions[key] = full
    return SimpleNamespace(store=store, a=a, a1=a1, b=b, sessions=sessions)


@pytest.fixture
async def client(store):
    from okwan_api.auth import set_store

    set_store(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://okwan.test") as c:
        yield c


async def ask(client, world, message: str, who: str = "a", conversation_id: str | None = None):
    body = {"message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return await client.post("/v1/support/messages", json=body,
                             headers={"Authorization": f"Bearer {world.sessions[who]}"})


# ── switched off until configured ──────────────────────────────────────

async def test_off_until_an_api_key_is_set(client, world, monkeypatch):
    support.set_client(None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = await ask(client, world, "hello")
    assert r.status_code == 503
    assert "not switched on" in r.json()["detail"]


async def test_it_needs_a_session_or_key(client, world, model):
    model(turn(text("hi")))
    r = await client.post("/v1/support/messages", json={"message": "hello"})
    assert r.status_code == 401


# ── the request Okwan sends ────────────────────────────────────────────

async def test_the_request_is_opus_low_effort_with_fallback_and_a_cached_prefix(client, world, model):
    m = model(turn(text("Hello! How can I help?")))
    r = await ask(client, world, "hello")
    assert r.status_code == 200, r.text
    call = m.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"] == {"effort": "low"}
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert "thinking" not in call, "Opus 5.5 always thinks; the parameter is omitted"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["cache_control"] == {"type": "ephemeral"}
    assert call["max_tokens"] == 16000


def test_every_tool_reads_except_escalate():
    names = {t["name"] for t in support.TOOLS}
    assert names == support.READ_ONLY_TOOLS | {"escalate"}
    for t in support.TOOLS:
        assert t["input_schema"].get("additionalProperties") is False, t["name"]


def test_the_knowledge_is_generated_from_the_registries():
    from okwan_core import all_connectors
    from okwan_recon.registry import all_reconciliations

    k = support.knowledge()
    for c in all_connectors():
        assert f"**{c.name}**" in k and all(f in k for f in c.auth.required_fields)
    for r in all_reconciliations():
        assert f"`{r.name}`" in k


# ── one answer, one conversation ───────────────────────────────────────

async def test_an_answer_starts_a_conversation_stored_for_the_caller(client, world, store, model):
    model(turn(THINKING, text("Your plan is free.")))
    r = await ask(client, world, "what plan am I on?")
    body = r.json()
    assert body["reply"] == "Your plan is free."
    assert body["conversation_id"].startswith("sup_") and body["ticket"] is None
    saved = await store.get_conversation(world.a.id, body["conversation_id"])
    assert saved.turns == 1
    assert [m["role"] for m in saved.messages] == ["user", "assistant"]
    assert await store.get_conversation(world.b.id, body["conversation_id"]) is None


async def test_the_history_replays_exactly_as_produced(client, world, model):
    """Thinking blocks must go back unchanged, so the transcript lives on the
    server and is appended to, never rebuilt."""
    m = model(turn(THINKING, text("First answer.")), turn(text("Second answer.")))
    first = (await ask(client, world, "one")).json()
    await ask(client, world, "two", conversation_id=first["conversation_id"])
    sent = m.calls[1]["messages"]
    assert sent[0] == m.calls[0]["messages"][0]
    assert sent[1] == {"role": "assistant", "content": [THINKING, text("First answer.")]}
    assert sent[2]["content"] == [{"type": "text", "text": "two"}]


async def test_asking_for_help_uses_none_of_the_plan(client, world, store, model):
    model(turn(text("hi")))
    await ask(client, world, "hello")
    assert await store.usage_since(world.a.id, NOW - timedelta(days=1)) == 0


# ── isolation: the caller's account and nothing else ───────────────────

async def test_the_overview_is_the_callers_account_only(client, world, model):
    m = model(turn(use("account_overview")), turn(text("Here is your account.")))
    await ask(client, world, "show my account")
    result = m.tool_results(1)[0]["content"]
    assert world.a.id in result and world.a1.id in result
    assert world.b.id not in result and "rival" not in result
    assert "ACMEsecret" not in result, "an overview names connectors, never values"


@pytest.mark.parametrize("tool", ["connection_status", "test_connection", "recent_runs", "api_keys"])
async def test_another_accounts_tenant_is_not_in_this_account(client, world, model, tool):
    tool_input = {"tenant_id": world.b.id}
    if tool in ("connection_status", "test_connection"):
        tool_input["connector"] = "stripe"
    m = model(turn(use(tool, tool_input)), turn(text("I can't see that.")))
    r = await ask(client, world, "look at the rival account")
    assert r.status_code == 200
    result = m.tool_results(1)[0]
    assert result["is_error"] is True and "not in this account" in result["content"]
    sent = json.dumps(m.calls[1])
    assert "rival@okwan.test" not in sent and "RIVAL" not in sent and '"orders": 7' not in sent


async def test_a_merchant_in_the_account_is_reachable(client, world, model):
    m = model(turn(use("recent_runs", {"tenant_id": world.a1.id})), turn(text("One failed run.")))
    await ask(client, world, "why did my merchant's run fail?")
    result = m.tool_results(1)[0]
    assert "is_error" not in result
    assert '"status": "failed"' in result["content"]


async def test_another_accounts_conversation_is_404(client, world, store, model):
    model(turn(text("Private answer.")))
    mine = (await ask(client, world, "my private question")).json()["conversation_id"]
    model(turn(text("never sent")))
    r = await ask(client, world, "let me read it", who="b", conversation_id=mine)
    assert r.status_code == 404
    kept = await store.get_conversation(world.a.id, mine)
    assert kept.turns == 1 and "let me read it" not in json.dumps(kept.messages)


async def test_api_keys_show_prefixes_never_hashes(client, world, store, model):
    m = model(turn(use("api_keys")), turn(text("You have one key.")))
    await ask(client, world, "is my key revoked?")
    result = m.tool_results(1)[0]["content"]
    keys = await store.keys_for(world.a.id)
    assert keys[0].prefix in result
    if keys[0].hash_hex:
        assert keys[0].hash_hex not in result


# ── secrets ────────────────────────────────────────────────────────────

SECRETS = [
    "okw_" + "A" * 43,
    "oks_" + "b1" * 20,
    "sk_live_51HxQ2bL0ngSecretValue",
    "rk_test_abcdefghijklmnop",
    "shpat_" + "0123456789abcdef" * 2,
    "whsec_abcdefghij1234567890",
    "EAA" + "x1" * 20,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "Bearer abcdefghijklmnopqrstuvwxyz012345",
    "EOv" + "a1B2c3D4e5" * 8,  # a PayPal-length client secret
]


@pytest.mark.parametrize("secret", SECRETS, ids=lambda s: s[:6])
def test_secret_looking_text_is_removed(secret):
    clean, found = support.redact(f"here it is: {secret} thanks")
    assert secret not in clean and found
    assert "[removed:" in clean and clean.startswith("here it is:") and clean.endswith("thanks")


def test_a_connection_string_keeps_everything_but_the_password():
    clean, found = support.redact("postgresql://shop:hunter2pass@db.example.com:5432/shop")
    assert clean == "postgresql://shop:[removed]@db.example.com:5432/shop"
    assert found == ["a database password"]


@pytest.mark.parametrize("ordinary", [
    "ten_6f1c2a9d0b3e4f57", "run_0123456789abcdef", "ch_3NxQ2bL2eZvKYlo21Abc1234",
    "order #1002 for $999.00", "8f14e45f-ceea-467a-9575-1b2d3c4e5f60", "pk_live_publishable",
    "my email is felix@example.com",
])
def test_ordinary_ids_and_text_are_kept(ordinary):
    assert support.redact(ordinary) == (ordinary, [])


async def test_a_pasted_secret_never_reaches_the_model_or_the_database(client, world, store, model):
    m = model(turn(text("Please replace that key.")))
    secret = "sk_live_51HxQ2bL0ngSecretValue"
    r = await ask(client, world, f"my stripe key {secret} doesn't work")
    body = r.json()
    assert body["removed"] == ["a Stripe or Paystack secret key"]
    assert body["message"] == "my stripe key [removed: a Stripe or Paystack secret key] doesn't work"
    sent = json.dumps(m.calls[0])
    assert secret not in sent and "[removed: a Stripe or Paystack secret key]" in sent
    notes = [x for x in m.calls[0]["messages"] if x["role"] == "system"]
    assert notes and "replace it" in notes[0]["content"]
    saved = await store.get_conversation(world.a.id, body["conversation_id"])
    assert secret not in json.dumps(saved.messages)


async def test_a_secret_inside_a_tool_result_is_removed(client, world, model):
    m = model(turn(use("recent_runs", {"tenant_id": world.a1.id})), turn(text("ok")))
    await ask(client, world, "what went wrong?")
    result = m.tool_results(1)[0]["content"]
    assert "LEAKEDinERRORtext" not in result and "[removed:" in result


# ── escalation ─────────────────────────────────────────────────────────

async def test_an_escalation_is_recorded_and_mailed_to_the_owner(client, world, store, model, outbox):
    secret = "sk_live_51HxQ2bL0ngSecretValue"
    m = model(turn(use("escalate", {"severity": "sev1", "category": "data_exposure",
                                    "summary": f"Customer reports seeing orders that are not theirs; "
                                               f"also pasted {secret}"})),
              turn(text("I've passed this to a person at Okwan.")))
    r = await ask(client, world, f"I can see another shop's orders! key {secret}")
    body = r.json()
    assert body["ticket"].startswith("tkt_")
    tickets = await store.list_tickets()
    mine = next(t for t in tickets if t.id == body["ticket"])
    assert (mine.tenant_id, mine.severity, mine.category, mine.notified) == (
        world.a.id, "sev1", "data_exposure", True)
    assert mine.conversation_id == body["conversation_id"]
    assert secret not in mine.summary
    to, subject, mail_body = outbox.sent[0]
    assert to == "owner@okwan.test" and "sev1" in subject
    assert world.a.id in mail_body and "another shop's orders" in mail_body
    assert secret not in mail_body and secret not in subject
    assert "is_error" not in m.tool_results(1)[0]


async def test_without_mail_an_escalation_is_still_recorded(client, world, store, model, monkeypatch,
                                                         caplog):
    monkeypatch.delenv("OKWAN_SUPPORT_ESCALATION_TO", raising=False)
    model(turn(use("escalate", {"severity": "sev3", "category": "billing",
                                "summary": "wants the pro plan"})), turn(text("Recorded.")))
    with caplog.at_level("WARNING", logger="okwan_api.support"):
        ticket = (await ask(client, world, "upgrade me")).json()["ticket"]
    found = next(t for t in await store.list_tickets() if t.id == ticket)
    assert found.notified is False
    assert "OKWAN_SUPPORT_ESCALATION_TO is not set" in caplog.text
    assert world.a.id in caplog.text


async def test_without_a_mailer_the_log_says_so(client, world, model, monkeypatch, caplog):
    from okwan_api import mail

    monkeypatch.setenv("OKWAN_SUPPORT_ESCALATION_TO", "owner@okwan.test")
    mail.set_mailer(None)
    try:
        model(turn(use("escalate", {"severity": "sev2", "category": "bug", "summary": "x"})),
              turn(text("Recorded.")))
        with caplog.at_level("WARNING", logger="okwan_api.support"):
            await ask(client, world, "it broke")
    finally:
        mail._overridden = False
    assert "no mailer" in caplog.text


@pytest.mark.parametrize("mailed", [True, False])
async def test_the_customer_hears_the_same_whether_or_not_mail_went_out(
    client, world, model, monkeypatch, outbox, mailed
):
    """Delivery is the operator's concern: nothing in the model's view of the
    escalation changes with it, so the customer is never told about it."""
    if not mailed:
        monkeypatch.delenv("OKWAN_SUPPORT_ESCALATION_TO", raising=False)
    m = model(turn(use("escalate", {"severity": "sev3", "category": "other", "summary": "x"})),
              turn(text("A person has it.")))
    await ask(client, world, "please pass this to a person")
    result = json.loads(m.tool_results(1)[0]["content"])
    assert set(result) == {"ticket", "next"}
    assert result["next"] == "A person at Okwan has this conversation and will follow up."
    assert bool(outbox.sent) is mailed


async def test_an_escalation_with_an_unknown_severity_is_refused(client, world, store, model, outbox):
    m = model(turn(use("escalate", {"severity": "urgent!!", "category": "other",
                                    "summary": "x"})), turn(text("ok")))
    await ask(client, world, "help")
    assert m.tool_results(1)[0]["is_error"] is True
    assert outbox.sent == []


# ── limits ─────────────────────────────────────────────────────────────

async def test_tool_rounds_are_capped_and_the_last_round_must_answer(client, world, model):
    rounds = [turn(use("account_overview", id=f"toolu_{i}")) for i in range(support.MAX_TOOL_ROUNDS)]
    m = model(*rounds, turn(text("Here's what I found.")))
    r = await ask(client, world, "loop forever")
    assert r.json()["reply"] == "Here's what I found."
    assert len(m.calls) == support.MAX_TOOL_ROUNDS + 1
    assert m.calls[-1]["tool_choice"] == {"type": "none"}
    assert all("tool_choice" not in c for c in m.calls[:-1])


async def test_a_refusal_gets_a_safe_reply(client, world, model):
    model(turn(stop="refusal"))
    r = await ask(client, world, "something declined")
    assert r.json()["reply"] == support.REFUSED


async def test_a_full_conversation_asks_for_a_new_one(client, world, store, model):
    m = model(turn(text("never sent")))
    cid = await store.save_conversation(world.a.id, None, [], support.MAX_TURNS)
    r = await ask(client, world, "one more", conversation_id=cid)
    assert r.status_code == 409
    assert m.calls == [], "a full conversation is refused before any model call"


async def test_messages_are_rate_limited(client, world, model, monkeypatch):
    from okwan_api.ratelimit import Rule

    monkeypatch.setattr(support, "SUPPORT_TENANT", Rule("support:tenant:test", 2, 3600))
    model(turn(text("1")), turn(text("2")), turn(text("3")))
    codes = [(await ask(client, world, f"q{i}")).status_code for i in range(3)]
    assert codes == [200, 200, 429]


async def test_a_model_outage_is_503_and_saves_nothing(client, world, store, model):
    model(anthropic.APIConnectionError(request=None))
    r = await ask(client, world, "hello")
    assert r.status_code == 503
    rows = [t for t in await store.list_tickets() if t.tenant_id == world.a.id]
    assert rows == []
