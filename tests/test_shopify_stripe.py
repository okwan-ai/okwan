"""shopify_stripe — the order ledger against the other US rail.

Alongside shopify_paypal this traces each order to the rail that
collected it. These tests cover what would fail silently — a join key
that resolves to the wrong field, a fee whose sign convention keeps it
from explaining anything, a refund netted twice, and a fallback that
can never fire or that fires on coincidence.

Rows are fixtures rather than live calls: the match engine is a pure
function over rows, and pinning the transport here would test Stripe's
uptime instead of the declaration.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import okwan_shopify.connector  # noqa: F401  (registers the connector)
import okwan_stripe.connector  # noqa: F401
from okwan_core import ConnectorContext
from okwan_recon import declarations  # noqa: F401  (registers declarations)
from okwan_recon.declaration import ExactRef, Explains, Fuzzy
from okwan_recon.emitters.mcp import tool_metadata
from okwan_recon.engine import match
from okwan_recon.fetch import fetch_rows
from okwan_recon.registry import all_reconciliations
from okwan_stripe.connector import stripe

SPEC = next(r for r in all_reconciliations() if r.name == "shopify_stripe")
PAYPAL = next(r for r in all_reconciliations() if r.name == "shopify_paypal")

ORDER_AT = "2026-08-27T15:00:00Z"
CHARGED_AT = int(datetime(2026, 8, 27, 15, 2, tzinfo=UTC).timestamp())


def order(
    name: str, minor: int, refunded: int = 0, at: str = ORDER_AT
) -> dict[str, Any]:
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


def charge(
    order_ref: str | None,
    minor: int,
    fee: int | None = 0,
    refunded: int = 0,
    at: int = CHARGED_AT,
) -> dict[str, Any]:
    """Shaped like a dumped `Charge`: lowercase currency, Unix `created`,
    fee positive as Stripe reports it."""
    return {
        "id": f"ch_{abs(hash((order_ref, minor, at))) % 10**15}",
        "amount": minor,
        "currency": "usd",
        "status": "succeeded",
        "created": at,
        "refunded": refunded == minor and minor > 0,
        "amount_refunded": refunded,
        "order_ref": order_ref,
        "fee_minor": fee,
        "net_minor": minor - refunded - (fee or 0),
    }


# --- declaration shape -------------------------------------------------

def test_declaration_is_registered():
    assert SPEC.name == "shopify_stripe"
    assert SPEC.left.qualified == "shopify.orders.list"
    assert SPEC.right.qualified == "stripe.charges.list"


def test_both_sides_are_read_only():
    """Structural, not a convention. A declaration over a write
    operation is refused rather than trusted not to be run."""
    SPEC.validate_against_registry()


def test_join_is_order_name_to_order_ref():
    """Shopify Payments writes the order name into charge metadata; the
    Stripe schema lifts it to `order_ref`."""
    exact = next(k for k in SPEC.keys if isinstance(k, ExactRef))
    assert (exact.left, exact.right) == ("name", "order_ref")


def test_fuzzy_is_the_fallback_not_the_first_rule():
    assert isinstance(SPEC.keys[0], ExactRef)
    assert isinstance(SPEC.keys[-1], Fuzzy)


def test_fuzzy_compares_gross_to_gross():
    """Stripe's net never equals an order total, so net-to-net would
    leave the fallback dead on every charge that carried a fee."""
    fuzzy = next(k for k in SPEC.keys if isinstance(k, Fuzzy))
    assert (fuzzy.amount, fuzzy.right_amount) == ("total_received_minor", "amount")
    assert fuzzy.timestamp_right == "created"


def test_comparison_is_net_of_fees_and_refunds():
    assert SPEC.resolved_amount.left == "net_payment_minor"
    assert SPEC.resolved_amount.right == "net_minor"


def test_the_fee_is_the_only_declared_cause():
    """Refunds net on both sides, so a refund explanation would only let
    an unrelated figure of the same size explain a real break."""
    assert [(e.path, e.side, e.label) for e in SPEC.explains] == [
        ("fee_minor", "right", "rail_fee")
    ]


def test_one_definition_produces_the_tool_view_and_route():
    meta = tool_metadata(SPEC)
    assert meta["tool_name"] == "reconcile_shopify_stripe"
    assert SPEC.view_name == "recon.shopify_stripe"
    assert SPEC.rest_path == "/v1/reconciliations/shopify_stripe"


# --- matching ----------------------------------------------------------

def test_reference_match_pairs_by_order_name():
    result = match(SPEC, [order("#1002", 99900)], [charge("#1002", 99900)])
    assert len(result.matched) == 1
    pair = result.matched[0]
    assert pair.rule == "exact_ref"
    assert pair.agrees is True


def test_reference_match_ignores_currency_case():
    """Stripe reports `usd`, Shopify `USD`. A case difference is not a
    currency difference, and must not void the comparison."""
    result = match(SPEC, [order("#1002", 99900)], [charge("#1002", 99900, fee=2927)])
    assert result.matched[0].discrepancy_minor == 2927


def test_positive_stripe_fee_is_a_discrepancy_with_a_known_cause():
    """$999.00 owed, $969.73 received. Stripe states the $29.27 fee
    positive; it must explain the gap exactly as PayPal's negative fee
    does."""
    result = match(SPEC, [order("#1002", 99900)], [charge("#1002", 99900, fee=2927)])
    pair = result.matched[0]
    assert pair.right["fee_minor"] > 0
    assert pair.discrepancy_minor == 2927
    assert pair.explained_by == "rail_fee"
    assert pair.is_unexplained is False


def test_fee_explains_on_magnitude_under_either_sign_convention():
    """The same rule object explains a Stripe-signed and a PayPal-signed
    fee of the same size. Only magnitude decides."""
    rule = Explains(path="fee_minor", side="right", label="rail_fee")
    assert rule.accounts_for(2927, 2927) is True
    assert rule.accounts_for(-2927, 2927) is True
    paypal_rule = next(e for e in PAYPAL.explains if e.path == "fee_minor")
    assert paypal_rule.model_dump() == rule.model_dump()


def test_a_shortfall_beyond_the_fee_stays_unexplained():
    """$5.00 short on top of the fee. The fee accounts for part of the
    gap, not the whole of it."""
    result = match(SPEC, [order("#1006", 7500)], [charge("#1006", 7000, fee=233)])
    pair = result.matched[0]
    assert pair.discrepancy_minor == 733
    assert pair.explained_by is None
    assert pair.is_unexplained is True


def test_a_refund_nets_on_both_sides_and_leaves_only_the_fee():
    """Shopify and Stripe both subtract the refund, so it cancels. If
    either side stopped netting it, the gap would be the refund plus the
    fee and nothing could explain it."""
    result = match(
        SPEC,
        [order("#1007", 50000, refunded=20000)],
        [charge("#1007", 50000, fee=1480, refunded=20000)],
    )
    pair = result.matched[0]
    assert pair.discrepancy_minor == 1480
    assert pair.explained_by == "rail_fee"


def test_a_missing_fee_is_not_read_as_a_free_charge():
    """An unexpanded balance transaction leaves `fee_minor` None. The
    pair still compares; nothing claims to explain it."""
    result = match(SPEC, [order("#1002", 99900)], [charge("#1002", 99900, fee=None)])
    pair = result.matched[0]
    assert pair.agrees is True
    assert pair.explained_by is None


def test_order_with_no_charge_is_unmatched_left():
    result = match(SPEC, [order("#1005", 45000)], [])
    assert [o["name"] for o in result.unmatched_left] == ["#1005"]


def test_charge_with_no_order_is_unmatched_right():
    result = match(SPEC, [], [charge("#9999", 5000)])
    assert len(result.unmatched_right) == 1


# --- failed attempts ---------------------------------------------------

def test_declaration_reads_succeeded_charges_only():
    assert SPEC.right.params == {"status": "succeeded"}


def test_unfiltered_a_failed_attempt_can_take_the_reference_join():
    """Why the filter exists. The exact rule takes the first charge
    carrying the reference, so list order alone decides — and a failed
    attempt listed first wins, leaving the real payment an orphan."""
    failed = {**charge("#1002", 99900), "id": "ch_failed", "status": "failed"}
    paid = {**charge("#1002", 99900), "id": "ch_paid"}
    result = match(SPEC, [order("#1002", 99900)], [failed, paid])
    assert result.matched[0].right["id"] == "ch_failed"


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._payload

    async def aclose(self) -> None:
        pass


async def test_a_failed_attempt_never_reaches_the_join(monkeypatch):
    """Through the real fetch path, with the failure listed first: the
    declaration's filter removes it before the engine sees it."""
    base = {
        "amount": 99900, "currency": "usd", "created": CHARGED_AT,
        "metadata": {"order_id": "#1002"},
        "balance_transaction": {"fee": 2927},
    }
    payload = {
        "data": [
            {**base, "id": "ch_failed", "status": "failed", "balance_transaction": None},
            {**base, "id": "ch_paid", "status": "succeeded"},
        ],
        "has_more": False,
    }
    monkeypatch.setattr(
        stripe, "context_factory",
        lambda conn, creds: ConnectorContext(client=_FakeClient(payload), credentials=creds),
    )
    rows = await fetch_rows(SPEC.right, lambda name, fields: {f: "" for f in fields})
    assert [r["id"] for r in rows] == ["ch_paid"]

    result = match(SPEC, [order("#1002", 99900)], rows)
    assert result.matched[0].right["id"] == "ch_paid"
    assert result.matched[0].explained_by == "rail_fee"
    assert result.unmatched_right == []


# --- fuzzy fallback ----------------------------------------------------

def test_unreferenced_charge_pairs_on_gross_despite_its_fee():
    """The reason the fallback reads gross: a fee makes net differ from
    the order total on every real charge."""
    result = match(SPEC, [order("#1003", 15000)], [charge(None, 15000, fee=465)])
    pair = result.matched[0]
    assert pair.rule == "fuzzy"
    assert pair.discrepancy_minor == 465
    assert pair.explained_by == "rail_fee"


def test_unix_created_is_read_against_iso_created_at():
    """Stripe states time as epoch seconds, Shopify as ISO strings. A
    charge outside the window must not pair however the two are written."""
    late = int(datetime(2026, 9, 10, tzinfo=UTC).timestamp())
    result = match(SPEC, [order("#1003", 15000)], [charge(None, 15000, at=late)])
    assert result.matched == []
    assert len(result.unmatched_left) == 1
    assert len(result.unmatched_right) == 1


def test_equal_unreferenced_charges_report_ambiguous():
    """Four $299.00 charges with no reference against one $299.00 order.
    Nothing separates them; choosing one would be invention."""
    legacy = [charge(None, 29900, fee=897, at=CHARGED_AT + i * 60) for i in range(4)]
    result = match(SPEC, [order("#1001", 29900)], legacy)
    assert result.matched == []
    assert len(result.ambiguous) == 1
    assert len(result.ambiguous[0].candidates) == 4
    assert len(result.unmatched_right) == 4


def test_a_refund_remainder_does_not_pair_with_a_small_charge():
    """$2,423.00 received less $2,124.00 refunded leaves $299.00. Pairing
    that remainder with an unrelated $299.00 charge is coincidence, which
    is what a gross-to-gross fallback refuses."""
    result = match(
        SPEC, [order("#1001", 242300, refunded=212400)], [charge(None, 29900, fee=897)]
    )
    assert result.matched == []
    assert result.ambiguous == []


def test_reference_runs_before_amount():
    """With a reference, equal amounts are distinguishable."""
    result = match(
        SPEC,
        [order("#1004", 45000), order("#1005", 45000)],
        [charge("#1005", 45000), charge("#1004", 45000)],
    )
    assert result.ambiguous == []
    for pair in result.matched:
        assert pair.left["name"] == pair.right["order_ref"]


# --- summary -----------------------------------------------------------

def test_fees_drop_out_of_the_unexplained_figure():
    result = match(
        SPEC,
        [order("#1002", 99900), order("#1006", 7500)],
        [charge("#1002", 99900, fee=2927), charge("#1006", 7000, fee=233)],
    )
    s = result.summary
    assert s["matched_with_explained_discrepancy"] == 1
    assert s["matched_with_unexplained_discrepancy"] == 1
    assert s["net_unexplained_minor"] == 733


def test_orders_paid_on_another_rail_lower_the_match_rate():
    """Match rate is over orders. An order collected on PayPal reads
    unmatched here; only the two declarations together say it was paid."""
    result = match(
        SPEC,
        [order("#1002", 99900), order("#1005", 45000)],
        [charge("#1002", 99900)],
    )
    assert result.summary["match_rate"] == 0.5
