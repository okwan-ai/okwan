"""Combined explanations and an evidence trail (§3.2, roadmap E3 and E4).

E3: a processor's fee rule sums several columns (discount, chargebacks,
adjustments) into one explanation, and when no single declared cause
accounts for a difference, the smallest combination that does is used:
a fee and a refund on the same order. E4: every verdict says how it was
reached, in references and amounts, and the stored, trimmed rows carry
it without ever carrying a record.

What would fail silently: a fee plus a refund reported as an unexplained
break; a cause pointing the wrong way used to explain; an explanation
that silently picks a combination when one cause alone fits; a "not on
this rail" that cannot show the rail looked; evidence that leaks a
customer record into storage.
"""
from __future__ import annotations

import json

import duckdb
import okwan_paypal.connector  # noqa: F401  (registers the connector)
import okwan_shopify.connector  # noqa: F401
import okwan_stripe.connector  # noqa: F401
import pytest
from okwan_recon import (
    AmountRef,
    ExactRef,
    Explains,
    Fuzzy,
    GroupSum,
    Reconciliation,
    ResourceRef,
    SettlementLag,
    declarations,
)
from okwan_recon.across import fold
from okwan_recon.coverage import Coverage
from okwan_recon.emitters.duckdb_view import COLUMNS, materialize_view
from okwan_recon.engine import match
from pydantic import ValidationError


def cov(source: str) -> Coverage:
    return Coverage(source=source, records=3, cap=100, truncated=False)


# ── E3: a fee rule over several columns ─────────────────────────────

def batch(bid: str, gross: int, fees: int, chargebacks: int | None = None) -> dict:
    row = {"batch_id": bid, "closed_on": "2026-11-02", "gross_minor": gross,
           "fees_minor": fees, "currency": "USD", "mid": "MID-1"}
    if chargebacks is not None:
        row["chargebacks_minor"] = chargebacks
    return row


def deposit(fitid: str, amount: int, day: str = "2026-11-03") -> dict:
    return {"fitid": fitid, "posted_on": day, "amount_minor": amount, "currency": "USD"}


FEE_RULE = Explains(paths=("fees_minor", "chargebacks_minor", "adjustments_minor"),
                    side="left", label="processor_fees")


def gross_spec(*rules) -> Reconciliation:
    """Batches reported gross, deposits net: the fee rule explains the gap."""
    return Reconciliation(
        name="gross_deposits_fixture",
        left=ResourceRef(connector="processor", resource="batches"),
        right=ResourceRef(connector="bank", resource="deposits"),
        keys=[ExactRef(left="batch_id", right="fitid")],
        amount=AmountRef(left="gross_minor", right="amount_minor"),
        explains=list(rules),
    )


def test_a_fee_rule_sums_its_columns_and_a_missing_one_counts_nothing():
    rows = [batch("B1", 100_000, 2_900, chargebacks=5_000)]
    result = match(gross_spec(FEE_RULE), rows, [deposit("B1", 92_100)])
    (row,) = result.rows()
    assert row["status"] == "matched_explained" and row["explained_by"] == "processor_fees"
    (part,) = row["evidence"]["explanation"]
    assert part["amount_minor"] == 7_900
    assert part["paths"] == ["fees_minor", "chargebacks_minor", "adjustments_minor"]


def test_a_fee_rule_that_does_not_add_up_stays_a_break():
    result = match(gross_spec(FEE_RULE), [batch("B1", 100_000, 2_900)], [deposit("B1", 92_100)])
    (row,) = result.rows()
    assert row["status"] == "matched_discrepant" and "explanation" not in row["evidence"]


def test_an_explanation_names_exactly_one_source():
    with pytest.raises(ValidationError):
        Explains(path="fee_minor", paths=("a", "b"))
    with pytest.raises(ValidationError):
        Explains()


# ── E3: causes combined ─────────────────────────────────────────────

def order(name: str, total: int, refunded: int = 0) -> dict:
    return {"name": name, "currency": "USD", "created_at": "2026-11-02T15:00:00Z",
            "total_price_minor": total, "total_refunded_minor": refunded}


def charge(ref: str, net: int, fee: int) -> dict:
    return {"order_ref": ref, "currency": "usd", "net_minor": net, "fee_minor": fee}


FEE = Explains(path="fee_minor", side="right", label="rail_fee")
REFUND = Explains(path="total_refunded_minor", side="left", label="refund")


def orders_spec() -> Reconciliation:
    return Reconciliation(
        name="orders_net_fixture",
        left=ResourceRef(connector="shop", resource="orders"),
        right=ResourceRef(connector="rail", resource="charges"),
        keys=[ExactRef(left="name", right="order_ref")],
        amount=AmountRef(left="total_price_minor", right="net_minor"),
        explains=[FEE, REFUND],
    )


def test_a_fee_and_a_refund_on_one_order_explain_it_together():
    """10,000 ordered, 2,000 refunded, a 300 fee: the rail nets 7,700.
    Neither cause alone is 2,300; both together are."""
    result = match(orders_spec(), [order("#1009", 10_000, refunded=2_000)], [charge("#1009", 7_700, 300)])
    (row,) = result.rows()
    assert row["status"] == "matched_explained" and row["explained_by"] == "rail_fee+refund"
    assert [(p["label"], p["amount_minor"]) for p in row["evidence"]["explanation"]] == [
        ("rail_fee", 300), ("refund", 2_000)]


def test_one_cause_that_fits_is_preferred_to_a_combination():
    result = match(orders_spec(), [order("#1010", 10_000, refunded=0)], [charge("#1010", 9_700, 300)])
    assert result.rows()[0]["explained_by"] == "rail_fee"


def test_a_cause_pointing_the_wrong_way_explains_nothing():
    """The rail netted more than the order: a fee cannot account for that."""
    result = match(orders_spec(), [order("#1011", 10_000)], [charge("#1011", 10_300, 300)])
    (row,) = result.rows()
    assert row["status"] == "matched_discrepant" and row["discrepancy"] == -300


def test_the_shipped_declarations_still_explain_as_before():
    """Additive: a Stripe fee on the shipped declaration is explained by
    the one rule, exactly as before."""
    stripe = declarations.shopify_stripe
    ledger = [{"name": "#1002", "currency": "USD", "created_at": "2026-08-27T15:00:00Z",
               "total_price_minor": 99_900, "total_received_minor": 99_900,
               "total_refunded_minor": 0, "net_payment_minor": 99_900}]
    charges = [{"order_ref": "#1002", "currency": "usd", "created": 1_787_842_860, "amount": 99_900,
                "amount_refunded": 0, "fee_minor": 2_927, "net_minor": 96_973}]
    (row,) = match(stripe, ledger, charges).rows()
    assert row["explained_by"] == "rail_fee"


# ── E4: evidence on every verdict ───────────────────────────────────

def test_a_reference_match_says_which_key_and_what_was_compared():
    result = match(orders_spec(), [order("#1012", 5_000)], [charge("#1012", 5_000, 0)])
    ev = result.rows()[0]["evidence"]
    assert ev["rule"] == "exact_ref"
    assert ev["key"] == {"left": "name", "right": "order_ref", "value": "#1012"}
    assert ev["compared"] == {"left_path": "total_price_minor", "right_path": "net_minor",
                              "left_minor": 5_000, "right_minor": 5_000, "currency": "USD"}


def test_a_lagged_match_states_its_business_days():
    spec = Reconciliation(
        name="lag_evidence_fixture",
        left=ResourceRef(connector="processor", resource="batches"),
        right=ResourceRef(connector="bank", resource="deposits"),
        keys=[Fuzzy(amount="gross_minor", currency="currency", amount_right="amount_minor",
                    timestamp_left="closed_on", timestamp_right="posted_on",
                    lag=SettlementLag(max_days=2))],
    )
    ev = match(spec, [batch("B1", 100, 0)], [deposit("D1", 100, day="2026-11-04")]).rows()[0]["evidence"]
    assert ev["rule"] == "fuzzy"
    assert ev["lag"]["business_days"] == 2 and ev["lag"]["calendar"] == "us_bank"


def test_a_grouped_deposit_lists_its_batches_by_reference_and_amount():
    spec = Reconciliation(
        name="group_evidence_fixture",
        left=ResourceRef(connector="processor", resource="batches"),
        right=ResourceRef(connector="bank", resource="deposits"),
        keys=[GroupSum(many="left", amount="gross_minor", amount_one="amount_minor",
                       timestamp_many="closed_on", timestamp_one="posted_on", share=("mid",),
                       ref_many="batch_id", ref_one="fitid", lag=SettlementLag(max_days=2))],
    )
    result = match(spec, [batch("B1", 100, 0), batch("B2", 250, 0)], [deposit("D1", 350)])
    (row,) = result.rows()
    ev = row["evidence"]
    assert ev["members"] == [{"ref": "B1", "amount_minor": 100}, {"ref": "B2", "amount_minor": 250}]
    assert ev["share"] == {"mid": "MID-1"}
    assert row["left"]["batch_id"] == "B1+B2"
    # The stored row names both sides by reference.
    (stored,) = result.trimmed_rows("batch_id", "fitid")
    assert stored["left_ref"] == "B1+B2" and stored["right_ref"] == "D1" and stored["members"] == 2


def test_a_miss_shows_the_other_side_looked():
    result = match(orders_spec(), [order("#1013", 5_000)], [], cov("shop.orders.list"), cov("rail.charges.list"))
    (row,) = result.rows()
    assert row["status"] == "unmatched_left"
    assert row["evidence"]["counterpart"]["source"] == "rail.charges.list"


def test_stored_rows_carry_evidence_and_never_a_record():
    result = match(orders_spec(), [order("#1014", 10_000, refunded=2_000)],
                   [charge("#1014", 7_700, 300), charge("#9999", 50, 0)],
                   cov("shop.orders.list"), cov("rail.charges.list"))
    stored = result.trimmed_rows("name", "order_ref")
    text = json.dumps(stored)
    assert all("evidence" in r for r in stored)
    # References and amounts only: no row carries a record (`left`,
    # `right`), and no record-only field travels inside the evidence.
    assert all("left" not in r and "right" not in r for r in stored)
    assert "created_at" not in text


def test_the_sql_view_has_an_evidence_column():
    con = duckdb.connect()
    spec = orders_spec()
    materialize_view(con, spec, match(spec, [order("#1015", 5_000)], [charge("#1015", 5_000, 0)]))
    assert ("evidence", "JSON") in COLUMNS
    value = con.execute(
        "SELECT json_extract_string(evidence, '$.key.value') FROM recon.orders_net_fixture"
    ).fetchone()[0]
    assert value == "#1015"


def test_the_check_carries_each_rails_evidence():
    """The fold's stored rows: a matched rail states its key and amounts,
    a rail without the order shows what it read."""
    paypal, stripe = declarations.shopify_paypal, declarations.shopify_stripe
    pp_rail, st_rail = declarations.rails.rails
    ledger = [{"name": "#1002", "currency": "USD", "created_at": "2026-08-27T15:00:00Z",
               "total_price_minor": 99_900, "total_received_minor": 99_900,
               "total_refunded_minor": 0, "net_payment_minor": 99_900}]
    charges = [{"order_ref": "#1002", "currency": "usd", "created": 1_787_842_860, "amount": 99_900,
                "amount_refunded": 0, "fee_minor": 2_927, "net_minor": 96_973}]
    led = cov("shopify.orders.list")
    result = fold(declarations.rails, ledger, [
        (paypal, pp_rail, match(paypal, ledger, [], led, cov("paypal.transactions.list"))),
        (stripe, st_rail, match(stripe, ledger, charges, led, cov("stripe.charges.list"))),
    ], led)
    (stored,) = result.trimmed_rows()
    by_rail = {r["rail"]: r for r in stored["rails"]}
    assert by_rail["stripe"]["evidence"]["key"]["value"] == "#1002"
    assert by_rail["stripe"]["evidence"]["explanation"][0]["label"] == "rail_fee"
    assert by_rail["paypal"]["status"] == "unmatched"
    assert by_rail["paypal"]["evidence"]["counterpart"]["source"] == "paypal.transactions.list"
