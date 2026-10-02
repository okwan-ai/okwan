"""MCP 2.0 tools generated from the reconciliation registry.

Mirrors okwan_mcp.generator: zero per-reconciliation MCP code, tool
names derived from the declaration, readOnlyHint always true because
validate_against_registry() forbids write operations on either side.
"""
from __future__ import annotations

import inspect
from typing import Any

from ..across import OUTCOMES, AcrossRails, run_across
from ..declaration import Fuzzy, Reconciliation
from ..paging import DEFAULT_ROWS, StaleCursor, page_rows
from ..registry import all_across, all_reconciliations
from ..runner import run


def tool_metadata(spec: Reconciliation) -> dict[str, Any]:
    """Declaration-derived descriptor; also used by docs and tests."""
    return {
        "name": spec.name,
        "tool_name": spec.tool_name,
        "path": spec.rest_path,
        "title": spec.display_title,
        "description": spec.description
        or (
            f"Reconcile {spec.left.qualified} against {spec.right.qualified}; "
            "reports matched, unmatched-left and unmatched-right records."
        ),
        "left": spec.left.qualified,
        "right": spec.right.qualified,
        "rules": [k.kind for k in spec.keys],
        "match_windows": [
            {"rule": k.kind, "window": k.window}
            for k in spec.keys
            if isinstance(k, Fuzzy)
        ],
        "lookback": spec.lookback,
        "view": spec.view_name,
        "read_only": True,
    }


def paged(summary: dict[str, Any], rows: list[dict[str, Any]], key: str,
          view: str, limit: int, cursor: str | None) -> dict[str, Any]:
    """Filter to `view` on `key`, then page. Shared by every surface."""
    if view != "all":
        rows = [r for r in rows if r[key] == view]
    try:
        page = page_rows(rows, limit, cursor, view)
    except StaleCursor as exc:
        return {"error": str(exc), "summary": summary, "rows": [],
                "has_more": False, "next_cursor": None}
    return {"summary": summary, **page}


def _signature(filter_name: str) -> inspect.Signature:
    kw = inspect.Parameter.KEYWORD_ONLY
    return inspect.Signature([
        inspect.Parameter("limit", kw, annotation=int, default=DEFAULT_ROWS),
        inspect.Parameter(filter_name, kw, annotation=str, default="all"),
        inspect.Parameter("cursor", kw, annotation=str | None, default=None),
    ])


def _make_tool_fn(spec: Reconciliation):
    async def tool_fn(
        limit: int = DEFAULT_ROWS, status: str = "all", cursor: str | None = None
    ) -> dict[str, Any]:
        result = await run(spec)
        return paged(result.summary, result.rows(), "status", status, limit, cursor)

    tool_fn.__signature__ = _signature("status")  # type: ignore[attr-defined]
    tool_fn.__doc__ = tool_metadata(spec)["description"]
    return tool_fn


def across_metadata(spec: AcrossRails) -> dict[str, Any]:
    """Declaration-derived descriptor for an across-rails fold."""
    return {
        "name": spec.name,
        "tool_name": spec.tool_name,
        "path": spec.rest_path,
        "title": spec.display_title,
        "description": spec.description
        or "Fold several rails against one order ledger, one verdict per order.",
        "rails": [r.reconciliation for r in spec.rails],
        "outcomes": list(OUTCOMES),
        "tolerance_bps": spec.tolerance_bps,
        "view": spec.view_name,
        "read_only": True,
    }


def _make_across_tool_fn(spec: AcrossRails):
    async def tool_fn(
        limit: int = DEFAULT_ROWS, outcome: str = "all", cursor: str | None = None
    ) -> dict[str, Any]:
        result = await run_across(spec)
        return paged(result.summary, result.rows(), "outcome", outcome, limit, cursor)

    tool_fn.__signature__ = _signature("outcome")  # type: ignore[attr-defined]
    tool_fn.__doc__ = across_metadata(spec)["description"]
    return tool_fn


def build_server():
    """One MCP server exposing every registered reconciliation.

    Reconciliations span connectors, so they cannot live on a
    per-connector server the way connector tools do.
    """
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer(
        name="okwan-reconciliation",
        instructions=(
            "Cross-rail reconciliation over Okwan connectors. Every tool is "
            "read-only and reports matched and unmatched records between two "
            "payment or ledger sources."
        ),
        version="0.1.0",
    )
    for spec in all_reconciliations():
        meta = tool_metadata(spec)
        server.add_tool(
            _make_tool_fn(spec),
            name=meta["tool_name"],
            description=f"[reconciliation] {meta['description']}",
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
            structured_output=False,
        )
    for spec in all_across():
        meta = across_metadata(spec)
        server.add_tool(
            _make_across_tool_fn(spec),
            name=meta["tool_name"],
            description=f"[reconciliation] {meta['description']}",
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
            structured_output=False,
        )
    return server


async def run_stdio() -> None:
    await build_server().run_stdio_async()
