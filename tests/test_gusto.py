"""Gusto: payrolls read as minor units, a token that renews itself once,
and Connect Gusto storing a company's tokens without ever showing one.

What would fail silently: a tip counted from a float, a missing total read
as zero, a window that slides while paging, a refresh token spent twice by
two racing requests (Gusto spends it once, so the second would disconnect
the business), a renewed pair never written back (the next request would
use a spent token), a callback that connects whoever sends a code, or a
token in a response, an error or a log. Fixture rows are shaped like
Gusto's documented responses; the token endpoint is httpx's MockTransport.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

import httpx
import pytest
from okwan_core import ConnectorContext
from okwan_gusto import auth as gusto_auth
from okwan_gusto import connector as gusto_connector
from okwan_gusto.auth import API_VERSION, Tokens, _GustoAuth
from okwan_gusto.connector import list_payrolls, window
from okwan_gusto.schemas import ListPayrollsIn, Payroll

COMPANY = "7b1d0df1-6403-4a06-8768-c1dd7d24d27a"

PAYROLLS = [
    {"payroll_uuid": "p-0901", "company_uuid": COMPANY, "processed": True, "processed_date": "2026-09-14",
     "check_date": "2026-09-16", "off_cycle": False,
     "pay_period": {"start_date": "2026-09-01", "end_date": "2026-09-15", "pay_schedule_uuid": "s1"},
     "totals": {"gross_pay": "2450.00", "net_pay": "1980.37", "employee_cash_tips": "312.50",
                "employee_paycheck_tips": "88.10", "employer_taxes": "187.43"}},
    {"payroll_uuid": "p-0916", "company_uuid": COMPANY, "processed": True, "processed_date": "2026-09-29",
     "check_date": "2026-10-01", "off_cycle": False,
     "pay_period": {"start_date": "2026-09-16", "end_date": "2026-09-30"},
     "totals": {"gross_pay": "2610.25", "net_pay": "2101.90", "employee_cash_tips": "290.00",
                "employee_paycheck_tips": "0.00"}},
    {"payroll_uuid": "p-bonus", "company_uuid": COMPANY, "processed": True, "processed_date": "2026-10-03",
     "check_date": "2026-10-03", "off_cycle": True,
     "pay_period": {"start_date": "2026-09-16", "end_date": "2026-09-30"}},
]


# ── a payroll, as Okwan reads it ──────────────────────────────────────

def test_a_payroll_reads_as_minor_units_with_every_tip():
    p = Payroll.from_gusto(PAYROLLS[0])
    assert (p.gross_pay_minor, p.net_pay_minor) == (245000, 198037)
    assert (p.cash_tips_minor, p.paycheck_tips_minor, p.tips_minor) == (31250, 8810, 40060)
    assert (p.check_date, p.period_start, p.period_end) == (date(2026, 9, 16), date(2026, 9, 1), date(2026, 9, 15))
    assert p.processed and not p.off_cycle and p.currency == "USD"
    assert "employee" not in json.dumps(p.model_dump(mode="json")).lower().replace("employees", "")


def test_a_payroll_with_no_totals_has_no_amounts_not_zero():
    p = Payroll.from_gusto(PAYROLLS[2])
    assert (p.gross_pay_minor, p.cash_tips_minor, p.tips_minor) == (None, None, None)
    assert p.off_cycle


def test_zero_paycheck_tips_are_zero_and_still_add_up():
    p = Payroll.from_gusto(PAYROLLS[1])
    assert (p.cash_tips_minor, p.paycheck_tips_minor, p.tips_minor) == (29000, 0, 29000)


# ── the walk ───────────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, pages: list[list[dict]]) -> None:
        self.pages = list(pages)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        self.calls.append((path, dict(params or {})))
        return self.pages.pop(0) if self.pages else []

    async def aclose(self) -> None:
        pass


CREDS = {"company_uuid": COMPANY, "access_token": "at-1", "refresh_token": "rt-1", "expires_at": "9999999999"}


async def test_the_walk_pins_its_window_and_pages_by_number_until_a_short_page():
    client = _FakeClient([PAYROLLS[:2], PAYROLLS[2:]])
    ctx = ConnectorContext(client=client, credentials=CREDS)
    params = {"limit": 2, "start_date": date(2026, 9, 1), "end_date": date(2026, 10, 31)}
    seen, cursor = [], None
    while True:
        page = await list_payrolls(ctx, ListPayrollsIn(**params, cursor=cursor))
        seen += [p.payroll_uuid for p in page.items]
        cursor = page.next_cursor
        if not page.has_more:
            break
    assert seen == ["p-0901", "p-0916", "p-bonus"] and len(client.calls) == 2
    path, first = client.calls[0]
    assert path == f"/v1/companies/{COMPANY}/payrolls"
    assert first == {"processing_statuses": "processed", "payroll_types": "regular,off_cycle", "include": "totals",
                     "start_date": "2026-09-01", "end_date": "2026-10-31", "sort_order": "asc", "page": 1, "per": 2}
    assert client.calls[1][1]["page"] == 2
    assert {(c[1]["start_date"], c[1]["end_date"]) for c in client.calls} == {("2026-09-01", "2026-10-31")}
    assert page.span_start.date() == date(2026, 9, 1) and page.span_end.date() == date(2026, 10, 31)


async def test_regular_payrolls_only_when_asked():
    client = _FakeClient([[]])
    await list_payrolls(ConnectorContext(client=client, credentials=CREDS), ListPayrollsIn(off_cycle=False))
    assert client.calls[0][1]["payroll_types"] == "regular"


def test_a_window_defaults_to_90_days_and_is_at_most_a_year():
    today = date(2026, 11, 9)
    assert window(ListPayrollsIn(), today) == (date(2026, 8, 11), today)
    assert window(ListPayrollsIn(start_date=date(2024, 1, 1)), today) == (date(2025, 11, 9), today)
    assert window(ListPayrollsIn(start_date=date(2026, 12, 1), end_date=date(2026, 11, 1)), today) == (
        date(2026, 11, 1), date(2026, 11, 1))


async def test_an_unreadable_cursor_restarts_the_walk():
    client = _FakeClient([[]])
    await list_payrolls(ConnectorContext(client=client, credentials=CREDS), ListPayrollsIn(cursor="not-ours"))
    assert client.calls[0][1]["page"] == 1


# ── the token: renewed once, reported, never echoed ───────────────────

@pytest.fixture
def gusto_app(monkeypatch):
    monkeypatch.setenv("OKWAN_GUSTO_CLIENT_ID", "cid-okwan")
    monkeypatch.setenv("OKWAN_GUSTO_CLIENT_SECRET", "secret-okwan")
    monkeypatch.setenv("OKWAN_DASHBOARD_URL", "https://app.okwan.test")
    monkeypatch.delenv("OKWAN_GUSTO_ENV", raising=False)
    monkeypatch.setattr(gusto_auth, "_rotated", {})


class Gusto:
    """Gusto's demo host as far as these tests need it."""

    def __init__(self) -> None:
        self.token_calls: list[dict] = []
        #: Each API call's headers as they arrived: a retry reuses the request object.
        self.api_calls: list[httpx.Headers] = []
        self.refusing = False
        self.issued = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.gusto-demo.com"
        if request.url.path == "/oauth/token":
            body = json.loads(request.content)
            self.token_calls.append(body)
            if self.refusing:
                return httpx.Response(401, json={"error": "invalid_grant", "echo": body.get("refresh_token")})
            self.issued += 1
            return httpx.Response(200, json={"access_token": f"at-{self.issued + 1}", "token_type": "Bearer",
                                             "expires_in": 7200, "refresh_token": f"rt-{self.issued + 1}"})
        if request.url.path == "/v1/token_info":
            return httpx.Response(200, json={"scope": "payrolls:read", "resource": {"type": "Company", "uuid": COMPANY},
                                             "resource_owner": {"type": "CompanyAdmin", "uuid": "admin-1"}})
        self.api_calls.append(httpx.Headers(request.headers))
        if request.headers["Authorization"] == "Bearer at-stale":
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(200, json=[])


@pytest.fixture
def gusto(gusto_app, monkeypatch):
    g = Gusto()
    monkeypatch.setattr(gusto_auth, "transport", httpx.MockTransport(g.handler))
    return g


async def _call(auth: _GustoAuth, g: Gusto, path: str = "/v1/companies/x/payrolls") -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.MockTransport(g.handler), auth=auth,
                                 base_url="https://api.gusto-demo.com") as c:
        return await c.get(path)


async def test_an_expired_token_is_renewed_before_the_call_and_the_new_pair_reported(gusto):
    auth = _GustoAuth({**CREDS, "expires_at": "0"})
    reported: list[Tokens] = []

    async def keep(t: Tokens) -> None:
        reported.append(t)

    auth.on_rotate = keep
    r = await _call(auth, gusto)
    assert r.status_code == 200
    [body] = gusto.token_calls
    assert body == {"client_id": "cid-okwan", "client_secret": "secret-okwan",
                    "redirect_uri": "https://app.okwan.test/integrations/gusto/callback",
                    "refresh_token": "rt-1", "grant_type": "refresh_token"}
    [call] = gusto.api_calls
    assert call["Authorization"] == "Bearer at-2"
    assert call["X-Gusto-API-Version"] == API_VERSION
    assert [(t.access_token, t.refresh_token) for t in reported] == [("at-2", "rt-2")]


async def test_a_401_renews_once_and_retries(gusto):
    auth = _GustoAuth({**CREDS, "access_token": "at-stale"})
    r = await _call(auth, gusto)
    assert r.status_code == 200
    assert len(gusto.token_calls) == 1
    assert [c["Authorization"] for c in gusto.api_calls] == ["Bearer at-stale", "Bearer at-2"]


async def test_two_requests_racing_spend_the_refresh_token_once(gusto):
    a, b = _GustoAuth({**CREDS, "expires_at": "0"}), _GustoAuth({**CREDS, "expires_at": "0"})
    await asyncio.gather(_call(a, gusto), _call(b, gusto))
    assert len(gusto.token_calls) == 1, "Gusto spends a refresh token once; a second refresh would disconnect"
    assert {c["Authorization"] for c in gusto.api_calls} == {"Bearer at-2"}


async def test_a_refused_refresh_says_connect_again_and_never_echoes_gusto(gusto):
    gusto.refusing = True
    with pytest.raises(Exception) as caught:
        await _call(_GustoAuth({**CREDS, "expires_at": "0"}), gusto)
    assert "connect Gusto again" in str(caught.value)
    assert "rt-1" not in str(caught.value) and "secret-okwan" not in str(caught.value)


def test_without_okwan_s_own_app_credentials_gusto_is_not_set_up(monkeypatch):
    monkeypatch.delenv("OKWAN_GUSTO_CLIENT_ID", raising=False)
    monkeypatch.delenv("OKWAN_GUSTO_CLIENT_SECRET", raising=False)
    assert not gusto_auth.configured()


def test_the_authorize_link_names_okwan_s_app_its_callback_and_the_state(gusto_app):
    url = httpx.URL(gusto_auth.authorize_url("st-1"))
    assert (url.host, url.path) == ("api.gusto-demo.com", "/oauth/authorize")
    assert dict(url.params) == {"client_id": "cid-okwan", "response_type": "code", "state": "st-1",
                                "redirect_uri": "https://app.okwan.test/integrations/gusto/callback"}


# ── Connect Gusto, end to end ─────────────────────────────────────────

@pytest.fixture
async def api(gusto, monkeypatch):
    from okwan_api.auth import set_store
    from okwan_api.main import app
    from okwan_vault import EnvMasterKey, MemoryStore, new_key
    from okwan_vault.accounts import SESSION_PREFIX, new_token

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    shop = await store.create_tenant("Corner Café")
    other = await store.create_tenant("Someone else")
    full, token_hash = new_token(SESSION_PREFIX)
    from datetime import UTC, datetime, timedelta

    await store.create_session(shop.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    other_full, other_hash = new_token(SESSION_PREFIX)
    await store.create_session(other.id, other_hash, datetime.now(UTC) + timedelta(hours=1))

    def factory(connector, credentials):
        return ConnectorContext(client=_FakeClient([PAYROLLS[:1]]), credentials=credentials)

    monkeypatch.setattr(gusto_connector.gusto, "context_factory", factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        c.headers["Authorization"] = f"Bearer {full}"
        yield c, store, shop, (other.id, {"Authorization": f"Bearer {other_full}"})


async def test_connect_gusto_stores_the_company_s_tokens_tests_them_and_shows_neither(api):
    client, store, shop, _ = api
    start = await client.post(f"/v1/tenants/{shop.id}/connectors/gusto/authorize")
    assert start.status_code == 200, start.text
    state = start.json()["state"]
    assert f"state={state}" in start.json()["url"]
    done = await client.post(f"/v1/tenants/{shop.id}/connectors/gusto/callback", json={"code": "code-1", "state": state})
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["connected"], body["company_uuid"]) == (True, COMPANY)
    assert (body["test"]["status"], body["test"]["operation"], body["test"]["rows"]) == ("rows", "gusto.payrolls.list", 1)
    stored = await store.credentials_for(shop.id, "gusto", ("company_uuid", "access_token", "refresh_token", "expires_at"))
    assert (stored["company_uuid"], stored["access_token"], stored["refresh_token"]) == (COMPANY, "at-2", "rt-2")
    assert "at-2" not in done.text and "rt-2" not in done.text and "secret-okwan" not in done.text
    again = await client.post(f"/v1/tenants/{shop.id}/connectors/gusto/callback", json={"code": "code-1", "state": state})
    assert again.status_code == 400, "a state works once"


async def test_a_code_with_a_state_this_caller_did_not_start_connects_nothing(api):
    client, store, shop, (other_id, stranger) = api
    assert (await client.post(f"/v1/tenants/{shop.id}/connectors/gusto/callback",
                              json={"code": "c", "state": "made-up"})).status_code == 400
    state = (await client.post(f"/v1/tenants/{shop.id}/connectors/gusto/authorize")).json()["state"]
    # Another account, on its own tenant: the state is not theirs.
    r = await client.post(f"/v1/tenants/{other_id}/connectors/gusto/callback", headers=stranger,
                          json={"code": "c", "state": state})
    assert r.status_code == 400
    assert await store.connectors_configured(shop.id) == {}
    assert await store.connectors_configured(other_id) == {}


async def test_a_gusto_token_cannot_be_typed_in(api):
    client, _, shop, _ = api
    r = await client.put(f"/v1/tenants/{shop.id}/credentials",
                         json={"connector": "gusto", "field": "access_token", "value": "pasted"})
    assert r.status_code == 400 and "Connect" in r.text


async def test_without_okwan_s_app_credentials_authorize_says_so(api, monkeypatch):
    client, _, shop, _ = api
    monkeypatch.delenv("OKWAN_GUSTO_CLIENT_SECRET")
    r = await client.post(f"/v1/tenants/{shop.id}/connectors/gusto/authorize")
    assert r.status_code == 503 and "isn't set up" in r.text


async def test_the_catalog_says_gusto_connects_with_a_button_and_reads_payrolls(api):
    client, *_ = api
    [g] = [c for c in (await client.get("/v1/connectors")).json() if c["name"] == "gusto"]
    assert g["connect"] == "oauth"
    assert g["credential_fields"] == ["company_uuid", "access_token", "refresh_token", "expires_at"]
    assert (g["probe"], g["sql_tables"], g["writes"]) == ("gusto.payrolls.list", ["gusto.payrolls"], [])
    others = [c for c in (await client.get("/v1/connectors")).json() if c["name"] != "gusto"]
    assert {c["connect"] for c in others} == {"fields"}


async def test_a_renewed_pair_is_written_back_to_the_tenant_s_vault(gusto_app, monkeypatch):
    from okwan_vault import EnvMasterKey, MemoryStore, new_key

    store = MemoryStore(EnvMasterKey(new_key()))
    shop = await store.create_tenant("Corner Café")
    monkeypatch.setattr(gusto_connector, "_store", lambda: store)
    ctx = gusto_connector.gusto.context(CREDS, tenant_id=shop.id)
    try:
        await ctx.client._client.auth.on_rotate(Tokens("at-9", "rt-9", 1234))
    finally:
        await ctx.client.aclose()
    stored = await store.credentials_for(shop.id, "gusto", ("access_token", "refresh_token", "expires_at"))
    assert stored == {"access_token": "at-9", "refresh_token": "rt-9", "expires_at": "1234"}
