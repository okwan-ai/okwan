"""Shopify connector — the order ledger's stated read bound.

Without `read_all_orders` the Admin API returns only the last 60 days of
orders and says nothing about the rest. A ledger that looks complete
while missing its history turns every older payment into a false
"payment with no order". These tests cover the bound being stated: the
scope read alongside the orders, the span it implies, and what the
reconciliation then makes of payments outside it.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import okwan_paypal.connector  # noqa: F401  (registers the connector)
import okwan_shopify.connector  # noqa: F401
from okwan_core import ConnectorContext
from okwan_recon import declarations
from okwan_recon.coverage import Coverage
from okwan_recon.declaration import ResourceRef
from okwan_recon.engine import match
from okwan_recon.fetch import fetch_side
from okwan_shopify.connector import (
    UNSCOPED_ORDER_DAYS,
    _read_from,
    list_orders,
    shopify,
)
from okwan_shopify.schemas import ListOrdersIn

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
SIXTY_DAYS_AGO = NOW - timedelta(days=UNSCOPED_ORDER_DAYS)

ALL = ["read_all_orders", "read_orders", "read_products"]
RECENT_ONLY = ["read_orders", "read_products"]

NODE = {
    "id": "gid://shopify/Order/1",
    "name": "#1002",
    "createdAt": "2026-08-27T15:10:03Z",
    "displayFinancialStatus": "PAID",
    "displayFulfillmentStatus": "UNFULFILLED",
    "currentTotalPriceSet": {"shopMoney": {"amount": "999.00", "currencyCode": "USD"}},
    "totalReceivedSet": {"shopMoney": {"amount": "999.00"}},
    "totalRefundedSet": {"shopMoney": {"amount": "0.00"}},
}


def _data(scopes: list[str] | None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "orders": {
            "edges": [{"node": NODE}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }
    if scopes is not None:
        data["currentAppInstallation"] = {"accessScopes": [{"handle": h} for h in scopes]}
    return data


# --- the bound ---------------------------------------------------------

def test_with_read_all_orders_the_ledger_is_unbounded():
    assert _read_from(_data(ALL), None, NOW) is None


def test_without_read_all_orders_reads_from_sixty_days_ago():
    assert _read_from(_data(RECENT_ONLY), None, NOW) == SIXTY_DAYS_AGO


def test_an_unreadable_scope_list_is_treated_as_absent():
    """Overstating what was read is the failure this exists to prevent."""
    assert _read_from(_data(None), None, NOW) == SIXTY_DAYS_AGO
    assert _read_from({"currentAppInstallation": None}, None, NOW) == SIXTY_DAYS_AGO


def test_a_later_created_at_min_is_the_tighter_bound():
    since = NOW - timedelta(days=10)
    assert _read_from(_data(RECENT_ONLY), since, NOW) == since
    assert _read_from(_data(ALL), since, NOW) == since


def test_an_earlier_created_at_min_cannot_widen_past_the_scope():
    """Asking for a year does not grant a year."""
    since = NOW - timedelta(days=365)
    assert _read_from(_data(RECENT_ONLY), since, NOW) == SIXTY_DAYS_AGO


def test_a_naive_created_at_min_is_read_as_utc():
    since = datetime(2026, 9, 20)  # noqa: DTZ001  (naive on purpose)
    assert _read_from(_data(ALL), since, NOW) == since.replace(tzinfo=UTC)


# --- the list operation ------------------------------------------------

class _FakeClient:
    """Records GraphQL documents and replays one canned response."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self.documents: list[str] = []

    async def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        self.documents.append(json["query"])
        return {"data": self._data}

    async def aclose(self) -> None:
        pass


def _ctx(client: _FakeClient) -> ConnectorContext:
    return ConnectorContext(client=client, credentials={})


async def test_scopes_ride_in_the_same_request_as_the_orders():
    """One round trip. A separate call would double the cost of every
    page against a 2-per-second rate limit."""
    client = _FakeClient(_data(ALL))
    await list_orders(_ctx(client), ListOrdersIn())
    assert len(client.documents) == 1
    assert "currentAppInstallation" in client.documents[0]
    assert "accessScopes" in client.documents[0]


async def test_page_states_the_bound_when_the_scope_is_missing():
    page = await list_orders(_ctx(_FakeClient(_data(RECENT_ONLY))), ListOrdersIn())
    assert page.span_start is not None
    age = datetime.now(UTC) - page.span_start
    assert abs(age - timedelta(days=UNSCOPED_ORDER_DAYS)) < timedelta(minutes=1)
    assert [o.name for o in page.items] == ["#1002"]


async def test_page_states_no_bound_when_the_scope_is_held():
    page = await list_orders(_ctx(_FakeClient(_data(ALL))), ListOrdersIn())
    assert page.span_start is None


# --- what the reconciliation makes of it ------------------------------

async def test_coverage_carries_the_bound(monkeypatch):
    client = _FakeClient(_data(RECENT_ONLY))
    monkeypatch.setattr(shopify, "context_factory", lambda conn, creds: _ctx(client))
    _, cov = await fetch_side(
        ResourceRef(connector="shopify", resource="orders"),
        lambda name, fields: {f: "" for f in fields},
        10,
    )
    assert cov.bounded
    assert cov.span_start is not None and cov.span_end is None


def test_a_payment_older_than_the_ledger_read_is_unverifiable_not_orphaned():
    """Its order is past the 60 days Shopify returned. Without the bound
    it reports as a payment with no order — a false finding."""
    ledger = Coverage(
        source="shopify.orders.list", records=0, cap=500, truncated=False,
        span_start=SIXTY_DAYS_AGO,
    )
    rail = Coverage(source="paypal.transactions.list", records=1, cap=500, truncated=False)
    old_payment = {
        "invoice_id": "#0950", "currency": "USD", "amount_minor": 45000,
        "fee_minor": 0, "net_minor": 45000,
        "initiated_at": (SIXTY_DAYS_AGO - timedelta(days=5)).isoformat(),
    }
    result = match(declarations.shopify_paypal, [], [old_payment], ledger, rail)
    assert result.unmatched_right == []
    [u] = result.unverifiable_right
    assert u.record["invoice_id"] == "#0950"
    assert "earlier" in u.reason
    assert result.summary["match_rate"] is None
