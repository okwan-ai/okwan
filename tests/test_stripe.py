"""Stripe connector — the fields a reconciliation joins and nets on.

A charge as Stripe returns it carries no flat order reference, keeps its
fee on a separate balance transaction, and reports refunds as an amount
beside the gross. The schema lifts all three into columns. These tests
cover the parts that would fail silently: a missing join key that reads
as an unmatched order, a fee that never arrives, and a net that counts
money the merchant does not have.
"""
from __future__ import annotations

from typing import Any

import okwan_stripe.connector  # noqa: F401  (registers the connector)
import pytest
from okwan_query import find
from okwan_stripe.connector import list_charges
from okwan_stripe.schemas import Charge, ListChargesIn

CHARGE = {
    "id": "ch_3PqR8sLkdIwHu7ix0abc1234",
    "object": "charge",
    "amount": 10000,
    "amount_refunded": 0,
    "currency": "usd",
    "status": "succeeded",
    "customer": "cus_QhX1a2b3c4d5e6",
    "description": "Order #1001",
    "created": 1759312800,
    "refunded": False,
    "metadata": {"order_id": "#1001"},
    "balance_transaction": {
        "id": "txn_3PqR8sLkdIwHu7ix0def5678",
        "object": "balance_transaction",
        "amount": 10000,
        "fee": 320,
        "net": 9680,
        "currency": "usd",
    },
}


def _charge(**overrides: Any) -> dict[str, Any]:
    return {**CHARGE, **overrides}


# --- order reference ---------------------------------------------------

@pytest.mark.parametrize("key", ["order_id", "order", "order_name"])
def test_order_ref_lifts_from_any_known_metadata_key(key):
    t = Charge.model_validate(_charge(metadata={key: "#1001"}))
    assert t.order_ref == "#1001"


def test_order_id_wins_over_the_fallback_keys():
    meta = {"order_name": "#name", "order": "#order", "order_id": "#id"}
    assert Charge.model_validate(_charge(metadata=meta)).order_ref == "#id"


def test_empty_order_id_falls_through_to_the_next_key():
    """An empty string is not a join key."""
    meta = {"order_id": "", "order_name": "#1001"}
    assert Charge.model_validate(_charge(metadata=meta)).order_ref == "#1001"


@pytest.mark.parametrize("meta", [{}, None, {"campaign": "spring"}])
def test_no_order_metadata_is_none(meta):
    """A charge with no merchant reference must surface as unjoinable,
    not join on some other metadata value."""
    assert Charge.model_validate(_charge(metadata=meta)).order_ref is None


def test_metadata_absent_entirely_is_none():
    row = {k: v for k, v in CHARGE.items() if k != "metadata"}
    assert Charge.model_validate(row).order_ref is None


# --- fee ---------------------------------------------------------------

def test_fee_lifts_from_the_expanded_balance_transaction():
    t = Charge.model_validate(CHARGE)
    assert t.fee_minor == 320


def test_unexpanded_balance_transaction_leaves_fee_none():
    """Without expansion Stripe returns the ID only. None, not zero, so a
    missing fee cannot pass for a free charge."""
    t = Charge.model_validate(_charge(balance_transaction="txn_3PqR8sLkdIwHu7ix0def5678"))
    assert t.fee_minor is None


def test_pending_charge_without_balance_transaction_leaves_fee_none():
    t = Charge.model_validate(_charge(balance_transaction=None, status="pending"))
    assert t.fee_minor is None


# --- net ---------------------------------------------------------------

def test_net_subtracts_the_fee():
    """Stripe reports the fee positive, so net subtracts where PayPal adds."""
    t = Charge.model_validate(CHARGE)
    assert t.net_minor == 9680
    assert t.net_major == pytest.approx(96.80)


def test_net_subtracts_refunds_and_fee():
    t = Charge.model_validate(_charge(amount_refunded=2500))
    assert t.net_minor == 10000 - 2500 - 320


def test_fully_refunded_charge_nets_negative_by_the_fee():
    """Stripe keeps its fee on a refund, so the merchant is out the fee."""
    t = Charge.model_validate(_charge(amount_refunded=10000, refunded=True))
    assert t.net_minor == -320


def test_net_without_a_fee_subtracts_refunds_only():
    t = Charge.model_validate(_charge(balance_transaction=None, amount_refunded=1000))
    assert t.net_minor == 9000


def test_zero_decimal_currency_net_major_is_whole_units():
    bt = {**CHARGE["balance_transaction"], "fee": 36, "currency": "jpy"}
    t = Charge.model_validate(
        _charge(amount=1000, currency="jpy", balance_transaction=bt)
    )
    assert t.net_minor == 964
    assert t.net_major == 964


# --- list operation ----------------------------------------------------

class _FakeClient:
    """Records the params requested and replays one canned page."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, params or {}))
        return self._payload


class _FakeContext:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client
        self.credentials: dict[str, str] = {}


async def test_list_charges_expands_the_balance_transaction():
    """Without the expansion every fee is None and every row breaks by
    Stripe's cut."""
    client = _FakeClient({"data": [CHARGE], "has_more": False})
    page = await list_charges(_FakeContext(client), ListChargesIn())
    path, params = client.calls[0]
    assert path == "/charges"
    assert params["expand[]"] == "data.balance_transaction"
    assert page.items[0].fee_minor == 320
    assert page.items[0].order_ref == "#1001"


async def test_list_charges_pages_from_the_last_id():
    client = _FakeClient({"data": [CHARGE], "has_more": True})
    page = await list_charges(_FakeContext(client), ListChargesIn())
    assert page.has_more
    assert page.next_cursor == CHARGE["id"]


async def test_status_filter_drops_other_statuses():
    failed = _charge(id="ch_failed", status="failed")
    client = _FakeClient({"data": [failed, CHARGE], "has_more": False})
    page = await list_charges(_FakeContext(client), ListChargesIn(status="succeeded"))
    assert [c.id for c in page.items] == [CHARGE["id"]]


async def test_status_is_not_sent_upstream():
    """Stripe's list has no status parameter and rejects unknown ones."""
    client = _FakeClient({"data": [CHARGE], "has_more": False})
    await list_charges(_FakeContext(client), ListChargesIn(status="succeeded"))
    assert "status" not in client.calls[0][1]


async def test_cursor_is_the_last_charge_stripe_returned_not_the_last_kept():
    """Paging from the last kept charge would replay the filtered tail
    on the next page, and loop when a whole page is filtered out."""
    failed = _charge(id="ch_failed_tail", status="failed")
    client = _FakeClient({"data": [CHARGE, failed], "has_more": True})
    page = await list_charges(_FakeContext(client), ListChargesIn(status="succeeded"))
    assert [c.id for c in page.items] == [CHARGE["id"]]
    assert page.next_cursor == "ch_failed_tail"


# --- one-definition rule ----------------------------------------------

def test_lifted_and_computed_fields_became_sql_columns():
    """Nobody wrote a column definition for Stripe."""
    cols = dict(find("stripe.charges").columns)
    assert cols["order_ref"] == "VARCHAR"
    assert cols["fee_minor"] == "BIGINT"
    assert cols["amount_refunded"] == "BIGINT"
    assert cols["net_minor"] == "BIGINT"
    assert cols["net_major"] == "DOUBLE"


def test_nested_sources_are_not_columns():
    """The lift flattens them; the raw shapes must not leak into SQL."""
    cols = dict(find("stripe.charges").columns)
    assert "metadata" not in cols
    assert "balance_transaction" not in cols
