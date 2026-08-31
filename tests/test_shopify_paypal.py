"""shopify_paypal — one order ledger against a second payment rail.

The declaration exists to answer a question no single rail can: which
orders were paid, on which rail, and where the figures disagree. These
tests cover what would fail silently — a join key that resolves to the
wrong field, a fee discrepancy reported as agreement, and equal-amount
orders paired by guesswork.

Rows are fixtures rather than live calls: the match engine is a pure
function over rows, and pinning the transport here would test PayPal's
uptime instead of the declaration.
"""
from __future__ import annotations

from typing import Any

import okwan_paypal.connector  # noqa: F401  (registers the connector)
import okwan_shopify.connector  # noqa: F401
from okwan_recon import declarations  # noqa: F401  (registers declarations)
from okwan_recon.declaration import ExactRef, Fuzzy
from okwan_recon.engine import match
from okwan_recon.registry import all_reconciliations

SPEC = next(r for r in all_reconciliations() if r.name == "shopify_paypal")

PAYPAL_FEE_MINOR = -1000  # 10.00 on a 450.00 sale, exaggerated for clarity


def order(name: str, minor: int, refunded: int = 0, at: str = "2026-08-27T15:00:00Z") -> dict[str, Any]:
    return {
        "id": f"gid://shopify/Order/{abs(hash(name)) % 10**13}",
        "name": name,
        "currency": "USD",
        "created_at": at,
        "total_price_minor": minor,
        "total_received_minor": minor,
        "total_refunded_minor": refunded,
        "net_payment_minor": minor - refunded,
        "is_reconcilable": True,
    }


def payment(
    invoice: str | None,
    minor: int,
    fee: int = 0,
    at: str = "2026-08-27T15:02:00Z",
    code: str = "T0006",
) -> dict[str, Any]:
    return {
        "transaction_id": f"PP{abs(hash((invoice, minor, at))) % 10**15}",
        "event_code": code,
        "status": "S",
        "currency": "USD",
        "amount_minor": minor,
        "fee_minor": fee,
        "initiated_at": at,
        "invoice_id": invoice,
        "custom_field": invoice,
        "net_minor": minor + fee,
        "is_payment": True,
    }


# --- declaration shape -------------------------------------------------

def test_declaration_is_registered():
    assert SPEC.name == "shopify_paypal"
    assert SPEC.left.qualified == "shopify.orders.list"
    assert SPEC.right.qualified == "paypal.transactions.list"


def test_both_sides_are_read_only():
    """Structural, not a convention. A declaration over a write
    operation is refused rather than trusted not to be run."""
    SPEC.validate_against_registry()


def test_join_is_order_name_to_invoice_id():
    """Shopify exposes no invoice field; the merchant order name is what
    a Shopify-through-PayPal checkout carries into `invoice_id`."""
    exact = next(k for k in SPEC.keys if isinstance(k, ExactRef))
    assert (exact.left, exact.right) == ("name", "invoice_id")


def test_fuzzy_is_the_fallback_not_the_first_rule():
    """Amount inside a window is the weakest signal a rail offers.
    Running it before the reference would pair on coincidence."""
    assert isinstance(SPEC.keys[0], ExactRef)
    assert isinstance(SPEC.keys[-1], Fuzzy)


def test_comparison_is_net_of_fees():
    """Gross agrees with the order total on every row and hides the fee.
    Net surfaces it as a discrepancy the ledger can account for."""
    assert SPEC.resolved_amount.left == "net_payment_minor"
    assert SPEC.resolved_amount.right == "net_minor"


# --- matching ----------------------------------------------------------

def test_reference_match_pairs_by_order_name():
    result = match(SPEC, [order("#1002", 99900)], [payment("#1002", 99900)])
    assert len(result.matched) == 1
    pair = result.matched[0]
    assert pair.rule == "exact_ref"
    assert pair.agrees is True
    assert pair.discrepancy_minor == 0


def test_fee_shows_as_a_discrepancy_not_agreement():
    """The merchant is owed 999.00 and receives 989.00. A reconciliation
    reporting that as clean has answered the wrong question."""
    result = match(
        SPEC,
        [order("#1002", 99900)],
        [payment("#1002", 99900, fee=PAYPAL_FEE_MINOR)],
    )
    pair = result.matched[0]
    assert pair.agrees is False
    assert pair.discrepancy_minor == -PAYPAL_FEE_MINOR
    assert pair.is_unexplained is True


def test_order_with_no_payment_is_unmatched_left():
    """#1003 was placed but never collected on this rail — the finding a
    merchant running two rails cannot get from either one alone."""
    result = match(SPEC, [order("#1003", 15000)], [])
    assert [o["name"] for o in result.unmatched_left] == ["#1003"]
    assert result.matched == []


def test_payment_with_no_order_is_unmatched_right():
    result = match(SPEC, [], [payment("#9999", 5000)])
    assert len(result.unmatched_right) == 1


def test_reference_survives_equal_amounts():
    """#1004 and #1005 are both 450.00. With a reference they are
    distinguishable, and the engine must use it rather than fall through
    to the amount rule."""
    result = match(
        SPEC,
        [order("#1004", 45000), order("#1005", 45000)],
        [payment("#1005", 45000), payment("#1004", 45000)],
    )
    assert len(result.matched) == 2
    assert result.ambiguous == []
    for pair in result.matched:
        assert pair.left["name"] == pair.right["invoice_id"]


def test_equal_amounts_without_a_reference_report_ambiguous():
    """Two candidates the data cannot separate. Picking one would be
    invention, and a wrong pairing double-counts revenue."""
    result = match(
        SPEC,
        [order("#1004", 45000)],
        [payment(None, 45000, at="2026-08-27T16:00:00Z"),
         payment(None, 45000, at="2026-08-27T17:00:00Z")],
    )
    assert len(result.ambiguous) == 1
    assert len(result.ambiguous[0].candidates) == 2
    assert result.matched == []


def test_ambiguous_candidates_are_not_consumed():
    """An unresolved record must not silently remove its candidates from
    consideration for anything else."""
    result = match(
        SPEC,
        [order("#1004", 45000)],
        [payment(None, 45000, at="2026-08-27T16:00:00Z"),
         payment(None, 45000, at="2026-08-27T17:00:00Z")],
    )
    assert result.ambiguous[0].left["name"] == "#1004"


# --- summary -----------------------------------------------------------

def test_summary_separates_agreement_from_matching():
    """A matched pair whose figures disagree is not a clean match.
    Collapsing the two hides the break inside a success count."""
    result = match(
        SPEC,
        [order("#1002", 99900), order("#1006", 7500)],
        [payment("#1002", 99900), payment("#1006", 7500, fee=PAYPAL_FEE_MINOR)],
    )
    s = result.summary
    assert s["matched"] == 2
    assert s["matched_in_agreement"] == 1
    assert s["matched_with_discrepancy"] == 1


def test_net_unexplained_is_the_actionable_figure():
    result = match(
        SPEC,
        [order("#1006", 7500)],
        [payment("#1006", 7500, fee=PAYPAL_FEE_MINOR)],
    )
    assert result.summary["net_unexplained_minor"] == -PAYPAL_FEE_MINOR


def test_unpaid_orders_lower_the_match_rate():
    """Match rate is over orders, not over payments — the merchant's
    question is which orders were collected."""
    result = match(
        SPEC,
        [order("#1002", 99900), order("#1003", 15000)],
        [payment("#1002", 99900)],
    )
    assert result.summary["match_rate"] == 0.5
