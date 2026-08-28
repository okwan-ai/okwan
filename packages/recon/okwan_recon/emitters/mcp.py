"""MCP 2.0 tools generated from the reconciliation registry.

Mirrors okwan_mcp.generator: zero per-reconciliation MCP code, tool
names derived from the declaration, readOnlyHint always true because
validate_against_registry() forbids write operations on either side.
"""
from __future__ import annotations

import inspect
from typing import Any

from ..declaration import Fuzzy, Reconciliation
from ..registry import all_reconciliations
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
        "view": spec.view_name,
        "read_only": True,
    }


def _make_tool_fn(spec: Reconciliation):
    async def tool_fn(
        limit: int = 100,
        status: str = "all",
    ) -> dict[str, Any]:
        result = await run(spec, max_records=limit)
        rows = result.rows()
        if status != "all":
            rows = [r for r in rows if r["status"] == status]
        return {"summary": result.summary, "rows": rows}

    tool_fn.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter(
                "limit", inspect.Parameter.KEYWORD_ONLY, annotation=int, default=100
            ),
            inspect.Parameter(
                "status", inspect.Parameter.KEYWORD_ONLY, annotation=str, default="all"
            ),
        ]
    )
    tool_fn.__doc__ = tool_metadata(spec)["description"]
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
    return server


async def run_stdio() -> None:
    await build_server().run_stdio_async()
