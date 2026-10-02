"""Result paging, and folds on the hosted MCP.

`limit` used to be the per-side fetch cap: asking for 100 rows read 100
records per side, reconciled a fragment, and reported the rest as
unmatched or unverifiable. These tests pin the separation — every
record read to the declaration's cap, the result paged — and cover what
paging stateless re-runs would get wrong silently: a page cut from a
result that has since changed.
"""
from __future__ import annotations

from typing import Any

import okwan_paypal.connector  # noqa: F401  (registers the connector)
import okwan_shopify.connector  # noqa: F401
import okwan_stripe.connector  # noqa: F401
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from okwan_query import mcp_http
from okwan_recon import declarations
from okwan_recon.across import AcrossRails, Rail
from okwan_recon.declaration import Reconciliation
from okwan_recon.emitters import mcp, rest
from okwan_recon.engine import ReconResult
from okwan_recon.paging import MAX_ROWS, StaleCursor, fingerprint, page_rows
from okwan_recon.registry import _ACROSS, _REGISTRY, register, register_across

PAIR = declarations.shopify_paypal
FOLD = declarations.rails


def rows(n: int, status: str = "unmatched_left") -> list[dict[str, Any]]:
    return [{"status": status, "left": {"name": f"#{i}"}, "right": None} for i in range(n)]


def names(page: dict[str, Any]) -> list[str]:
    return [r["left"]["name"] for r in page["rows"]]


# --- fetch cap is not page size ---------------------------------------

def test_the_declared_fetch_cap_defaults_high():
    assert Reconciliation.model_fields["max_records"].default == 5_000
    assert PAIR.max_records == 5_000


async def test_limit_no_longer_reaches_the_fetch(monkeypatch):
    seen: dict[str, Any] = {}

    async def fake_run(spec, *args, **kw):
        seen.update(kw)
        return ReconResult(name=spec.name)

    monkeypatch.setattr(mcp, "run", fake_run)
    await mcp._make_tool_fn(PAIR)(limit=3)
    assert "max_records" not in seen


async def test_a_small_page_still_reports_the_whole_result(monkeypatch):
    """Five unmatched orders, a page of two: the summary counts five."""
    result = ReconResult(name=PAIR.name, unmatched_left=[{"name": f"#{i}"} for i in range(5)])

    async def fake_run(spec, *args, **kw):
        return result

    monkeypatch.setattr(mcp, "run", fake_run)
    out = await mcp._make_tool_fn(PAIR)(limit=2)
    assert out["summary"]["unmatched_left"] == 5
    assert len(out["rows"]) == 2
    assert out["total_rows"] == 5 and out["has_more"] is True


# --- paging ------------------------------------------------------------

def test_walking_every_page_returns_every_row_exactly_once():
    data = rows(7)
    seen: list[str] = []
    cursor = None
    while True:
        page = page_rows(data, 3, cursor)
        seen += names(page)
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor = page["next_cursor"]
    assert seen == [f"#{i}" for i in range(7)]


def test_limit_is_clamped():
    assert len(page_rows(rows(MAX_ROWS + 5), 10**6)["rows"]) == MAX_ROWS
    assert len(page_rows(rows(3), 0)["rows"]) == 1


def test_a_cursor_into_a_changed_result_is_refused():
    """Re-run between pages and an order was added: the offset now points
    somewhere else, so the page is refused rather than served."""
    cursor = page_rows(rows(4), 2)["next_cursor"]
    with pytest.raises(StaleCursor, match="changed"):
        page_rows(rows(5), 2, cursor)


def test_a_cursor_for_another_filter_is_refused():
    data = rows(4)
    cursor = page_rows(data, 2, view="all")["next_cursor"]
    with pytest.raises(StaleCursor, match="issued for 'all'"):
        page_rows(data, 2, cursor, view="unmatched_left")


def test_an_unreadable_cursor_is_refused_not_restarted():
    """Restarting would hand back page one as if it were page two."""
    with pytest.raises(StaleCursor, match="unreadable"):
        page_rows(rows(4), 2, "not-a-cursor")


def test_the_fingerprint_ignores_clock_derived_text():
    """A coverage span ends at now, so a reason quoting it differs on
    every run. That must not read as a changed result."""
    a = [{"status": "unverifiable_left", "left": {"name": "#1"}, "reason": "read to 12:00"}]
    b = [{"status": "unverifiable_left", "left": {"name": "#1"}, "reason": "read to 12:01"}]
    assert fingerprint(a) == fingerprint(b)


def test_a_stale_cursor_still_returns_the_summary():
    out = mcp.paged({"matched": 1}, rows(2), "status", "all", 1, "garbage")
    assert "error" in out
    assert out["summary"] == {"matched": 1}
    assert out["rows"] == []


# --- REST --------------------------------------------------------------

def _client(monkeypatch, result) -> TestClient:
    async def fake(spec, *args, **kw):
        return result

    monkeypatch.setattr(rest, "run", fake)
    monkeypatch.setattr(rest, "run_across", fake)
    app = FastAPI()
    app.include_router(rest.build_router())
    return TestClient(app)


def test_rest_pages_and_names_the_page_data(monkeypatch):
    result = ReconResult(name=PAIR.name, unmatched_left=[{"name": f"#{i}"} for i in range(3)])
    client = _client(monkeypatch, result)
    first = client.get(f"/v1/reconciliations/{PAIR.name}", params={"limit": 2}).json()
    assert len(first["data"]) == 2 and first["has_more"] is True
    second = client.get(
        f"/v1/reconciliations/{PAIR.name}",
        params={"limit": 2, "cursor": first["next_cursor"]},
    ).json()
    assert len(second["data"]) == 1 and second["has_more"] is False


def test_rest_stale_cursor_is_a_conflict(monkeypatch):
    client = _client(monkeypatch, ReconResult(name=PAIR.name))
    r = client.get(f"/v1/reconciliations/{PAIR.name}", params={"cursor": "garbage"})
    assert r.status_code == 409


@pytest.mark.parametrize("status", ["ambiguous", "matched_explained", "matched_discrepant"])
def test_rest_filter_reaches_every_status(monkeypatch, status):
    """These were unreachable: the filter pattern was hand-written."""
    client = _client(monkeypatch, ReconResult(name=PAIR.name))
    assert client.get(f"/v1/reconciliations/{PAIR.name}", params={"status": status}).status_code == 200


# --- one name space -----------------------------------------------------

def test_a_reconciliation_cannot_take_a_folds_name():
    with pytest.raises(ValueError, match="across-rails fold"):
        register(PAIR.model_copy(update={"name": FOLD.name}))
    assert FOLD.name not in _REGISTRY


def test_a_fold_cannot_take_a_reconciliations_name():
    with pytest.raises(ValueError, match="names a reconciliation"):
        register_across(AcrossRails(
            name=PAIR.name, ledger_total="total_price_minor",
            rails=[Rail(reconciliation="shopify_paypal", collected="amount_minor"),
                   Rail(reconciliation="shopify_stripe", collected="amount")],
        ))
    assert PAIR.name not in _ACROSS


# --- hosted MCP --------------------------------------------------------

ALL_CREDS = {
    "shopify": ("access_token", "shop_domain"),
    "paypal": ("client_id", "client_secret"),
    "stripe": ("secret_key",),
}


def _tenant(monkeypatch, configured: set[str]) -> None:
    def resolver(name: str, fields: tuple[str, ...]) -> dict[str, str]:
        return {f: ("x" if name in configured else "") for f in fields}

    async def tenant(ctx):
        return resolver

    monkeypatch.setattr(mcp_http, "_tenant_resolver", tenant)


async def test_hosted_list_includes_folds_and_their_runnability(monkeypatch):
    _tenant(monkeypatch, {"shopify", "paypal"})
    out = await mcp_http._list_recon_tool()(ctx=None)
    [fold] = out["across"]
    assert fold["name"] == "rails"
    assert fold["runnable"] is False
    assert fold["missing_credentials"] == ["stripe.secret_key"]
    assert {r["name"] for r in out["reconciliations"]} >= {"shopify_paypal", "shopify_stripe"}


async def test_hosted_reconcile_runs_a_fold_by_name(monkeypatch):
    _tenant(monkeypatch, set(ALL_CREDS))

    class _Folded:
        @property
        def summary(self):
            return {"collected_twice": 1, "collected_twice_minor": 99900}

        def rows(self):
            return [
                {"outcome": "collected_twice", "order": {"name": "#1002"}},
                {"outcome": "collected", "order": {"name": "#1003"}},
            ]

    async def fake(spec, resolver):
        return _Folded()

    monkeypatch.setattr(mcp_http, "run_across", fake)
    tool = mcp_http._reconcile_tool()
    out = await tool(ctx=None, name="rails", status="collected_twice")
    assert [r["order"]["name"] for r in out["rows"]] == ["#1002"]
    assert out["summary"]["collected_twice_minor"] == 99900


async def test_hosted_reconcile_rejects_a_status_the_kind_does_not_have(monkeypatch):
    _tenant(monkeypatch, set(ALL_CREDS))
    out = await mcp_http._reconcile_tool()(ctx=None, name="rails", status="matched")
    assert "no outcome 'matched'" in out["error"]


async def test_hosted_fold_names_what_is_missing(monkeypatch):
    _tenant(monkeypatch, {"shopify", "paypal"})
    out = await mcp_http._reconcile_tool()(ctx=None, name="rails")
    assert "stripe.secret_key" in out["error"]


async def test_hosted_unknown_name_lists_both_kinds(monkeypatch):
    _tenant(monkeypatch, set(ALL_CREDS))
    out = await mcp_http._reconcile_tool()(ctx=None, name="nope")
    assert "rails" in out["error"] and "shopify_paypal" in out["error"]


async def test_hosted_pages_with_a_cursor(monkeypatch):
    _tenant(monkeypatch, set(ALL_CREDS))
    result = ReconResult(name=PAIR.name, unmatched_left=[{"name": f"#{i}"} for i in range(3)])

    async def fake(spec, resolver):
        return result

    monkeypatch.setattr(mcp_http, "run_recon", fake)
    tool = mcp_http._reconcile_tool()
    first = await tool(ctx=None, name=PAIR.name, limit=2)
    second = await tool(ctx=None, name=PAIR.name, limit=2, cursor=first["next_cursor"])
    assert len(first["rows"]) == 2 and len(second["rows"]) == 1
    assert second["has_more"] is False
