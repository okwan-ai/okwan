"""Reconciliation coverage — "no payment exists" vs "payments not read".

An unmatched order is a finding only if its payment would have been
read had it existed. A side truncated at the record cap, bounded to a
window, or clamped to a rail's ledger horizon can leave a real payment
unfetched, and the order then reads unpaid with nothing to say why.
These tests pin the property an agent depends on: the output states
what was read, and an unmatched record outside it says it is not a
finding.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import duckdb
import okwan_paypal.connector  # noqa: F401  (registers the connector)
import okwan_shopify.connector  # noqa: F401
import okwan_stripe.connector  # noqa: F401
from okwan_core import ConnectorContext
from okwan_paypal.connector import paypal
from okwan_recon import declarations, runner
from okwan_recon.coverage import Coverage
from okwan_recon.declaration import ResourceRef
from okwan_recon.emitters import mcp
from okwan_recon.emitters.duckdb_view import materialize_view
from okwan_recon.engine import match
from okwan_recon.fetch import fetch_side
from okwan_stripe.connector import stripe

SPEC = declarations.shopify_paypal

START = datetime(2026, 7, 4, tzinfo=UTC)
HORIZON = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


def order(name: str, at: str = "2026-08-27T15:00:00Z") -> dict[str, Any]:
    return {
        "name": name, "currency": "USD", "created_at": at,
        "total_received_minor": 45000, "total_refunded_minor": 0,
        "net_payment_minor": 45000,
    }


def payment(invoice: str, at: str = "2026-08-27T15:02:00Z") -> dict[str, Any]:
    return {
        "invoice_id": invoice, "currency": "USD", "initiated_at": at,
        "amount_minor": 45000, "fee_minor": 0, "net_minor": 45000,
    }


def cov(source: str, **kw: Any) -> Coverage:
    base: dict[str, Any] = {"records": 1, "cap": 500, "truncated": False}
    return Coverage(source=source, **{**base, **kw})


COMPLETE_LEDGER = cov("shopify.orders.list")
PAYPAL_READ = cov("paypal.transactions.list", span_start=START,
                  span_end=HORIZON, horizon=HORIZON)


# --- the verdict -------------------------------------------------------

def test_an_unbounded_untruncated_side_reads_everything():
    assert COMPLETE_LEDGER.reads(None) == (True, None)
    assert COMPLETE_LEDGER.reads(datetime(2001, 1, 1, tzinfo=UTC))[0] is True


def test_inside_the_span_is_read():
    assert PAYPAL_READ.reads(datetime(2026, 8, 27, tzinfo=UTC)) == (True, None)


def test_before_the_span_is_not_read():
    read, why = PAYPAL_READ.reads(datetime(2026, 6, 1, tzinfo=UTC))
    assert read is False
    assert "earlier" in why


def test_after_the_ledger_horizon_is_not_read_and_says_horizon():
    """PayPal lags real time. An order placed after its ledger's horizon
    has a payment that has not landed yet, not one that does not exist."""
    read, why = PAYPAL_READ.reads(HORIZON + timedelta(hours=1))
    assert read is False
    assert "ledger horizon" in why


def test_a_truncated_side_vouches_for_nothing():
    """Which records the cap cut depends on the rail's ordering, so no
    date on the other side can be judged against it."""
    cut = cov("stripe.charges.list", records=100, cap=100, truncated=True)
    read, why = cut.reads(datetime(2026, 8, 27, tzinfo=UTC))
    assert read is False
    assert "100-record cap" in why


def test_a_bounded_side_cannot_vouch_for_an_undated_record():
    read, why = PAYPAL_READ.reads(None)
    assert read is False
    assert "no timestamp" in why


# --- the property ------------------------------------------------------

def test_unpaid_order_inside_coverage_is_a_finding():
    result = match(SPEC, [order("#1003")], [], COMPLETE_LEDGER, PAYPAL_READ)
    [row] = [r for r in result.rows() if r["status"] == "unmatched_left"]
    assert row["counterpart_read"] is True
    assert row["caveat"] is None
    assert result.summary["unmatched_left_counterpart_unread"] == 0
    assert result.summary["caveats"] == []


def test_unpaid_order_after_the_horizon_is_not_a_finding():
    late = (HORIZON + timedelta(hours=2)).isoformat()
    result = match(SPEC, [order("#1007", at=late)], [], COMPLETE_LEDGER, PAYPAL_READ)
    [row] = [r for r in result.rows() if r["status"] == "unmatched_left"]
    assert row["counterpart_read"] is False
    assert "ledger horizon" in row["caveat"]
    s = result.summary
    assert s["unmatched_left"] == 1
    assert s["unmatched_left_counterpart_unread"] == 1
    assert "not findings" in s["caveats"][0]


def test_a_truncated_payment_side_voids_every_unpaid_order():
    """The stated property: if the rail truncated, no unmatched order is
    a finding, however its date falls."""
    cut = cov("paypal.transactions.list", records=100, cap=100, truncated=True)
    result = match(
        SPEC, [order("#1003"), order("#1005")],
        [payment("#1004", at="2026-07-10T00:00:00Z")],
        COMPLETE_LEDGER, cut,
    )
    unmatched = [r for r in result.rows() if r["status"] == "unmatched_left"]
    assert len(unmatched) == 2
    assert all(r["counterpart_read"] is False for r in unmatched)
    assert result.summary["unmatched_left_counterpart_unread"] == 2
    assert "cap" in result.summary["caveats"][0]


def test_a_truncated_ledger_voids_every_orphan_payment():
    """Symmetric: a payment with no order is not a finding when the
    order side stopped short."""
    cut = cov("shopify.orders.list", records=100, cap=100, truncated=True)
    result = match(SPEC, [], [payment("#9999")], cut, PAYPAL_READ)
    [row] = [r for r in result.rows() if r["status"] == "unmatched_right"]
    assert row["counterpart_read"] is False
    assert result.summary["unmatched_right_counterpart_unread"] == 1


def test_matched_rows_need_no_caveat():
    result = match(SPEC, [order("#1004")], [payment("#1004")], COMPLETE_LEDGER, PAYPAL_READ)
    [row] = result.rows()
    assert row["counterpart_read"] is True and row["caveat"] is None


def test_summary_states_what_each_side_read():
    result = match(SPEC, [], [], COMPLETE_LEDGER, PAYPAL_READ)
    coverage = result.summary["coverage"]
    assert coverage["left"]["span_start"] is None  # the ledger is read in full
    assert coverage["right"]["span_start"] == START.isoformat()
    assert coverage["right"]["horizon"] == HORIZON.isoformat()
    assert coverage["right"]["truncated"] is False


def test_without_coverage_nothing_is_claimed():
    """A bare match over rows knows nothing about what was read. It must
    not call an unmatched record a finding, nor deny it is one."""
    result = match(SPEC, [order("#1003")], [])
    [row] = result.rows()
    assert row["counterpart_read"] is None
    assert result.summary["coverage"] is None
    assert result.summary["caveats"] == []


# --- what the fetch reports --------------------------------------------

class _Client:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._payload

    async def aclose(self) -> None:
        pass


def _fake(monkeypatch, connector, payload: dict[str, Any]) -> None:
    monkeypatch.setattr(
        connector, "context_factory",
        lambda conn, creds: ConnectorContext(client=_Client(payload), credentials=creds),
    )


def _creds(name: str, fields: tuple[str, ...]) -> dict[str, str]:
    return {f: "" for f in fields}


def _charges(n: int, more: bool) -> dict[str, Any]:
    data = [
        {"id": f"ch_{i}", "amount": 100, "currency": "usd", "status": "succeeded",
         "created": 1786532776 + i}
        for i in range(n)
    ]
    return {"data": data, "has_more": more}


async def test_cap_reached_with_more_upstream_is_truncated(monkeypatch):
    _fake(monkeypatch, stripe, _charges(3, more=True))
    rows, c = await fetch_side(ResourceRef(connector="stripe", resource="charges"), _creds, 3)
    assert len(rows) == 3
    assert c.truncated is True


async def test_cap_reached_exactly_with_nothing_more_is_complete(monkeypatch):
    _fake(monkeypatch, stripe, _charges(3, more=False))
    _, c = await fetch_side(ResourceRef(connector="stripe", resource="charges"), _creds, 3)
    assert c.truncated is False
    assert c.bounded is False


async def test_paypal_reports_its_span_and_the_horizon_it_clamped_to(monkeypatch):
    _fake(monkeypatch, paypal, {
        "transaction_details": [], "page": 1, "total_pages": 1,
        "last_refreshed_datetime": HORIZON.isoformat(),
    })
    ref = ResourceRef(connector="paypal", resource="transactions")
    _, c = await fetch_side(
        ref, _creds, 10, {"start_date": START, "end_date": HORIZON + timedelta(hours=3)}
    )
    assert c.span_start == START
    assert c.span_end == HORIZON
    assert c.horizon == HORIZON


async def test_paypal_without_a_window_still_states_its_default(monkeypatch):
    """No lookback means PayPal's own 30 days — a bound the caller never
    chose. Coverage must report it rather than read as unbounded."""
    _fake(monkeypatch, paypal, {
        "transaction_details": [], "page": 1, "total_pages": 1,
        "last_refreshed_datetime": "2099-01-01T00:00:00Z",
    })
    _, c = await fetch_side(ResourceRef(connector="paypal", resource="transactions"), _creds, 10)
    assert c.bounded
    assert c.span_end - c.span_start <= timedelta(days=31)
    assert c.horizon is None  # PayPal was current; nothing clamped


# --- every surface carries it -----------------------------------------

async def test_the_mcp_tool_returns_coverage_and_caveats(monkeypatch):
    cut = cov("paypal.transactions.list", records=1, cap=1, truncated=True)

    async def fake_run(spec, **kw):
        return match(spec, [order("#1003")], [], COMPLETE_LEDGER, cut)

    monkeypatch.setattr(mcp, "run", fake_run)
    out = await mcp._make_tool_fn(SPEC)(limit=1, status="unmatched_left")
    assert out["summary"]["coverage"]["right"]["truncated"] is True
    assert out["summary"]["unmatched_left_counterpart_unread"] == 1
    assert out["rows"][0]["counterpart_read"] is False


def test_the_sql_view_can_separate_findings_from_unread():
    late = (HORIZON + timedelta(hours=2)).isoformat()
    result = match(
        SPEC, [order("#1003"), order("#1007", at=late)], [], COMPLETE_LEDGER, PAYPAL_READ
    )
    con = duckdb.connect()
    view = materialize_view(con, SPEC, result)
    unpaid = con.execute(
        f"SELECT left_record->>'name' FROM {view} "
        "WHERE status = 'unmatched_left' AND counterpart_read"
    ).fetchall()
    assert unpaid == [("#1003",)]


async def test_the_runner_hands_coverage_to_the_result(monkeypatch):
    async def side(ref, resolver, cap, overrides):
        c = PAYPAL_READ if ref.connector == "paypal" else COMPLETE_LEDGER
        return [], c

    monkeypatch.setattr(runner, "fetch_side", side)
    result = await runner.run(SPEC)
    assert result.right_coverage is PAYPAL_READ
    assert result.left_coverage is COMPLETE_LEDGER
