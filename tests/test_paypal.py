"""PayPal connector — minor-unit correctness, flattening, and the window walk.

PayPal is the first rail that pages inside a bounded date window rather
than along a single sequence, so the cursor carries two things and the
walk has to roll forward. These tests cover the parts that would fail
silently: money that rounds the wrong way, ledger rows that are not
sales, and a window boundary that drops records.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import okwan_paypal.connector  # noqa: F401  (registers the connector)
import pytest
from okwan_paypal.connector import (
    WINDOW_DAYS,
    _decode,
    _encode,
    list_transactions,
)
from okwan_paypal.schemas import ListTransactionsIn, Transaction, money_to_minor
from okwan_query import find

SEED_ROW = {
    "transaction_info": {
        "transaction_id": "6C472575008547939",
        "transaction_event_code": "T1900",
        "transaction_initiation_date": "2026-08-29T22:16:06Z",
        "transaction_amount": {"currency_code": "USD", "value": "5000.00"},
        "transaction_status": "S",
        "transaction_subject": "Initial balance",
    },
    "payer_info": {"address_status": "N", "payer_name": {}},
}


# --- money -------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [
        ("299.10", "USD", 29910),   # the float path gives 29909.999...
        ("5000.00", "USD", 500000),
        ("0.01", "USD", 1),
        ("-4.55", "USD", -455),     # refunds keep their sign
        ("1500", "KRW", 1500),      # zero-decimal: the integer is major
        ("1500.00", "JPY", 1500),
    ],
)
def test_decimal_strings_convert_exactly(value, currency, expected):
    assert money_to_minor(value, currency) == expected


def test_unparseable_money_is_none_not_zero():
    """Zero is a real amount. Absence must not read as a balanced row."""
    assert money_to_minor("", "USD") is None
    assert money_to_minor("n/a", "USD") is None
    assert money_to_minor(None, "USD") is None


# --- flattening --------------------------------------------------------

def test_nested_payload_flattens_to_one_row():
    t = Transaction.model_validate(SEED_ROW)
    assert t.transaction_id == "6C472575008547939"
    assert t.amount_minor == 500000
    assert t.currency == "USD"
    assert t.subject == "Initial balance"


def test_fee_is_signed_and_net_subtracts_it():
    """PayPal reports the fee as a negative number, so net is a sum."""
    row = {
        "transaction_info": {
            "transaction_id": "T1",
            "transaction_event_code": "T0006",
            "transaction_amount": {"currency_code": "USD", "value": "100.00"},
            "fee_amount": {"currency_code": "USD", "value": "-3.49"},
            "transaction_status": "S",
        }
    }
    t = Transaction.model_validate(row)
    assert t.amount_minor == 10000
    assert t.fee_minor == -349
    assert t.net_minor == 9651
    assert t.net_major == pytest.approx(96.51)


def test_missing_amount_leaves_computed_fields_none():
    t = Transaction.model_validate({"transaction_info": {"transaction_id": "T2"}})
    assert t.amount_minor is None
    assert t.net_minor is None
    assert t.amount_major is None


# --- classification ----------------------------------------------------

def test_account_adjustments_are_not_payments():
    """Sandbox seed funding is T1900. Counted as a sale it becomes an
    unmatched break that never resolves."""
    assert Transaction.model_validate(SEED_ROW).is_payment is False


@pytest.mark.parametrize("code", ["T0006", "T0000", "T0300", "T0400"])
def test_inbound_payment_codes_are_payments(code):
    """A capture arrives as T0006 with a positive amount."""
    row = {
        "transaction_info": {
            "transaction_id": "X",
            "transaction_event_code": code,
            "transaction_amount": {"currency_code": "USD", "value": "99.00"},
        }
    }
    assert Transaction.model_validate(row).is_payment is True


def test_outbound_payout_is_not_a_payment():
    """T0001 is a payout send and shares the T0 family with a capture.
    Counted as a sale it becomes revenue the merchant never earned."""
    row = {
        "transaction_info": {
            "transaction_id": "X",
            "transaction_event_code": "T0001",
            "transaction_amount": {"currency_code": "USD", "value": "-299.00"},
        }
    }
    assert Transaction.model_validate(row).is_payment is False


def test_amountless_row_is_not_a_payment():
    """Direction is part of the definition, so a row with no amount
    cannot be classified as one."""
    row = {"transaction_info": {"transaction_id": "X", "transaction_event_code": "T0006"}}
    assert Transaction.model_validate(row).is_payment is False


# --- cursor ------------------------------------------------------------

def test_cursor_round_trips_window_and_page():
    start = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert _decode(_encode(start, 3)) == (start, 3)


def test_unreadable_cursor_restarts_rather_than_raising():
    """A cursor is the caller's handle, not a key."""
    assert _decode("not-a-cursor") == (None, 1)
    assert _decode("") == (None, 1)


def test_cursor_is_opaque():
    """Callers must not be able to page by editing it."""
    token = _encode(datetime(2026, 7, 1, tzinfo=UTC), 2)
    assert "2026" not in token and "page" not in token


# --- window walk -------------------------------------------------------

class _FakeClient:
    """Records the windows requested and replays canned pages."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages
        self.calls: list[dict[str, Any]] = []

    async def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(params)
        return self._pages[min(len(self.calls) - 1, len(self._pages) - 1)]


class _FakeContext:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client
        self.credentials: dict[str, str] = {}


def _payload(rows: list[dict], page: int = 1, total_pages: int = 1) -> dict[str, Any]:
    return {
        "transaction_details": rows,
        "page": page,
        "total_pages": total_pages,
        "total_items": len(rows),
        "last_refreshed_datetime": "2099-01-01T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_window_is_capped_below_the_upstream_limit():
    """Upstream refuses a range over 31 days."""
    client = _FakeClient([_payload([SEED_ROW])])
    page = await list_transactions(
        _FakeContext(client),
        ListTransactionsIn(start_date=datetime.now(UTC) - timedelta(days=90)),
    )
    start = datetime.fromisoformat(client.calls[0]["start_date"])
    end = datetime.fromisoformat(client.calls[0]["end_date"])
    assert (end - start).days <= 31
    assert len(page.items) == 1


@pytest.mark.asyncio
async def test_more_pages_in_a_window_keep_the_window():
    client = _FakeClient([_payload([SEED_ROW], page=1, total_pages=2)])
    page = await list_transactions(_FakeContext(client), ListTransactionsIn())
    assert page.has_more
    window, n = _decode(page.next_cursor)
    assert n == 2


@pytest.mark.asyncio
async def test_exhausted_window_rolls_forward_without_a_gap():
    """The next window must start where the last one ended, or records
    falling in the seam are never returned."""
    client = _FakeClient([_payload([SEED_ROW])])
    start = datetime.now(UTC) - timedelta(days=90)
    page = await list_transactions(
        _FakeContext(client), ListTransactionsIn(start_date=start)
    )
    assert page.has_more
    next_window, n = _decode(page.next_cursor)
    assert n == 1
    first_end = datetime.fromisoformat(client.calls[0]["end_date"])
    assert next_window == first_end


@pytest.mark.asyncio
async def test_empty_windows_are_skipped_not_returned():
    """A caller paging a quiet year must not receive empty pages."""
    client = _FakeClient([_payload([]), _payload([]), _payload([SEED_ROW])])
    page = await list_transactions(
        _FakeContext(client),
        ListTransactionsIn(start_date=datetime.now(UTC) - timedelta(days=90)),
    )
    assert len(client.calls) > 1
    assert len(page.items) == 1


@pytest.mark.asyncio
async def test_walk_stops_at_paypals_own_horizon():
    """PayPal states how current its ledger is; past that there is
    nothing to find and empty windows would be walked forever."""
    payload = _payload([SEED_ROW])
    payload["last_refreshed_datetime"] = "2026-08-30T09:59:59Z"
    client = _FakeClient([payload])
    page = await list_transactions(
        _FakeContext(client),
        ListTransactionsIn(
            start_date=datetime(2026, 8, 1, tzinfo=UTC),
            end_date=datetime(2027, 1, 1, tzinfo=UTC),
        ),
    )
    assert page.has_more is False
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_list_requests_balance_affecting_rows_only():
    """IDs are not unique across balance-affecting and non-balance-affecting
    rows; without this filter every row pairs ambiguously."""
    client = _FakeClient([_payload([SEED_ROW])])
    await list_transactions(_FakeContext(client), ListTransactionsIn())
    assert client.calls[0]["balance_affecting_records_only"] == "Y"


# --- one-definition rule ----------------------------------------------

def test_computed_fields_became_sql_columns():
    """Nobody wrote a column definition for PayPal."""
    cols = dict(find("paypal.transactions").columns)
    assert cols["amount_minor"] == "BIGINT"
    assert cols["net_minor"] == "BIGINT"
    assert cols["net_major"] == "DOUBLE"
    assert cols["is_payment"] == "BOOLEAN"
    assert cols["initiated_at"] == "TIMESTAMP"


def test_window_constant_stays_under_the_upstream_cap():
    assert WINDOW_DAYS <= 31
