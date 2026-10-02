"""reconcile_across_rails — was each order paid once?

Each rail declaration answers whether an order was paid on that rail.
An order taken on both Stripe and PayPal is a clean, fee-explained match
on each, so the double collection exists only in the fold. These tests
cover what would fail silently: a duplicate read as a split, a split
read as a duplicate, "uncollected" asserted while a rail never looked,
and a positive match discarded because another rail could not read.

Rows are fixtures through the real member declarations and the real
match engine; only transport is absent.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import duckdb
import okwan_paypal.connector  # noqa: F401  (registers the connector)
import okwan_shopify.connector  # noqa: F401
import okwan_stripe.connector  # noqa: F401
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from okwan_recon import across, declarations
from okwan_recon.across import OUTCOMES, AcrossRails, Rail, fold
from okwan_recon.coverage import Coverage
from okwan_recon.emitters import mcp
from okwan_recon.emitters.duckdb_view import materialize_across_view
from okwan_recon.emitters.rest import build_router
from okwan_recon.engine import match
from okwan_recon.registry import register_across
from pydantic import ValidationError

SPEC = declarations.rails
PAYPAL = declarations.shopify_paypal
STRIPE = declarations.shopify_stripe
PP_RAIL, ST_RAIL = SPEC.rails

AT = "2026-08-27T15:00:00Z"
EPOCH = int(datetime(2026, 8, 27, 15, 1, tzinfo=UTC).timestamp())


def order(name: str, total: int, currency: str = "USD") -> dict[str, Any]:
    return {
        "name": name, "currency": currency, "created_at": AT,
        "total_price_minor": total, "total_received_minor": total,
        "total_refunded_minor": 0, "net_payment_minor": total,
    }


def pp(ref: str | None, gross: int, fee: int = -300) -> dict[str, Any]:
    return {
        "invoice_id": ref, "currency": "USD", "initiated_at": AT,
        "amount_minor": gross, "fee_minor": fee, "net_minor": gross + fee,
    }


def st(ref: str | None, gross: int, fee: int = 300, currency: str = "usd") -> dict[str, Any]:
    return {
        "order_ref": ref, "currency": currency, "created": EPOCH,
        "amount": gross, "amount_refunded": 0, "fee_minor": fee,
        "net_minor": gross - fee,
    }


def cov(source: str, truncated: bool = False) -> Coverage:
    return Coverage(source=source, records=1, cap=100, truncated=truncated)


LEDGER = cov("shopify.orders.list")


def run(
    ledger: list[dict], paypal: list[dict], stripe: list[dict],
    paypal_cut: bool = False, stripe_cut: bool = False,
):
    """The fold over real matches, as run_across does it minus fetch."""
    pp_cov = cov("paypal.transactions.list", truncated=paypal_cut)
    st_cov = cov("stripe.charges.list", truncated=stripe_cut)
    return fold(SPEC, ledger, [
        (PAYPAL, PP_RAIL, match(PAYPAL, ledger, paypal, LEDGER, pp_cov)),
        (STRIPE, ST_RAIL, match(STRIPE, ledger, stripe, LEDGER, st_cov)),
    ], LEDGER)


def outcome(result, name: str) -> str:
    return next(v.outcome for v in result.orders if v.order["name"] == name)


# --- outcomes ----------------------------------------------------------

def test_one_rail_is_collected_and_names_it():
    result = run([order("#1003", 15000)], [], [st("#1003", 15000)])
    [v] = result.orders
    assert v.outcome == "collected"
    assert v.collected_on == ["stripe"]


def test_full_total_on_two_rails_is_collected_twice():
    result = run([order("#1002", 99900)], [pp("#1002", 99900)], [st("#1002", 99900)])
    [v] = result.orders
    assert v.outcome == "collected_twice"
    assert v.collected_on == ["paypal", "stripe"]
    assert v.collected_minor == 199800
    s = result.summary
    assert s["collected_twice"] == 1
    assert s["collected_twice_minor"] == 99900
    assert s["overcollected_minor"] == 99900


def test_amounts_summing_to_the_total_are_a_split_not_a_duplicate():
    """Normal: part on a gift card through PayPal, the rest by card."""
    result = run([order("#1010", 10000)], [pp("#1010", 4000)], [st("#1010", 6000)])
    assert outcome(result, "#1010") == "split_tender"
    assert result.summary["collected_twice"] == 0


def test_neither_duplicate_nor_split_is_reported_not_arbitrated():
    result = run([order("#1011", 10000)], [pp("#1011", 10000)], [st("#1011", 4000)])
    [v] = result.orders
    assert v.outcome == "collected_inconsistent"
    assert "paypal 10000" in v.reason and "stripe 4000" in v.reason


def test_cross_currency_collections_are_inconsistent_not_summed():
    result = run(
        [order("#1012", 10000)], [pp("#1012", 10000)], [st("#1012", 10000, currency="eur")]
    )
    [v] = result.orders
    assert v.outcome == "collected_inconsistent"
    assert "currencies" in v.reason


def test_no_rail_with_every_rail_read_is_uncollected():
    result = run([order("#1013", 5000)], [], [])
    assert outcome(result, "#1013") == "uncollected"


def test_uncollected_requires_every_rail_to_have_read():
    """Strict. Stripe stopped at its cap, so its absence proves nothing."""
    result = run([order("#1013", 5000)], [], [], stripe_cut=True)
    [v] = result.orders
    assert v.outcome == "unverifiable"
    assert v.reason.startswith("stripe:")
    assert result.summary["uncollected"] == 0


def test_a_match_is_positive_evidence_whatever_another_rail_missed():
    """Collected on PayPal with Stripe truncated is still collected. The
    truncated rail is named, because a second collection on it cannot be
    ruled out."""
    result = run([order("#1005", 45000)], [pp("#1005", 45000)], [], stripe_cut=True)
    [v] = result.orders
    assert v.outcome == "collected"
    assert v.unverified_rails == ["stripe"]


def test_ambiguous_with_no_match_elsewhere_is_unverifiable():
    """Candidates exist on the rail; calling it uncollected would deny
    payments the rail plainly holds."""
    ledger = [order("#1014", 29900)]
    stripe = [st(None, 29900, ) | {"created": EPOCH + i} for i in range(3)]
    result = run(ledger, [], stripe)
    [v] = result.orders
    assert v.outcome == "unverifiable"
    assert "3 equal candidates" in v.reason


# --- the near-total tolerance -----------------------------------------

def test_within_tolerance_still_counts_as_the_full_total():
    """1% by default. $999.00 and $995.00 is a duplicate, not a mystery."""
    result = run([order("#1002", 99900)], [pp("#1002", 99900)], [st("#1002", 99500)])
    assert outcome(result, "#1002") == "collected_twice"


def test_beyond_tolerance_is_not_a_duplicate():
    result = run([order("#1002", 99900)], [pp("#1002", 99900)], [st("#1002", 97000)])
    assert outcome(result, "#1002") == "collected_inconsistent"


def test_tolerance_is_declared_not_hardcoded():
    strict = SPEC.model_copy(update={"tolerance_bps": 0})
    ledger = [order("#1002", 99900)]
    result = fold(strict, ledger, [
        (PAYPAL, PP_RAIL, match(PAYPAL, ledger, [pp("#1002", 99900)])),
        (STRIPE, ST_RAIL, match(STRIPE, ledger, [st("#1002", 99500)])),
    ])
    assert result.orders[0].outcome == "collected_inconsistent"


# --- what each verdict carries ----------------------------------------

def test_each_rail_carries_its_own_discrepancy():
    result = run([order("#1002", 99900)], [pp("#1002", 99900, fee=-3536)],
                 [st("#1002", 99900, fee=2927)])
    by_rail = {r.rail: r for r in result.orders[0].rails}
    assert by_rail["paypal"].discrepancy_minor == 3536
    assert by_rail["stripe"].discrepancy_minor == 2927
    assert by_rail["paypal"].explained_by == by_rail["stripe"].explained_by == "rail_fee"


def test_rail_records_with_no_order_are_counted_per_rail():
    result = run([order("#1003", 15000)], [pp("#9999", 100)], [st("#1003", 15000)])
    assert result.summary["rails"]["shopify_paypal"]["unmatched_right"] == 1
    assert result.summary["rails"]["shopify_stripe"]["unmatched_right"] == 0


def test_summary_counts_every_outcome_even_at_zero():
    s = run([], [], []).summary
    assert all(s[o] == 0 for o in OUTCOMES)


# --- the shipped declaration -------------------------------------------

def test_shipped_fold_compares_gross_against_the_order_total():
    """Not total_received: the ledger only sees its own checkout."""
    assert SPEC.ledger_total == "total_price_minor"
    assert [(r.reconciliation, r.collected) for r in SPEC.rails] == [
        ("shopify_paypal", "amount_minor"), ("shopify_stripe", "amount"),
    ]


def test_shipped_fold_validates():
    SPEC.validate_against_registry()


def test_one_rail_is_not_a_fold():
    with pytest.raises(ValidationError):
        AcrossRails(name="x", rails=[PP_RAIL], ledger_total="total_price_minor")


def test_unknown_member_is_refused():
    bad = AcrossRails(
        name="x", ledger_total="total_price_minor",
        rails=[PP_RAIL, Rail(reconciliation="nope", collected="amount")],
    )
    with pytest.raises(ValueError, match="unknown reconciliation"):
        bad.validate_against_registry()


def test_members_reading_different_ledgers_are_refused():
    """"The same order" means nothing across two different ledger reads."""
    other = PAYPAL.model_copy(update={
        "name": "paypal_paid_only",
        "left": PAYPAL.left.model_copy(update={"params": {"financial_status": "paid"}}),
    })
    from okwan_recon.registry import _REGISTRY

    _REGISTRY[other.name] = other
    try:
        bad = AcrossRails(
            name="x", ledger_total="total_price_minor",
            rails=[Rail(reconciliation=other.name, collected="amount_minor"), ST_RAIL],
        )
        with pytest.raises(ValueError, match="reads shopify.orders.list"):
            bad.validate_against_registry()
    finally:
        del _REGISTRY[other.name]


def test_a_collected_path_the_rail_does_not_produce_is_refused():
    bad = AcrossRails(
        name="x", ledger_total="total_price_minor",
        rails=[PP_RAIL, Rail(reconciliation="shopify_stripe", collected="gross")],
    )
    with pytest.raises(ValueError, match="no field 'gross'"):
        bad.validate_against_registry()


def test_a_fold_cannot_shadow_a_reconciliation_view():
    from okwan_recon.registry import _REGISTRY

    _REGISTRY["across_dup"] = PAYPAL
    try:
        with pytest.raises(ValueError, match="already names"):
            register_across(AcrossRails(
                name="dup", ledger_total="total_price_minor", rails=[PP_RAIL, ST_RAIL]
            ))
    finally:
        del _REGISTRY["across_dup"]


# --- run ---------------------------------------------------------------

async def test_the_ledger_is_fetched_once_for_every_rail(monkeypatch):
    """Each member judges the same order rows, not separate reads that
    could differ between calls."""
    calls: list[str] = []

    async def side(ref, resolver, cap, overrides):
        calls.append(ref.connector)
        rows = {"shopify": [order("#1002", 99900)], "paypal": [pp("#1002", 99900)],
                "stripe": [st("#1002", 99900)]}[ref.connector]
        return rows, cov(ref.qualified)

    monkeypatch.setattr(across, "fetch_side", side)
    result = await across.run_across(SPEC)
    assert calls == ["shopify", "paypal", "stripe"]
    assert result.orders[0].outcome == "collected_twice"


# --- one definition, three surfaces ------------------------------------

def test_metadata_derives_from_the_declaration():
    meta = mcp.across_metadata(SPEC)
    assert meta["tool_name"] == "reconcile_across_rails"
    assert meta["path"] == "/v1/reconciliations/across/rails"
    assert meta["view"] == "recon.across_rails"
    assert meta["rails"] == ["shopify_paypal", "shopify_stripe"]
    assert meta["outcomes"] == list(OUTCOMES)


async def test_the_mcp_tool_filters_by_outcome(monkeypatch):
    async def fake(spec, **kw):
        return run([order("#1002", 99900), order("#1003", 15000)],
                   [pp("#1002", 99900)], [st("#1002", 99900), st("#1003", 15000)])

    monkeypatch.setattr(mcp, "run_across", fake)
    out = await mcp._make_across_tool_fn(SPEC)(limit=100, outcome="collected_twice")
    assert [r["order"]["name"] for r in out["rows"]] == ["#1002"]
    assert out["summary"]["collected"] == 1


def test_the_mcp_server_registers_the_tool():
    server = mcp.build_server()
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "reconcile_across_rails" in names


def test_the_rest_route_is_generated():
    client = TestClient(_app())
    listed = client.get("/v1/reconciliations").json()
    assert [a["name"] for a in listed["across"]] == ["rails"]
    assert client.get("/v1/reconciliations/across/nope").status_code == 404
    bad = client.get("/v1/reconciliations/across/rails", params={"outcome": "paid"})
    assert bad.status_code == 422


def test_the_sql_view_answers_which_orders_were_taken_twice():
    result = run(
        [order("#1002", 99900), order("#1003", 15000)],
        [pp("#1002", 99900)], [st("#1002", 99900), st("#1003", 15000)],
    )
    con = duckdb.connect()
    view = materialize_across_view(con, SPEC, result)
    got = con.execute(
        f"SELECT order_record->>'name', collected_minor - order_total_minor "
        f"FROM {view} WHERE outcome = 'collected_twice'"
    ).fetchall()
    assert got == [("#1002", 99900)]


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(build_router())
    return app
