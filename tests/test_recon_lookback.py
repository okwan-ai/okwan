"""Reconciliation lookback — one declared span, resolved per run.

A rail whose list defaults to a recent window makes a reconciliation go
blank as its data ages: every order reads unpaid and nothing errors.
These tests cover the parts that would fail silently — a window that
never reaches the side that needs it, one that is forced onto a side
that cannot take it, and precedence that lets the default beat an
explicit choice.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import okwan_paypal.connector  # noqa: F401  (registers the connector)
import okwan_shopify.connector  # noqa: F401
import okwan_stripe.connector  # noqa: F401
import pytest
from okwan_core import ConnectorContext
from okwan_paypal.connector import paypal
from okwan_recon import declarations, runner
from okwan_recon.declaration import ExactRef, Reconciliation, ResourceRef
from okwan_recon.emitters.mcp import tool_metadata
from okwan_recon.fetch import fetch_rows
from okwan_recon.runner import resolve_window, side_overrides
from okwan_stripe.connector import stripe
from pydantic import ValidationError

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def spec(lookback: str | None = "90d", **kw: Any) -> Reconciliation:
    return Reconciliation(
        name="t",
        left=ResourceRef(connector="shopify", resource="orders"),
        right=ResourceRef(connector="paypal", resource="transactions"),
        keys=[ExactRef(left="name", right="invoice_id")],
        lookback=lookback,
        **kw,
    )


def _no_credentials(name: str, fields: tuple[str, ...]) -> dict[str, str]:
    return {f: "" for f in fields}


class _RecordingClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(params or {})
        return self._payload

    async def aclose(self) -> None:
        pass


def _fake(monkeypatch, connector, payload: dict[str, Any]) -> _RecordingClient:
    client = _RecordingClient(payload)
    monkeypatch.setattr(
        connector, "context_factory",
        lambda conn, creds: ConnectorContext(client=client, credentials=creds),
    )
    return client


# --- declaration -------------------------------------------------------

def test_lookback_uses_the_fuzzy_window_grammar():
    assert spec("90d").lookback_delta == timedelta(days=90)
    assert spec("48h").lookback_delta == timedelta(hours=48)


@pytest.mark.parametrize("bad", ["90", "3 weeks", "90D", "-1d"])
def test_malformed_lookback_is_refused_at_declaration(bad):
    """Caught when the declaration loads, not on the first run."""
    with pytest.raises(ValidationError):
        spec(bad)


def test_no_lookback_means_no_window():
    assert spec(None).lookback_delta is None
    assert resolve_window(spec(None), NOW) == {}


def test_lookback_is_part_of_the_derived_metadata():
    """An agent reading the tool should know what span it reconciles."""
    assert tool_metadata(spec("90d"))["lookback"] == "90d"


def test_shipped_rail_declarations_state_a_span():
    assert declarations.shopify_paypal.lookback is not None
    assert declarations.shopify_stripe.lookback is not None


# --- resolution --------------------------------------------------------

def test_window_resolves_to_concrete_bounds_ending_now():
    window = resolve_window(spec("90d"), NOW)
    assert window == {"start_date": NOW - timedelta(days=90), "end_date": NOW}


def test_a_sides_own_params_beat_the_lookback():
    """A declaration that pins a side's start explicitly meant it."""
    pinned = datetime(2026, 8, 1, tzinfo=UTC)
    ref = ResourceRef(connector="paypal", resource="transactions",
                      params={"start_date": pinned})
    out = side_overrides(ref, resolve_window(spec(), NOW), None)
    assert "start_date" not in out  # fetch_rows then reads ref.params
    assert out["end_date"] == NOW


def test_caller_overrides_beat_the_lookback():
    ref = ResourceRef(connector="paypal", resource="transactions")
    mine = datetime(2026, 1, 1, tzinfo=UTC)
    out = side_overrides(ref, resolve_window(spec(), NOW), {"start_date": mine})
    assert out["start_date"] == mine


async def test_both_sides_get_the_same_instant(monkeypatch):
    """Resolved once per run. Two calls to now() would give the sides
    different ends, and a record landing between them reads unmatched."""
    seen: list[dict[str, Any]] = []

    async def record(ref, resolver, cap, overrides):
        seen.append(overrides)
        return []

    monkeypatch.setattr(runner, "fetch_rows", record)
    await runner.run(spec("90d"))
    assert seen[0] == seen[1]
    assert seen[0]["end_date"] - seen[0]["start_date"] == timedelta(days=90)


# --- application -------------------------------------------------------

async def test_a_side_that_accepts_a_window_receives_it(monkeypatch):
    """PayPal walks from the lookback's start rather than its own 30-day
    default, so data older than a month is still read."""
    client = _fake(monkeypatch, paypal, {
        "transaction_details": [], "page": 1, "total_pages": 1,
        "last_refreshed_datetime": NOW.isoformat(),
    })
    window = resolve_window(spec("90d"), NOW)
    ref = ResourceRef(connector="paypal", resource="transactions")
    await fetch_rows(ref, _no_credentials, 10, side_overrides(ref, window, None))
    first = datetime.fromisoformat(client.calls[0]["start_date"])
    assert first == NOW - timedelta(days=90)


async def test_a_side_that_does_not_accept_a_window_is_left_alone(monkeypatch):
    """Stripe's list takes neither field. It is read exactly as it would
    be without a lookback — no stray parameter upstream rejects."""
    client = _fake(monkeypatch, stripe, {"data": [], "has_more": False})
    window = resolve_window(spec("90d"), NOW)
    ref = ResourceRef(connector="stripe", resource="charges")
    await fetch_rows(ref, _no_credentials, 10, side_overrides(ref, window, None))
    sent = client.calls[0]
    assert "start_date" not in sent and "end_date" not in sent
    assert set(sent) == {"limit", "expand[]"}
