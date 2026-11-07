"""Square connector: flattening, card data, paging, and its two checks.

Fixtures are rows recorded from the Square sandbox on 2026-11-07 (§3.5
probe), trimmed, and the Payouts API guide's documented examples: the
sandbox makes no payouts. A fake transport replays pages and records
what was asked, so the walks are tested without a network.

What would fail silently: a card's first six digits or fingerprint
carried into a row; a refund-free payment read as refunded by None; a
payout's `currency_code` dropped so its currency reads empty; a payout
entry walk that skips a payout or repeats one when it resumes; a status
filter that moves the cursor and replays a page; a payment's fee read
as an unexplained break; a deposit matched before the payout arrived.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import okwan_files.connector  # noqa: F401  (registers the connector)
import okwan_square.connector  # noqa: F401  (registers the connector)
import pytest
from okwan_core import CredentialError, get
from okwan_query.catalog import tables_for
from okwan_recon import declarations
from okwan_recon.engine import match
from okwan_square.auth import API_VERSION, SquareAuth, host_for
from okwan_square.connector import (
    _decode,
    _encode,
    list_orders,
    list_payments,
    list_payout_entries,
)
from okwan_square.schemas import (
    ListOrdersIn,
    ListPaymentsIn,
    ListPayoutEntriesIn,
    Order,
    Payment,
    Payout,
    PayoutEntry,
    Refund,
)

# ── fixtures: the sandbox, trimmed ──────────────────────────────────

CARD = {"status": "CAPTURED", "entry_method": "KEYED",
        "card": {"card_brand": "VISA", "last_4": "5858", "exp_month": 11, "exp_year": 2028,
                 "fingerprint": "sq-1-kg3crZKb-C-q8_H2smGILVGduuJWrTty73PrIaxVJuFeN5yB",
                 "card_type": "CREDIT", "prepaid_type": "NOT_PREPAID", "bin": "453275"}}

TIPPED_REFUNDED = {
    "id": "8XtWXMbD4qXioQkxUmFS90nD2DPZY", "created_at": "2026-11-07T13:55:26.116Z",
    "updated_at": "2026-11-07T13:55:40.995Z", "amount_money": {"amount": 12999, "currency": "USD"},
    "tip_money": {"amount": 200, "currency": "USD"}, "refunded_money": {"amount": 2000, "currency": "USD"},
    "status": "COMPLETED", "source_type": "CARD", "card_details": CARD, "location_id": "LQ1NQJGY9BQJ4",
    "order_id": "rqAzdOr7TfLz3ol82JuoEVaJ3ZUZY", "reference_id": "OKW-1002",
    "processing_fee": [{"effective_at": "2026-11-07T15:55:28.000Z", "type": "INITIAL",
                        "amount_money": {"amount": 413, "currency": "USD"}}],
    "total_money": {"amount": 13199, "currency": "USD"}, "receipt_number": "8XtW",
}

PLAIN = {
    "id": "E3BFTpruXV5ilHwxdLY3aQGAxyZZY", "created_at": "2026-11-07T13:55:25.028Z",
    "amount_money": {"amount": 4500, "currency": "USD"}, "total_money": {"amount": 4500, "currency": "USD"},
    "status": "COMPLETED", "source_type": "CARD", "card_details": CARD, "location_id": "LQ1NQJGY9BQJ4",
    "order_id": "1jcAHGXP9sQBnMS97LUtLdvVuVDZY", "reference_id": "OKW-1001",
    "processing_fee": [{"type": "INITIAL", "amount_money": {"amount": 161, "currency": "USD"}}],
}

REFUND = {
    "id": "8XtWXMbD4qXioQkxUmFS90nD2DPZY_YYPfhB6MZ1roDP2PfZYvvsmik0W12JNmQPDMjFtnFfT",
    "status": "COMPLETED", "amount_money": {"amount": 2000, "currency": "USD"},
    "payment_id": "8XtWXMbD4qXioQkxUmFS90nD2DPZY", "order_id": "fi7v0kgpWBcvRZs48k3Jf7rHZeVZY",
    "location_id": "LQ1NQJGY9BQJ4", "reason": "Okwan probe: partial refund", "destination_type": "CARD",
    "created_at": "2026-11-07T13:55:37.072Z",
}

SALE = {
    "id": "rqAzdOr7TfLz3ol82JuoEVaJ3ZUZY", "location_id": "LQ1NQJGY9BQJ4", "state": "COMPLETED",
    "reference_id": "OKW-1002", "created_at": "2026-11-07T13:55:25.640Z", "closed_at": "2026-11-07T13:55:26.379Z",
    "total_tax_money": {"amount": 0, "currency": "USD"}, "total_discount_money": {"amount": 0, "currency": "USD"},
    "total_tip_money": {"amount": 200, "currency": "USD"}, "total_money": {"amount": 13199, "currency": "USD"},
    "total_service_charge_money": {"amount": 0, "currency": "USD"},
    "tenders": [{"id": "8XtWXMbD4qXioQkxUmFS90nD2DPZY", "type": "CARD", "card_details": CARD,
                 "amount_money": {"amount": 13199, "currency": "USD"},
                 "payment_id": "8XtWXMbD4qXioQkxUmFS90nD2DPZY"}],
}

RETURN = {
    "id": "fi7v0kgpWBcvRZs48k3Jf7rHZeVZY", "location_id": "LQ1NQJGY9BQJ4", "state": "COMPLETED",
    "created_at": "2026-11-07T13:55:37.072Z",
    "returns": [{"uid": "4cgZ8pVuq65y0XbS255fJC", "source_order_id": "rqAzdOr7TfLz3ol82JuoEVaJ3ZUZY"}],
    "return_amounts": {"total_money": {"amount": 2000, "currency": "USD"}},
}

# ── fixtures: the Payouts API guide's examples ──────────────────────

PAYOUT = {
    "id": "po_6542107a-323c-6a93-b9df-cggg3949b7e", "status": "PAID", "location_id": "XZKT883AFFSS4E",
    "created_at": "2025-01-23T22:04:59Z", "updated_at": "2025-01-23T23:09:09.684Z",
    "amount_money": {"amount": 7073, "currency_code": "USD"}, "destination": {"type": "CARD", "id": "ccof:x"},
    "version": 2, "type": "BATCH", "arrival_date": "2025-01-23", "number_of_entries": 4,
    "end_to_end_id": "613485392229",
    "payout_fee": [{"amount_money": {"amount": 126, "currency_code": "USD"}, "type": "TRANSFER_FEE"}],
}


def entry(eid: str, kind: str, gross: int, fee: int, payout: str = "po_1", **details: Any) -> dict:
    out = {"id": eid, "payout_id": payout, "effective_at": "2026-11-08T03:07:09Z", "type": kind,
           "gross_amount_money": {"amount": gross, "currency_code": "USD"},
           "fee_amount_money": {"amount": fee, "currency_code": "USD"},
           "net_amount_money": {"amount": gross - fee, "currency_code": "USD"}}
    if details:
        out[f"type_{kind.lower()}_details"] = details
    return out


# ── rows ────────────────────────────────────────────────────────────

def test_a_payment_flattens_with_tip_refund_and_fee():
    p = Payment.model_validate(TIPPED_REFUNDED)
    assert (p.amount_minor, p.tip_minor, p.total_minor, p.refunded_minor) == (12_999, 200, 13_199, 2_000)
    assert (p.fee_minor, p.net_minor) == (413, 12_786)
    assert (p.reference_id, p.order_id, p.currency) == ("OKW-1002", "rqAzdOr7TfLz3ol82JuoEVaJ3ZUZY", "USD")


def test_nothing_from_the_card_but_brand_and_last_four_leaves_a_row():
    for row in (Payment.model_validate(TIPPED_REFUNDED), Order.model_validate(SALE)):
        text = json.dumps(row.model_dump(mode="json"))
        for kept_out in ("453275", "fingerprint", "sq-1-", "exp_", "2028", "bin"):
            assert kept_out not in text
    p = Payment.model_validate(TIPPED_REFUNDED)
    assert (p.card_brand, p.card_last4) == ("VISA", "5858")


def test_no_refund_is_zero_refunded_and_no_fee_yet_is_no_net():
    p = Payment.model_validate({**PLAIN, "processing_fee": None})
    assert p.refunded_minor == 0
    assert p.fee_minor is None and p.net_minor is None  # posted a few seconds after completion


def test_a_refund_flattens():
    r = Refund.model_validate(REFUND)
    assert (r.payment_id, r.amount_minor, r.currency, r.status) == (
        "8XtWXMbD4qXioQkxUmFS90nD2DPZY", 2_000, "USD", "COMPLETED")


def test_a_payout_reads_currency_code_and_its_own_fee():
    p = Payout.model_validate(PAYOUT)
    assert (p.amount_minor, p.currency, p.payout_fee_minor, p.destination_type) == (7_073, "USD", 126, "CARD")
    assert p.arrival_date.isoformat() == "2025-01-23"


@pytest.mark.parametrize("row,payment,refund", [
    (entry("poe_1", "CHARGE", 15_000, 465, payment_id="P1"), "P1", None),
    (entry("poe_2", "REFUND", -5_000, 0, payment_id="P2", refund_id="R2"), "P2", "R2"),
    (entry("poe_3", "DEPOSIT_FEE", -126, 0, payout_id="po_1"), None, None),
])
def test_payout_entries_name_what_they_settle(row, payment, refund):
    e = PayoutEntry.model_validate(row)
    assert (e.payment_id, e.refund_id, e.currency) == (payment, refund, "USD")
    assert e.net_minor == e.gross_minor - e.fee_minor


def test_an_order_and_its_return():
    sale, ret = Order.model_validate(SALE), Order.model_validate(RETURN)
    assert (sale.total_minor, sale.tip_minor, sale.payment_ids) == (13_199, 200, ["8XtWXMbD4qXioQkxUmFS90nD2DPZY"])
    assert (ret.total_minor, ret.returned_minor, ret.source_order_id) == (None, 2_000, sale.id)


# ── auth ────────────────────────────────────────────────────────────

def test_the_environment_picks_the_host_and_nothing_else_is_guessed():
    assert host_for({"environment": "production"}) == "https://connect.squareup.com"
    assert host_for({"environment": " Sandbox "}) == "https://connect.squareupsandbox.com"
    with pytest.raises(CredentialError, match="production' or 'sandbox"):
        host_for({"environment": "live"})
    assert SquareAuth().required_fields == ("access_token", "environment")


def test_every_request_carries_the_token_and_the_pinned_version():
    auth = SquareAuth().bind({"access_token": "EAAAtest", "environment": "sandbox"})
    request = next(auth.auth_flow(httpx.Request("GET", "https://connect.squareupsandbox.com/v2/payments")))
    assert request.headers["Authorization"] == "Bearer EAAAtest"
    assert request.headers["Square-Version"] == API_VERSION


# ── paging, against a fake transport ────────────────────────────────

class _FakeClient:
    """Answers by path, in order, and records every call."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = {k: list(v) for k, v in answers.items()}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("GET", path, dict(params or {})))
        return self.answers[path].pop(0)

    async def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("POST", path, dict(json or {})))
        return self.answers[path].pop(0)

    async def aclose(self) -> None:
        pass


class _Ctx:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client
        self.credentials: dict[str, str] = {}
        self.tenant_id = None


async def test_payments_take_the_window_and_filter_without_moving_the_cursor():
    from datetime import UTC, datetime

    pending = {**PLAIN, "id": "PENDING1", "status": "APPROVED"}
    client = _FakeClient({"/v2/payments": [{"payments": [TIPPED_REFUNDED, pending], "cursor": "c2"}]})
    page = await list_payments(_Ctx(client), ListPaymentsIn(
        status="completed", location_id="L1", start_date=datetime(2026, 9, 1, tzinfo=UTC), limit=50))
    assert [p.id for p in page.items] == ["8XtWXMbD4qXioQkxUmFS90nD2DPZY"]
    assert (page.next_cursor, page.has_more) == ("c2", True)  # Square's cursor, not the filtered tail's
    ((_, _path, params),) = client.calls
    assert params == {"limit": 50, "location_id": "L1", "begin_time": "2026-09-01T00:00:00Z"}


async def test_payout_entries_walk_every_payout_and_resume_where_they_stopped():
    payouts = {"/v2/payouts": [{"payouts": [{"id": "po_1"}, {"id": "po_2"}], "cursor": "pc"},
                               {"payouts": [{"id": "po_3"}]}]}
    entries = {
        "/v2/payouts/po_1/payout-entries": [
            {"payout_entries": [entry("e1", "CHARGE", 100, 3, "po_1", payment_id="P1"),
                                entry("e2", "FEE", -5, 0, "po_1")], "cursor": "ec"},
            {"payout_entries": [entry("e3", "CHARGE", 200, 6, "po_1", payment_id="P3")]}],
        "/v2/payouts/po_2/payout-entries": [{"payout_entries": [entry("e4", "CHARGE", 300, 9, "po_2", payment_id="P4")]}],
        "/v2/payouts/po_3/payout-entries": [{"payout_entries": [entry("e5", "CHARGE", 400, 12, "po_3", payment_id="P5")]}],
    }
    client = _FakeClient({**payouts, **entries})
    first = await list_payout_entries(_Ctx(client), ListPayoutEntriesIn(type="CHARGE", limit=2))
    seen = [e.id for e in first.items]
    cursor = first.next_cursor
    while cursor:
        page = await list_payout_entries(_Ctx(client), ListPayoutEntriesIn(type="CHARGE", limit=2, cursor=cursor))
        seen += [e.id for e in page.items]
        cursor = page.next_cursor
    assert seen == ["e1", "e3", "e4", "e5"]  # every payout, in order, no FEE line, none twice
    assert all(not v for v in client.answers.values())  # every page asked for exactly once


async def test_one_payouts_entries_page_on_squares_cursor():
    client = _FakeClient({"/v2/payouts/po_9/payout-entries": [{"payout_entries": [entry("e9", "CHARGE", 10, 1, "po_9")],
                                                               "cursor": "next"}]})
    page = await list_payout_entries(_Ctx(client), ListPayoutEntriesIn(payout_id="po_9"))
    assert (page.next_cursor, [e.id for e in page.items]) == ("next", ["e9"])


def test_a_walk_cursor_round_trips_and_a_bad_one_restarts():
    state = {"p": "pc", "q": ["po_2"], "c": "po_1", "e": "ec", "d": False}
    assert _decode(_encode(state)) == state
    assert _decode("not-a-cursor!") is None


async def test_orders_search_every_location_by_default_with_the_window():
    from datetime import UTC, datetime

    client = _FakeClient({"/v2/locations": [{"locations": [{"id": "L1"}, {"id": "L2"}]}],
                          "/v2/orders/search": [{"orders": [SALE, RETURN], "cursor": "oc"}]})
    page = await list_orders(_Ctx(client), ListOrdersIn(start_date=datetime(2026, 11, 1, tzinfo=UTC),
                                                        state="completed", limit=200))
    assert [o.id for o in page.items] == [SALE["id"], RETURN["id"]] and page.next_cursor == "oc"
    body = client.calls[1][2]
    assert body["location_ids"] == ["L1", "L2"] and body["limit"] == 200
    assert body["query"]["filter"] == {"date_time_filter": {"created_at": {"start_at": "2026-11-01T00:00:00Z"}},
                                       "state_filter": {"states": ["COMPLETED"]}}


# ── the one definition ──────────────────────────────────────────────

def test_six_resources_every_one_a_read_and_each_a_sql_table():
    square = get("square")
    assert all(op.is_read_only for _, op in square.iter_operations())
    tables = {f"{t.connector}.{t.resource}": dict(t.columns) for t in tables_for(square)}
    assert set(tables) == {f"square.{r}" for r in
                           ("locations", "payments", "refunds", "payouts", "payout_entries", "orders")}
    assert tables["square.payments"]["net_minor"] == "BIGINT"  # the computed field is a column
    assert "bin" not in tables["square.payments"]


# ── the two checks ──────────────────────────────────────────────────

def _rows(model, raw: list[dict]) -> list[dict]:
    return [model.model_validate(r).model_dump(mode="json") for r in raw]


def test_each_payment_meets_its_payout_line_and_the_fee_explains_the_gap():
    spec = declarations.square_payments_payouts
    payments = _rows(Payment, [TIPPED_REFUNDED, PLAIN, {**PLAIN, "id": "NOTPAIDOUT", "reference_id": "OKW-1004"}])
    lines = _rows(PayoutEntry, [
        entry("e1", "CHARGE", 13_199, 413, payment_id="8XtWXMbD4qXioQkxUmFS90nD2DPZY"),
        entry("e2", "CHARGE", 4_500, 200, payment_id="E3BFTpruXV5ilHwxdLY3aQGAxyZZY"),  # fee is not 200
    ])
    result = match(spec, payments, lines)
    by = {(r["left"] or {}).get("id"): r for r in result.rows()}
    assert by["8XtWXMbD4qXioQkxUmFS90nD2DPZY"]["status"] == "matched_explained"
    assert by["8XtWXMbD4qXioQkxUmFS90nD2DPZY"]["explained_by"] == "square_fee"
    assert by["E3BFTpruXV5ilHwxdLY3aQGAxyZZY"]["status"] == "matched_explained"  # the line's own fee
    assert by["NOTPAIDOUT"]["status"] == "unmatched_left"


def test_a_line_whose_fee_does_not_account_for_it_is_a_break():
    spec = declarations.square_payments_payouts
    payments = _rows(Payment, [PLAIN])
    short = entry("e1", "CHARGE", 4_500, 161, payment_id=PLAIN["id"])
    short["net_amount_money"]["amount"] = 4_000  # 339 missing, the fee says 161
    (row,) = match(spec, payments, _rows(PayoutEntry, [short])).rows()
    assert row["status"] == "matched_discrepant"


def _deposit(fitid: str, day: str, amount: int) -> dict:
    return {"fitid": fitid, "posted_on": day, "amount_minor": amount, "currency": "USD",
            "file_id": "imp_1", "row": 2, "layout": "bank"}


def test_a_payout_meets_its_deposit_on_arrival_or_two_business_days_later():
    spec = declarations.square_payouts_deposits
    payouts = _rows(Payout, [
        {**PAYOUT, "id": "po_fri", "arrival_date": "2026-09-04", "amount_money": {"amount": 7_073, "currency_code": "USD"}},
        {**PAYOUT, "id": "po_late", "arrival_date": "2026-11-02", "amount_money": {"amount": 5_000, "currency_code": "USD"}},
        {**PAYOUT, "id": "po_early", "arrival_date": "2026-11-10", "amount_money": {"amount": 9_000, "currency_code": "USD"}},
    ])
    deposits = [
        _deposit("D1", "2026-09-08", 7_073),   # Friday's payout, posted the Tuesday after Labor Day
        _deposit("D2", "2026-11-05", 5_000),   # three business days late: not this payout
        _deposit("D3", "2026-11-09", 9_000),   # the day before it arrived: never
    ]
    result = match(spec, payouts, deposits)
    pairs = {p.left["id"]: p.right["fitid"] for p in result.matched}
    assert pairs == {"po_fri": "D1"}
    assert {r["id"] for r in result.unmatched_left} == {"po_late", "po_early"}
