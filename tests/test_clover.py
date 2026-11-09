"""Clover connector: flattening, card data, the 90-day walk, and its two checks.

Fixtures are rows recorded from the Clover sandbox on 2026-11-07 (§3.5
probe: three sales on test merchant 6Q2985K6VA301), trimmed, and Clover's
documented card-payment and refund shapes: the sandbox takes card sales
and refunds only through Clover's separate Ecommerce API. A fake
transport replays pages and records what was asked, so the walk is
tested without a network.

What would fail silently: a card's first six digits, the cardholder's
name or a card token carried into a row; a tip left out of what the card
was charged; a voided refund counted as refunded; an evening sale booked
to tomorrow because it is past midnight in UTC; a lookback longer than 90
days asked of Clover in one query, which Clover cuts to its most recent
90 without saying so; an offset walk whose end moves, so a new sale shifts
a row into the page already read; two same-sized sales on one day paired
with each other's settlement because amount and day were all that was
compared.
"""
from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any

import httpx
import okwan_clover.connector  # noqa: F401  (registers the connector)
import okwan_files.connector  # noqa: F401  (registers the connector)
import pytest
from okwan_clover import connector as clover_connector
from okwan_clover.auth import HOSTS, CloverAuth, host_for, merchant_id
from okwan_clover.connector import (
    MAX_SLICES_PER_CALL,
    SLICE_DAYS,
    _decode,
    _encode,
    list_orders,
    list_payments,
    list_refunds,
)
from okwan_clover.schemas import (
    ListOrdersIn,
    ListPaymentsIn,
    ListRefundsIn,
    Order,
    Payment,
    Refund,
)
from okwan_core import CredentialError, get
from okwan_core.connector import ConnectorContext
from okwan_query.catalog import tables_for
from okwan_recon import declarations
from okwan_recon.calendar import is_business_day
from okwan_recon.engine import match

MID = "6Q2985K6VA301"
CREDS = {"access_token": "test-token", "merchant_id": MID, "environment": "sandbox"}
PACIFIC = {"timezone": "America/Los_Angeles", "currency": "USD"}


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


# ── fixtures: the sandbox, trimmed, and Clover's documented shapes ──

#: Recorded from the sandbox: an order paid with an external tender.
LIVE = {
    "id": "34ER5JPVNWWDC", "amount": 1250, "externalPaymentId": "EXT-OKW-1001",
    "createdTime": 1794066331000, "clientCreatedTime": 1794066331000, "modifiedTime": 1794066330000,
    "result": "SUCCESS", "refunds": {"elements": []},
    "order": {"id": "WEJQ9XBTPC0NG", "currency": "USD", "total": 1250, "title": "OKW-1001"},
    "tender": {"id": "1FWH52CRGCZBP", "labelKey": "com.clover.tender.external_payment",
               "label": "External Payment"},
}
LIVE_ORDER = {
    "id": "WEJQ9XBTPC0NG", "currency": "USD", "total": 1250, "title": "OKW-1001",
    "note": "Okwan sandbox test sale", "state": "open", "paymentState": "PAID",
    "manualTransaction": False, "testMode": False, "createdTime": 1794066330000,
    "modifiedTime": 1794066331000, "payments": {"elements": [{"id": "34ER5JPVNWWDC"}]},
}

#: 8:30 p.m. Pacific on Friday 6 November 2026: already Saturday in UTC.
FRIDAY_EVENING = datetime(2026, 11, 7, 4, 30, tzinfo=UTC)

#: Clover's documented card payment, with everything a card transaction carries.
CARD = {
    "id": "CARDPAYMENT01", "order": {"id": "ORDER0000001"}, "amount": 4000, "tipAmount": 600,
    "taxAmount": 300, "createdTime": _ms(FRIDAY_EVENING), "modifiedTime": _ms(FRIDAY_EVENING),
    "result": "SUCCESS", "offline": False,
    "tender": {"labelKey": "com.clover.tender.credit_card", "label": "Credit Card"},
    "cardTransaction": {
        "cardType": "VISA", "last4": "1111", "first6": "411111", "cardholderName": "KOFI MENSAH",
        "token": "tok_7HxQ2", "authCode": "OK1234", "referenceId": "630500001111",
        "transactionNo": "000123", "entryType": "EMV_CONTACTLESS", "state": "CLOSED",
        "currency": "usd", "avsResult": "SUCCESS", "extra": {"authorizingNetworkName": "VISA"},
        "vaultedCard": {"first6": "411111", "last4": "1111", "cardholderName": "KOFI MENSAH",
                        "token": "vault_9Pq"},
    },
    "refunds": {"elements": [{"id": "REFUND000001", "amount": 1000},
                             {"id": "REFUND000002", "amount": 500, "voided": True}]},
}

REFUND = {
    "id": "REFUND000001", "orderRef": {"id": "ORDER0000001"}, "payment": {"id": "CARDPAYMENT01"},
    "amount": 1000, "taxAmount": 75, "tipAmount": 0, "createdTime": _ms(FRIDAY_EVENING),
    "status": "SUCCESS", "voided": False, "reason": "returned item", "authCode": "RF5678",
    "cardTransaction": {"cardType": "VISA", "last4": "1111", "first6": "411111",
                        "cardholderName": "KOFI MENSAH"},
}


# ── flattening ──────────────────────────────────────────────────────

def test_a_card_payment_flattens_to_cents_with_its_tip_and_live_refunds():
    p = Payment.model_validate(CARD, context=PACIFIC)
    assert (p.amount_minor, p.tip_minor, p.tax_minor, p.total_minor) == (4000, 600, 300, 4600)
    assert p.refunded_minor == 1000  # the voided refund is not money back
    assert (p.order_id, p.tender, p.result, p.currency) == ("ORDER0000001", "credit_card", "SUCCESS", "USD")
    assert (p.card_brand, p.card_last4, p.auth_code, p.processor_ref, p.card_entry) == (
        "VISA", "1111", "OK1234", "630500001111", "EMV_CONTACTLESS")


def test_nothing_from_the_card_but_brand_last_four_and_authorization_leaves_a_row():
    for row in (Payment.model_validate(CARD, context=PACIFIC), Refund.model_validate(REFUND, context=PACIFIC)):
        dumped = json.dumps(row.model_dump(mode="json"))
        for kept_out in ("411111", "KOFI", "tok_7HxQ2", "vault_9Pq", "first6", "cardholder", "token"):
            assert kept_out not in dumped


def test_an_evening_sale_is_the_merchants_day_not_utcs():
    p = Payment.model_validate(CARD, context=PACIFIC)
    assert p.created_at == FRIDAY_EVENING
    assert p.created_on == date(2026, 11, 6)  # Friday where the shop is
    assert Payment.model_validate(CARD).created_on == date(2026, 11, 7)  # no timezone known: UTC


def test_the_live_sandbox_payment_and_its_order():
    p = Payment.model_validate(LIVE, context=PACIFIC)
    assert (p.id, p.order_id, p.total_minor, p.tender, p.external_payment_id) == (
        "34ER5JPVNWWDC", "WEJQ9XBTPC0NG", 1250, "external_payment", "EXT-OKW-1001")
    assert p.card_last4 is None and p.refunded_minor == 0
    o = Order.model_validate(LIVE_ORDER, context=PACIFIC)
    assert (o.total_minor, o.payment_state, o.payment_ids, o.currency) == (1250, "PAID", ["34ER5JPVNWWDC"], "USD")
    assert "note" not in o.model_dump()  # free text a ticket can hold a name in


def test_a_refund_flattens():
    r = Refund.model_validate(REFUND, context=PACIFIC)
    assert (r.payment_id, r.order_id, r.amount_minor, r.tax_minor, r.currency) == (
        "CARDPAYMENT01", "ORDER0000001", 1000, 75, "USD")
    assert (r.auth_code, r.card_last4, r.voided, r.created_on) == ("RF5678", "1111", False, date(2026, 11, 6))


def test_a_flat_row_validates_as_itself():
    p = Payment.model_validate(CARD, context=PACIFIC)
    assert Payment.model_validate(p.model_dump(mode="json")) == p


# ── the credentials ─────────────────────────────────────────────────

def test_the_environment_picks_the_host_and_nothing_else_is_guessed():
    assert host_for({"environment": " Sandbox "}) == HOSTS["sandbox"] == "https://apisandbox.dev.clover.com"
    assert host_for({"environment": "production"}) == "https://api.clover.com"
    with pytest.raises(CredentialError):
        host_for({"environment": "live"})


@pytest.mark.parametrize("bad", ["", "6Q2985K6VA30", "6Q2985K6VA301X", "../payments?x", "6Q2985K6VA3/1"])
def test_a_merchant_id_that_could_steer_the_path_is_refused(bad):
    with pytest.raises(CredentialError):
        merchant_id({"merchant_id": bad})
    assert merchant_id({"merchant_id": " 6q2985k6va301 "}) == MID


def test_every_request_carries_the_token_and_the_field_names_match_the_vault():
    assert CloverAuth().required_fields == ("access_token", "merchant_id", "environment")
    request = httpx.Request("GET", "https://apisandbox.dev.clover.com/v3/merchants/x")
    flow = CloverAuth().bind(CREDS).auth_flow(request)
    assert next(flow).headers["Authorization"] == "Bearer test-token"


# ── the walk, against a fake transport ──────────────────────────────

class _FakeClient:
    """The merchant once, then list pages in order; records every call."""

    def __init__(self, pages: list[list[dict]] | None = None) -> None:
        self.pages = [{"elements": p} for p in pages or []]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, dict(params or {})))
        if path == f"/v3/merchants/{MID}":
            return {"id": MID, "properties": {"timezone": "America/Los_Angeles", "defaultCurrency": "USD"}}
        return self.pages.pop(0) if self.pages else {"elements": []}

    async def aclose(self) -> None:
        pass

    def lists(self) -> list[dict[str, Any]]:
        return [params for path, params in self.calls if path != f"/v3/merchants/{MID}"]


def _ctx(client: _FakeClient) -> ConnectorContext:
    return ConnectorContext(client=client, credentials=CREDS)


def _bounds(params: dict[str, Any]) -> tuple[int, int]:
    lo = next(f for f in params["filter"] if f.startswith("createdTime>="))
    hi = next(f for f in params["filter"] if f.startswith("createdTime<"))
    return int(lo.removeprefix("createdTime>=")), int(hi.removeprefix("createdTime<"))


async def test_a_window_longer_than_90_days_is_read_in_slices_oldest_first():
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 7, 20, tzinfo=UTC)  # 200 days
    client = _FakeClient([[], [], [LIVE]])
    page = await list_payments(_ctx(client), ListPaymentsIn(start_date=start, end_date=end))
    slices = [_bounds(p) for p in client.lists()]
    assert len(slices) == 3 and slices[0][0] == _ms(start) and slices[-1][1] == _ms(end)
    assert all(hi - lo <= SLICE_DAYS * 86_400_000 for lo, hi in slices)  # never past Clover's 90
    assert all(a[1] == b[0] for a, b in pairwise(slices))  # contiguous, no gap
    assert all(p["orderBy"] == "createdTime ASC" and p["offset"] == 0 for p in client.lists())
    assert [p.id for p in page.items] == ["34ER5JPVNWWDC"] and not page.has_more
    assert (page.span_start, page.span_end) == (start, end)  # what the walk covered, stated


async def test_a_full_page_continues_in_the_same_slice_at_the_next_offset():
    start, end = datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC)
    client = _FakeClient([[LIVE, CARD], [{**LIVE, "id": "THIRDPAYMENT1"}]])
    first = await list_payments(_ctx(client), ListPaymentsIn(start_date=start, end_date=end, limit=2))
    assert first.has_more and len(first.items) == 2
    second = await list_payments(_ctx(client), ListPaymentsIn(cursor=first.next_cursor, limit=2))
    (a, b) = client.lists()
    assert (a["offset"], b["offset"]) == (0, 2) and _bounds(a) == _bounds(b)
    assert [p.id for p in second.items] == ["THIRDPAYMENT1"] and second.next_cursor is None


async def test_the_end_is_pinned_so_a_sale_rung_up_mid_walk_cannot_shift_the_pages():
    client = _FakeClient([[LIVE, CARD], []])
    since = datetime.now(UTC) - timedelta(days=30)  # one slice, ending now
    first = await list_payments(_ctx(client), ListPaymentsIn(start_date=since, limit=2))
    pinned = _decode(first.next_cursor)["e"]
    await list_payments(_ctx(client), ListPaymentsIn(cursor=first.next_cursor, limit=2))
    assert [_bounds(p)[1] for p in client.lists()] == [pinned, pinned]  # the same end, page after page


async def test_a_quiet_stretch_is_walked_inline_but_each_call_is_bounded():
    start, end = datetime(2021, 11, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)  # five years
    client = _FakeClient()
    page = await list_payments(_ctx(client), ListPaymentsIn(start_date=start, end_date=end))
    assert len(client.lists()) == MAX_SLICES_PER_CALL
    assert page.items == [] and page.has_more and page.next_cursor  # come back for the rest


def test_a_walk_cursor_round_trips_and_a_bad_one_restarts():
    state = {"a": 1, "s": 5, "e": 9, "o": 100}
    assert _decode(_encode(state)) == state
    assert _decode("not-a-cursor!") is None
    assert _decode(_encode({"a": 1, "s": 10, "e": 9, "o": 0})) is None  # a slice past its own end
    assert _decode(base64.urlsafe_b64encode(b'{"a":1}').decode()) is None


async def test_the_merchant_is_read_once_per_connection():
    client = _FakeClient([[LIVE], [LIVE]])
    ctx = _ctx(client)
    await list_payments(ctx, ListPaymentsIn())
    await list_payments(ctx, ListPaymentsIn())
    assert [path for path, _ in client.calls].count(f"/v3/merchants/{MID}") == 1


async def test_filters_go_to_clover_and_card_only_keeps_what_a_processor_settles():
    client = _FakeClient([[LIVE, CARD]])
    page = await list_payments(_ctx(client), ListPaymentsIn(result="success", card_only=True))
    (params,) = client.lists()
    assert "result=SUCCESS" in params["filter"] and params["expand"] == "tender,cardTransaction,refunds"
    assert [p.id for p in page.items] == ["CARDPAYMENT01"]


async def test_orders_skip_empty_tickets_and_refunds_can_leave_out_voids():
    client = _FakeClient([[LIVE_ORDER, {**LIVE_ORDER, "id": "EMPTYTICKET01", "total": None}], [REFUND]])
    orders = await list_orders(_ctx(client), ListOrdersIn(state="OPEN", skip_empty=True))
    refunds = await list_refunds(_ctx(client), ListRefundsIn(voided=False))
    o_params, r_params = client.lists()
    assert "state=open" in o_params["filter"] and o_params["expand"] == "payments"
    assert "voided=false" in r_params["filter"] and "expand" not in r_params
    assert [o.id for o in orders.items] == ["WEJQ9XBTPC0NG"] and [r.id for r in refunds.items] == ["REFUND000001"]


# ── the one definition ──────────────────────────────────────────────

def test_three_resources_every_one_a_read_and_each_a_sql_table():
    clover = get("clover")
    assert all(op.is_read_only for _, op in clover.iter_operations())
    tables = {f"{t.connector}.{t.resource}": dict(t.columns) for t in tables_for(clover)}
    assert set(tables) == {"clover.payments", "clover.refunds", "clover.orders"}
    assert tables["clover.payments"]["total_minor"] == "BIGINT"  # the computed field is a column
    assert not {"first6", "cardholder_name", "token"} & set(tables["clover.payments"])
    assert clover.probe()[0].name == "payments"  # a connection test reads a page of payments


async def test_the_generated_mcp_tools_are_the_three_lists():
    from okwan_mcp.generator import list_tool_names

    assert set(await list_tool_names(get("clover"))) == {
        "clover_payments_list", "clover_refunds_list", "clover_orders_list"}


def test_both_checks_read_only_and_resolve():
    for spec in (declarations.clover_orders_payments, declarations.clover_payments_settlements):
        spec.validate_against_registry()


# ── orders against their payments ───────────────────────────────────

def _payments(*raw: dict) -> list[dict]:
    return [Payment.model_validate(r, context=PACIFIC).model_dump(mode="json") for r in raw]


def _orders(*raw: dict) -> list[dict]:
    return [Order.model_validate(r, context=PACIFIC).model_dump(mode="json") for r in raw]


def _order(oid: str, total: int) -> dict:
    return {**LIVE_ORDER, "id": oid, "total": total, "payments": {"elements": []}}


def _paid(pid: str, oid: str, amount: int, **extra: Any) -> dict:
    return {**LIVE, "id": pid, "order": {"id": oid}, "amount": amount, **extra}


def test_a_split_check_is_one_order_and_short_or_unpaid_tickets_are_findings():
    spec = declarations.clover_orders_payments
    orders = _orders(_order("SPLIT", 5000), _order("SHORT", 1250), _order("WALKOUT", 900))
    payments = _payments(_paid("P1", "SPLIT", 3000), _paid("P2", "SPLIT", 2000),
                         _paid("P3", "SHORT", 1000, tipAmount=250),  # a tip is not the ticket
                         _paid("P9", "ELSEWHERE", 700))
    result = match(spec, orders, payments)
    by = {p.left["id"]: p for p in result.matched}
    assert by["SPLIT"].discrepancy_minor == 0 and by["SPLIT"].evidence["key"]["value"] == "SPLIT"
    assert by["SHORT"].discrepancy_minor == 250  # paid 10.00 of 12.50
    assert [o["id"] for o in result.unmatched_left] == ["WALKOUT"]
    assert [p["id"] for p in result.unmatched_right] == ["P9"]


# ── card payments against the processor's settlement ────────────────

def _settled(txn: str, day: str, amount: int, last4: str | None, auth: str | None = None) -> dict:
    return {"transaction_id": txn, "transacted_on": day, "amount_minor": amount, "currency": "USD",
            "card_last4": last4, "auth_code": auth, "file_id": "imp_1", "row": 2, "layout": "processor"}


def _card(pid: str, amount: int, last4: str, auth: str, at: datetime, tip: int = 0) -> dict:
    txn = {**CARD["cardTransaction"], "last4": last4, "authCode": auth}
    return {**CARD, "id": pid, "amount": amount, "tipAmount": tip, "createdTime": _ms(at),
            "cardTransaction": txn, "refunds": {"elements": []}}


FRI = datetime(2026, 11, 6, 19, 0, tzinfo=UTC)   # 11 a.m. Pacific, Friday
MON = datetime(2026, 11, 2, 19, 0, tzinfo=UTC)   # 11 a.m. Pacific, Monday


def _settlement_case() -> tuple[list[dict], list[dict]]:
    payments = _payments(
        _card("COFFEE_A", 1250, "1111", "A11111", FRI),
        _card("COFFEE_B", 1250, "2222", "B22222", FRI),
        _card("TIPPED", 4000, "3333", "C33333", FRI_EVENING := FRIDAY_EVENING, tip=600),
        _card("LATE", 900, "4444", "D44444", MON),
    )
    assert FRI_EVENING  # booked Friday where the shop is, Saturday in UTC
    export = [
        _settled("X1", "2026-11-06", 1250, "2222", "B22222"),
        _settled("X2", "2026-11-06", 1250, "1111"),             # the file has no auth code column
        _settled("X3", "2026-11-09", 4600, "3333"),             # dated by batch: the next business day
        _settled("X4", "2026-11-04", 900, "4444"),              # two business days on: not this sale
        _settled("X5", "2026-11-06", 1250, "1111", "ZZ9999"),   # same card, another authorization
    ]
    return payments, export


def test_same_sized_sales_on_one_day_meet_their_own_settlement_by_card():
    spec = declarations.clover_payments_settlements
    payments, export = _settlement_case()
    result = match(spec, payments, export)
    pairs = {p.left["id"]: p.right["transaction_id"] for p in result.matched}
    assert pairs == {"COFFEE_A": "X2", "COFFEE_B": "X1", "TIPPED": "X3"}
    evidence = {p.left["id"]: p.evidence for p in result.matched}
    assert evidence["COFFEE_B"]["agreed"] == ["card_last4", "auth_code"]
    assert evidence["COFFEE_A"]["agreed"] == ["card_last4"]  # a column the file lacks says nothing
    assert evidence["TIPPED"]["lag"]["business_days"] == 1 and result.matched[0].discrepancy_minor == 0
    assert [p["id"] for p in result.unmatched_left] == ["LATE"]
    assert {x["transaction_id"] for x in result.unmatched_right} == {"X4", "X5"}
    assert not result.ambiguous


def test_without_the_card_guard_the_same_day_is_a_coin_toss():
    spec = declarations.clover_payments_settlements
    (rule,) = spec.keys
    unguarded = spec.model_copy(update={"keys": [rule.model_copy(update={"agree": ()})]})
    payments, export = _settlement_case()
    result = match(unguarded, payments, export)
    assert {a.left["id"] for a in result.ambiguous} == {"COFFEE_A", "COFFEE_B"}  # said, not guessed


# ── every surface, as a provisioned tenant ──────────────────────────

def _recent_business_day() -> date:
    day = datetime.now(UTC).date() - timedelta(days=10)
    while not is_business_day(day):
        day -= timedelta(days=1)
    return day


@pytest.fixture
async def tenant(monkeypatch):
    from okwan_api.auth import set_store
    from okwan_api.main import app
    from okwan_vault import EnvMasterKey, MemoryStore, new_key

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    shop = await store.create_tenant("Corner Café")
    key, _ = await store.issue_key(shop.id, scope="admin")
    for field, value in CREDS.items():
        await store.put_credential(shop.id, "clover", field, value)

    day = _recent_business_day()
    noon = datetime(day.year, day.month, day.day, 20, 0, tzinfo=UTC)  # noon Pacific
    seen: list[dict[str, str]] = []

    def factory(connector, credentials):
        seen.append(dict(credentials))
        return ConnectorContext(client=_FakeClient([[
            _card("CAFE_SALE_1", 1250, "1111", "A11111", noon),
            _card("CAFE_SALE_2", 875, "2222", "B22222", noon),
        ]]), credentials=credentials)

    monkeypatch.setattr(clover_connector.clover, "context_factory", factory)
    export = (
        "Date,Amount,Card,Auth,Transaction\n"
        f"{day:%m/%d/%Y},12.50,************1111,A11111,T-1\n"
        f"{day:%m/%d/%Y},-3.00,************1111,,T-2\n"   # a refund: not a sale to settle
        f"{day:%m/%d/%Y},99.00,************9999,Z99999,T-3\n"
    ).encode()
    mapping = {"kind": "settlement_transactions", "currency": "USD", "name": "processor",
               "columns": {"transacted_on": "Date", "amount_minor": "Amount", "card_last4": "Card",
                           "auth_code": "Auth", "transaction_id": "Transaction"}}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://okwan.test") as c:
        c.headers["Authorization"] = f"Bearer {key}"
        r = await c.post(f"/v1/tenants/{shop.id}/imports", json={
            "filename": "processor-export.csv", "content_base64": base64.b64encode(export).decode(),
            "mapping": mapping})
        assert r.status_code == 201, r.text
        yield c, seen


async def test_clover_is_queryable_for_a_provisioned_tenant_on_every_surface(tenant):
    client, seen = tenant
    rest = await client.post("/v1/clover/payments/list", json={"result": "SUCCESS", "card_only": True})
    assert rest.status_code == 200, rest.text
    assert [p["id"] for p in rest.json()["items"]] == ["CAFE_SALE_1", "CAFE_SALE_2"]
    assert seen and all(c == CREDS for c in seen)  # the tenant's own vault, nothing from the request

    recon = await client.get("/v1/reconciliations/clover_payments_settlements")
    assert recon.status_code == 200, recon.text
    rows = recon.json()["data"]
    verdict = {(r.get("left") or {}).get("id") or (r.get("right") or {}).get("transaction_id"): r["status"]
               for r in rows}
    assert verdict == {"CAFE_SALE_1": "matched", "CAFE_SALE_2": "unmatched_left", "T-3": "unmatched_right"}

    sql = await client.post("/v1/query", json={
        "sql": "SELECT status, count(*) AS n FROM recon.clover_payments_settlements GROUP BY status "
               "ORDER BY status"})
    assert sql.status_code == 200, sql.text
    body = sql.json()["rows"]
    counts = {r[0]: r[1] for r in body} if body and isinstance(body[0], list) else {r["status"]: r["n"] for r in body}
    assert counts == {"matched": 1, "unmatched_left": 1, "unmatched_right": 1}
