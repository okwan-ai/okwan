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
    assert ev["members"] == [{"ref": "B1", "amount_minor": 100, "when": "2026-11-02"},
                             {"ref": "B2", "amount_minor": 250, "when": "2026-11-02"}]
    # The deposit has one date; the group has none of its own, its members do.
    assert ev["dated"] == {"left": None, "right": "2026-11-03"}
    assert ev["share"] == {"mid": "MID-1"}
    assert row["left"]["batch_id"] == "B1+B2"
    # The stored row names both sides by reference.
    (stored,) = result.trimmed_rows("batch_id", "fitid")
    assert stored["left_ref"] == "B1+B2" and stored["right_ref"] == "D1" and stored["members"] == 2


def _batches_deposits() -> Reconciliation:
    return Reconciliation(
        name="unpaired_figures_fixture",
        left=ResourceRef(connector="processor", resource="batches"),
        right=ResourceRef(connector="bank", resource="deposits"),
        keys=[GroupSum(many="left", amount="gross_minor", amount_one="amount_minor",
                       timestamp_many="closed_on", timestamp_one="posted_on", share=("mid",),
                       ref_many="batch_id", ref_one="fitid", lag=SettlementLag(max_days=2))],
    )


def test_a_record_with_no_counterpart_is_reported_with_its_amount_and_date():
    """A stored row named a batch that was never paid by its reference and
    nothing else, and a deposit from a statement with no reference column
    by nothing at all: not something a person can act on. Each now carries
    the amount, the currency and the date the check read it by."""
    unreferenced = {"posted_on": "2026-11-06", "amount_minor": 31_240, "currency": "usd"}
    result = match(_batches_deposits(), [batch("B1", 100, 0), batch("B9", 94_406, 0)],
                   [deposit("D1", 100), unreferenced])
    rows = {r["status"]: r for r in result.trimmed_rows("batch_id", "fitid")}
    assert (rows["unmatched_left"]["left_ref"], rows["unmatched_left"]["evidence"]) == (
        "B9", {"record": {"amount_minor": 94_406, "currency": "USD", "when": "2026-11-02"}})
    assert (rows["unmatched_right"]["right_ref"], rows["unmatched_right"]["evidence"]) == (
        None, {"record": {"amount_minor": 31_240, "currency": "USD", "when": "2026-11-06"}})
    # Figures and a date, never the record: nothing else of either one travels.
    unpaired = json.dumps([rows["unmatched_left"], rows["unmatched_right"]])
    assert all(word not in unpaired for word in ("posted_on", "closed_on", "gross_minor", "fees_minor", "MID-1"))
    # What the other side read is still said beside it.
    covered = match(_batches_deposits(), [batch("B9", 94_406, 0)], [], cov("processor.batches.list"),
                    cov("bank.deposits.list"))
    (row,) = covered.rows()
    assert row["evidence"]["counterpart"]["source"] == "bank.deposits.list"
    assert row["evidence"]["record"] == {"amount_minor": 94_406, "currency": "USD", "when": "2026-11-02"}


def test_a_pair_says_when_each_side_happened():
    result = match(_batches_deposits(), [batch("B1", 100, 0)], [deposit("D1", 100, day="2026-11-04")])
    (row,) = result.rows()
    assert row["members"] is None and row["evidence"]["dated"] == {"left": "2026-11-02", "right": "2026-11-04"}


def test_a_date_is_kept_as_the_day_or_the_instant_and_nothing_else():
    from datetime import UTC, date, datetime

    from okwan_recon.engine import _when

    assert _when("2026-10-13") == _when(date(2026, 10, 13)) == "2026-10-13"       # a file's day stays a day
    assert _when("2026-10-13T14:03:22-04:00") == "2026-10-13T18:03:22+00:00"      # an instant, in UTC
    assert _when(datetime(2026, 10, 13, 9, 30, tzinfo=UTC)) == "2026-10-13T09:30:00+00:00"
    assert _when(1_791_900_000) == "2026-10-13T14:00:00+00:00"                    # a rail's epoch seconds
    for odd in (None, True, "", "next Tuesday", "9999-99-99", 10**20, float("nan"), {"at": 1}):
        assert _when(odd) is None, odd


@pytest.mark.parametrize("stamp", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_a_date_at_the_edge_of_the_calendar_does_not_take_the_check_down(stamp):
    """Attacker's pass: such a timestamp parses and compares, and cannot be
    moved to UTC. Dating a record did that outside its guard, so one row,
    in a customer's own table, made the whole check a 500 on every
    surface and left no stored run. It is now kept as no date."""
    spec = Reconciliation(
        name="edge_of_the_calendar_fixture",
        left=ResourceRef(connector="shop", resource="payments"),
        right=ResourceRef(connector="rail", resource="charges"),
        keys=[ExactRef(left="reference", right="order_ref"),
              Fuzzy(amount="amount", currency="currency", timestamp_left="created_at", timestamp_right="created_at",
                    window="3d")],
    )
    paid = {"reference": "#1", "amount": 5_000, "currency": "USD", "created_at": stamp}
    alone = {"reference": "#2", "amount": 700, "currency": "USD", "created_at": stamp}
    result = match(spec, [paid, alone], [{"order_ref": "#1", "amount": 5_000, "currency": "USD",
                                          "created_at": "2026-11-02T10:00:00+00:00"}])
    rows = {r["status"]: r for r in result.trimmed_rows("reference", "order_ref")}
    assert rows["matched"]["evidence"]["dated"] == {"left": None, "right": "2026-11-02T10:00:00+00:00"}
    assert rows["unmatched_left"]["evidence"]["record"] == {"amount_minor": 700, "currency": "USD", "when": None}
    assert len(result.rows()) == 2


def test_only_a_code_a_whole_amount_and_a_date_are_kept_of_a_record():
    """Attacker's pass: the currency of a record with no counterpart was
    kept as whatever its field held, any type, any length. Where that
    field is a customer's own column it carried a name, an email and card
    digits into the stored run, REST, the SQL view and MCP. Before this
    change such a row kept the record's reference and nothing else."""
    from okwan_recon.engine import _code, _figure

    note = "refund to Jane Doe <jane.doe@example.com>, card 4242 4242 4242 4242"
    for odd in (note, {"code": "USD", "payer": "Jane Doe"}, ["USD", "jane.doe@example.com"], "x" * 100_000,
                "US", "USDT", "U$D", 840, None, True, ""):
        assert _code(odd) is None, odd
    assert (_code("usd"), _code(" GHS ")) == ("USD", "GHS")
    for odd in (12.5, "1200", True, None, [1200], 10**15, -(10**15), 10**400):
        assert _figure(odd) is None, odd
    assert (_figure(0), _figure(-455), _figure(10**15 - 1)) == (0, -455, 10**15 - 1)
    # And on every check there is, on either side: nothing of the note is kept.
    from okwan_recon import registry

    seen = 0
    for spec in registry.all_reconciliations():
        amount = spec.resolved_amount
        if amount is None:
            continue
        left_when, right_when = spec.timestamp_paths
        for side, cur, amt, when in (("left", amount.currency, amount.left, left_when),
                                     ("right", amount.right_currency, amount.right_path, right_when)):
            record = {}
            for path, value in ((cur, note), (amt, note), (when, note)):
                if path and "." not in path:
                    record[path] = value
            result = match(spec, [record] if side == "left" else [], [record] if side == "right" else [])
            kept = json.dumps(result.trimmed_rows("reference", "reference"))
            assert "JANE" not in kept.upper() and "4242" not in kept, (spec.name, side)
            seen += 1
    assert seen >= 16


def test_a_check_that_names_no_amount_or_date_reports_a_miss_as_it_did():
    spec = Reconciliation(
        name="reference_only_fixture",
        left=ResourceRef(connector="shop", resource="orders"),
        right=ResourceRef(connector="rail", resource="charges"),
        keys=[ExactRef(left="name", right="order_ref")],
    )
    (row,) = match(spec, [{"name": "#1", "total": 5}], []).rows()
    assert row["status"] == "unmatched_left" and row["evidence"] is None


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
