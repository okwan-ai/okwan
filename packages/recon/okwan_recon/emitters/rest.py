"""Read-only REST routes generated from the reconciliation registry.

Authenticated like every other data route: the caller presents an Okwan
API key, and both sides' credentials are read from that tenant's vault —
the same resolution the hosted MCP uses. Nothing is read from request
headers or from this server's environment. Until 2026-10-02 these routes
took per-connector credential headers and fell back to the server's own
OKWAN_<CONNECTOR>_* variables, the last survivor of the per-request model
the vault replaced.

A run is metered and quota-gated like a query: one request however many
upstream calls the two sides make.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from okwan_core import CredentialError, OkwanError, UpstreamError

from ..across import OUTCOMES, run_across
from ..engine import STATUSES
from ..paging import DEFAULT_ROWS, MAX_ROWS
from ..registry import all_across, all_reconciliations, get, get_across
from ..runner import run
from .mcp import across_metadata, paged, tool_metadata


def _rest(out: dict[str, Any]) -> dict[str, Any]:
    """REST names the page `data`; a stale cursor is a 409, not a 200."""
    if "error" in out:
        raise HTTPException(409, out["error"])
    out["data"] = out.pop("rows")
    return out


async def _vault_resolver(tenant):
    from okwan_api.auth import get_store
    from okwan_vault import resolver_for

    return await resolver_for(get_store(), tenant.id)


def build_router() -> APIRouter:
    from okwan_api.auth import check_quota, current_tenant, meter

    router = APIRouter(prefix="/v1/reconciliations", tags=["reconciliations"])

    @router.get("")
    async def list_reconciliations(_=Depends(current_tenant)) -> dict[str, Any]:
        return {
            "data": [tool_metadata(s) for s in all_reconciliations()],
            "across": [across_metadata(s) for s in all_across()],
        }

    @router.get("/across/{name}")
    async def read_across(
        name: str,
        limit: int = Query(DEFAULT_ROWS, ge=1, le=MAX_ROWS),
        outcome: str = Query("all", pattern=f"^(all|{'|'.join(OUTCOMES)})$"),
        cursor: str | None = None,
        tenant=Depends(check_quota),
    ) -> dict[str, Any]:
        try:
            spec = get_across(name)
        except KeyError:
            raise HTTPException(404, f"unknown across-rails fold '{name}'") from None
        try:
            result = await run_across(spec, await _vault_resolver(tenant))
        except CredentialError as exc:
            raise HTTPException(401, str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(exc.status, exc.body) from exc
        except OkwanError as exc:
            raise HTTPException(502, str(exc)) from exc
        await meter(tenant, "rest:across")
        return _rest(paged(result.summary, result.rows(), "outcome", outcome, limit, cursor))

    @router.get("/{name}")
    async def read_reconciliation(
        name: str,
        limit: int = Query(DEFAULT_ROWS, ge=1, le=MAX_ROWS),
        status: str = Query("all", pattern=f"^(all|{'|'.join(STATUSES)})$"),
        cursor: str | None = None,
        tenant=Depends(check_quota),
    ) -> dict[str, Any]:
        try:
            spec = get(name)
        except KeyError:
            raise HTTPException(404, f"unknown reconciliation '{name}'") from None
        try:
            result = await run(spec, await _vault_resolver(tenant))
        except CredentialError as exc:
            raise HTTPException(401, str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(exc.status, exc.body) from exc
        except OkwanError as exc:
            raise HTTPException(502, str(exc)) from exc
        await meter(tenant, "rest:reconcile")
        return _rest(paged(result.summary, result.rows(), "status", status, limit, cursor))

    return router
