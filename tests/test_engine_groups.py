"""Group matching and settlement-lag windows (§3.2, roadmap E1 and E2).

Batch to deposit is many-to-one; split tender and a refund row are
one-to-many; a deposit lands some business days after its batch, never
before it. What would fail silently:
- a weekend's batches matched to nothing because no single batch equals
  the deposit;
- a deposit claimed by whichever batches happen to sum, when two
  groupings fit (a coin flip presented as a fact);
- batches from two merchants summed into one deposit;
- a deposit matched to a batch closed after it;
- a Labor Day deposit missed because the window counted calendar days;
- the second card of a split order reported as a payment with no order.

Pure engine: fixture rows through `match`; the fold test uses the real
shipped declarations with one rule swapped.
"""
from __future__ import annotations

from datetime import date

import okwan_paypal.connector  # noqa: F401  (registers the connector)
import okwan_shopify.connector  # noqa: F401
import okwan_stripe.connector  # noqa: F401
import pytest
from okwan_recon import (
    AmountRef,
    ExactRef,
    Explains,
    Fuzzy,
    GroupRef,
    GroupSum,
    Reconciliation,
    ResourceRef,
    SettlementLag,
    declarations,
)
from okwan_recon.across import fold
from okwan_recon.calendar import (
    as_date,
    business_days_after,
    is_business_day,
    us_bank_holidays,
)
from okwan_recon.coverage import Coverage
from okwan_recon.engine import match
from pydantic import ValidationError

# ── the calendar ────────────────────────────────────────────────────

def test_fed_holidays_2026_with_a_saturday_holiday_not_moved():
    days = us_bank_holidays(2026)
    assert date(2026, 9, 7) in days        # Labor Day
    assert date(2026, 11, 26) in days      # Thanksgiving
    assert date(2026, 6, 19) in days       # Juneteenth, a Friday
    # July 4 2026 is a Saturday: the Fed does not close on Friday the 3rd.
    assert date(2026, 7, 3) not in days and date(2026, 7, 4) not in days


def test_a_sunday_holiday_is_observed_on_monday():
    assert date(2027, 7, 5) in us_bank_holidays(2027)  # July 4 2027 is a Sunday
    assert not is_business_day(date(2027, 7, 5))


def test_business_days_count_forward_and_skip_weekends_and_holidays():
    friday, tuesday = date(2026, 9, 4), date(2026, 9, 8)  # Monday is Labor Day
    assert business_days_after(friday, tuesday) == 1
    assert business_days_after(date(2026, 9, 5), tuesday) == 1  # a Saturday batch
    assert business_days_after(friday, friday) == 0
    assert business_days_after(tuesday, friday) is None  # settlement runs one way


def test_a_bare_date_is_that_day_and_a_timestamp_is_the_merchants_day():
    assert as_date("2026-11-06", "America/Los_Angeles") == date(2026, 11, 6)
    # 3 a.m. UTC on the 7th is 7 p.m. on the 6th in Los Angeles.
    assert as_date("2026-11-07T03:00:00Z", "America/Los_Angeles") == date(2026, 11, 6)


def test_a_lag_needs_a_real_zone_and_an_ordered_range():
    with pytest.raises(ValidationError):
        SettlementLag(timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        SettlementLag(min_days=3, max_days=1)


# ── batches against deposits (GroupSum) ─────────────────────────────

def batch(bid: str, day: str, net: int, mid: str = "MID-1") -> dict:
    return {"batch_id": bid, "closed_on": day, "net_minor": net, "currency": "USD", "mid": mid}


def deposit(fitid: str, day: str, amount: int) -> dict:
    return {"fitid": fitid, "posted_on": day, "amount_minor": amount, "currency": "USD"}


def deposits_spec(max_days: int = 2, **group) -> Reconciliation:
    return Reconciliation(
        name="batches_deposits_fixture",
        left=ResourceRef(connector="processor", resource="batches"),
        right=ResourceRef(connector="bank", resource="deposits"),
        keys=[GroupSum(
            many="left", amount="net_minor", amount_one="amount_minor",
            timestamp_many="closed_on", timestamp_one="posted_on", share=("mid",),
            lag=SettlementLag(max_days=max_days), **group,
        )],
    )


def rows_by_status(result) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in result.rows():
        out.setdefault(r["status"], []).append(r)
    return out


def test_a_weekends_batches_are_one_deposit_after_the_holiday():
    """Friday, Saturday and Sunday's batches land as one deposit on the
    Tuesday after Labor Day: T+1 in business days, four calendar days."""
    batches = [batch("B1", "2026-09-04", 10_000), batch("B2", "2026-09-05", 20_000),
               batch("B3", "2026-09-06", 5_000)]
    result = match(deposits_spec(), batches, [deposit("D1", "2026-09-08", 35_000)])
    rows = rows_by_status(result)
    assert list(rows) == ["matched"]
    (row,) = rows["matched"]
    assert row["rule"] == "group_sum" and row["members"] == 3 and row["discrepancy"] == 0
    assert row["confidence"] == 0.75  # a sum without a reference: stated weaker
    assert row["left"]["member_count"] == 3 and row["left"]["net_minor"] == 35_000
    assert [m["batch_id"] for m in row["left"]["members"]] == ["B1", "B2", "B3"]


def test_each_deposit_claims_its_own_batch_in_order():
    batches = [batch("B1", "2026-11-02", 10_000), batch("B2", "2026-11-03", 25_000)]
    deps = [deposit("D1", "2026-11-03", 10_000), deposit("D2", "2026-11-04", 25_000)]
    result = match(deposits_spec(), batches, deps)
    pairs = {p.right["fitid"]: p.left["batch_id"] for p in result.matched}
    assert pairs == {"D1": "B1", "D2": "B2"}


def test_two_groupings_that_both_fit_are_ambiguous_and_claim_nothing():
    batches = [batch("B1", "2026-11-02", 100), batch("B2", "2026-11-02", 200),
               batch("B3", "2026-11-02", 300)]
    result = match(deposits_spec(), batches, [deposit("D1", "2026-11-03", 300)])
    (amb,) = result.ambiguous
    assert amb.side == "right" and amb.rule == "group_sum"
    assert sorted(len(a) for a in amb.alternatives) == [1, 2]  # {B3} or {B1, B2}
    assert "2 groupings" in amb.reason
    assert not result.matched
    assert {b["batch_id"] for b in result.unmatched_left} == {"B1", "B2", "B3"}
    row = rows_by_status(result)["ambiguous"][0]
    assert row["left"] is None and row["right"]["fitid"] == "D1"


def test_batches_of_different_merchants_are_never_summed_together():
    batches = [batch("B1", "2026-11-02", 100, mid="MID-1"), batch("B2", "2026-11-02", 200, mid="MID-2")]
    result = match(deposits_spec(), batches, [deposit("D1", "2026-11-03", 300)])
    assert not result.matched and not result.ambiguous
    assert len(result.unmatched_right) == 1 and len(result.unmatched_left) == 2


def test_a_deposit_before_its_batch_never_matches():
    result = match(deposits_spec(), [batch("B1", "2026-11-03", 100)], [deposit("D1", "2026-11-02", 100)])
    assert not result.matched


def test_too_many_candidates_is_reported_not_guessed():
    batches = [batch(f"B{i}", "2026-11-02", 100) for i in range(30)]
    result = match(deposits_spec(), batches, [deposit("D1", "2026-11-03", 100)])
    (amb,) = result.ambiguous
    assert "more than 24 candidates" in amb.reason
    assert not result.matched


def test_the_lag_is_counted_in_business_days():
    """Friday to Wednesday after Labor Day is T+2: outside a one-day lag,
    inside a two-day one."""
    batches, deps = [batch("B1", "2026-09-04", 100)], [deposit("D1", "2026-09-09", 100)]
    assert not match(deposits_spec(max_days=1), batches, deps).matched
    assert match(deposits_spec(max_days=2), batches, deps).matched


# ── a 1:1 match with a lag (Fuzzy) ──────────────────────────────────

def fuzzy_spec(**rule) -> Reconciliation:
    return Reconciliation(
        name="fuzzy_lag_fixture",
        left=ResourceRef(connector="processor", resource="batches"),
        right=ResourceRef(connector="bank", resource="deposits"),
        keys=[Fuzzy(amount="net_minor", currency="currency", amount_right="amount_minor",
                    timestamp_left="closed_on", timestamp_right="posted_on", **rule)],
    )


def test_a_symmetric_window_matches_backwards_and_a_lag_does_not():
    """The bug the lag fixes: a deposit the day before the batch."""
    batches, deps = [batch("B1", "2026-11-03", 100)], [deposit("D1", "2026-11-02", 100)]
    assert match(fuzzy_spec(window="7d"), batches, deps).matched
    assert not match(fuzzy_spec(lag=SettlementLag(max_days=2)), batches, deps).matched


def test_a_fuzzy_lag_matches_across_a_holiday_weekend():
    result = match(fuzzy_spec(lag=SettlementLag(max_days=1)),
                   [batch("B1", "2026-09-04", 100)], [deposit("D1", "2026-09-08", 100)])
    (pair,) = result.matched
    assert pair.rule == "fuzzy"


# ── one order, several records (GroupRef) ───────────────────────────

def order(name: str, total: int, refunded: int = 0) -> dict:
    return {"name": name, "currency": "USD", "created_at": "2026-11-02T15:00:00Z",
            "total_price_minor": total, "total_refunded_minor": refunded}


def charge(ref: str, amount: int) -> dict:
    return {"order_ref": ref, "currency": "usd", "amount": amount}


def orders_spec(rule) -> Reconciliation:
    return Reconciliation(
        name="orders_charges_fixture",
        left=ResourceRef(connector="shop", resource="orders"),
        right=ResourceRef(connector="rail", resource="charges"),
        keys=[rule],
        amount=AmountRef(left="total_price_minor", right="amount"),
        explains=[Explains(path="total_refunded_minor", side="left", label="refund")],
    )


def test_split_tender_on_one_rail_is_one_order():
    rows = [charge("#1005", 6_000), charge("#1005", 4_000)]
    grouped = match(orders_spec(GroupRef(left="name", right="order_ref")), [order("#1005", 10_000)], rows)
    (pair,) = grouped.matched
    assert pair.members is not None and len(pair.members) == 2
    assert pair.discrepancy_minor == 0 and not grouped.unmatched_right
    assert pair.right["order_ref"] == "#1005" and pair.right["amount"] == 10_000

    # The same rows through a one-to-one reference join: the second card
    # becomes a payment with no order, and the order a 4,000 break.
    exact = match(orders_spec(ExactRef(left="name", right="order_ref")), [order("#1005", 10_000)], rows)
    assert len(exact.unmatched_right) == 1 and exact.matched[0].discrepancy_minor == 4_000


def test_a_refund_row_is_summed_and_explained():
    rows = [charge("#1006", 10_000), charge("#1006", -2_500)]
    result = match(orders_spec(GroupRef(left="name", right="order_ref")),
                   [order("#1006", 10_000, refunded=2_500)], rows)
    (row,) = result.rows()
    assert row["status"] == "matched_explained" and row["explained_by"] == "refund"
    assert row["discrepancy"] == 2_500


def test_more_records_than_an_order_could_have_are_ambiguous():
    rows = [charge("#1007", 100) for _ in range(3)]
    result = match(orders_spec(GroupRef(left="name", right="order_ref", max_members=2)),
                   [order("#1007", 300)], rows)
    (amb,) = result.ambiguous
    assert "3 records carry this reference" in amb.reason
    assert not result.matched


def test_one_record_under_a_reference_is_an_ordinary_pair():
    result = match(orders_spec(GroupRef(left="name", right="order_ref")), [order("#1008", 500)], [charge("#1008", 500)])
    (pair,) = result.matched
    assert pair.members is None and pair.right["amount"] == 500


# ── the fold reads a grouped rail ───────────────────────────────────

def test_the_check_counts_a_split_on_one_rail_as_collected_once():
    """Two Stripe charges for one order, joined by GroupRef: the fold sees
    the order's full total collected once, not a short collection."""
    stripe = declarations.shopify_stripe.model_copy(
        update={"keys": [GroupRef(left="name", right="order_ref")]}
    )
    paypal = declarations.shopify_paypal
    pp_rail, st_rail = declarations.rails.rails
    ledger = [{"name": "#1005", "currency": "USD", "created_at": "2026-08-27T15:00:00Z",
               "total_price_minor": 10_000, "total_received_minor": 10_000,
               "total_refunded_minor": 0, "net_payment_minor": 10_000}]
    charges = [{"order_ref": "#1005", "currency": "usd", "created": 1_787_842_860, "amount": a,
                "amount_refunded": 0, "fee_minor": 0, "net_minor": a} for a in (6_000, 4_000)]
    cov = lambda s: Coverage(source=s, records=1, cap=100, truncated=False)
    led = cov("shopify.orders.list")
    result = fold(declarations.rails, ledger, [
        (paypal, pp_rail, match(paypal, ledger, [], led, cov("paypal.transactions.list"))),
        (stripe, st_rail, match(stripe, ledger, charges, led, cov("stripe.charges.list"))),
    ], led)
    (verdict,) = result.orders
    assert verdict.outcome == "collected"
    assert verdict.collected_minor == 10_000
