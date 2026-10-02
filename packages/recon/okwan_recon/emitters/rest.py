"""Read-only REST routes generated from the reconciliation registry.

Credentials arrive per connector as
X-Okwan-{CONNECTOR}-Credential-{field}, extending the gateway's v0
header convention to the two-sided case; falls back to environment
variables when a header is absent.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
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


def _header_resolver(request: Request):
    def resolve(connector_name: str, fields: tuple[str, ...]) -> dict[str, str]:
        from ..fetch import env_credentials

        fallback = env_credentials(connector_name, fields)
        out: dict[str, str] = {}
        for f in fields:
            header = f"X-Okwan-{connector_name.title()}-Credential-{f.replace('_', '-')}"
            out[f] = request.headers.get(header) or fallback.get(f, "")
        return out

    return resolve


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/reconciliations", tags=["reconciliations"])

    @router.get("")
    async def list_reconciliations() -> dict[str, Any]:
        return {
            "data": [tool_metadata(s) for s in all_reconciliations()],
            "across": [across_metadata(s) for s in all_across()],
        }

    @router.get("/across/{name}")
    async def read_across(
        request: Request,
        name: str,
        limit: int = Query(DEFAULT_ROWS, ge=1, le=MAX_ROWS),
        outcome: str = Query("all", pattern=f"^(all|{'|'.join(OUTCOMES)})$"),
        cursor: str | None = None,
    ) -> dict[str, Any]:
        try:
            spec = get_across(name)
        except KeyError:
            raise HTTPException(404, f"unknown across-rails fold '{name}'") from None
        try:
            result = await run_across(spec, _header_resolver(request))
        except CredentialError as exc:
            raise HTTPException(401, str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(exc.status, exc.body) from exc
        except OkwanError as exc:
            raise HTTPException(502, str(exc)) from exc
        return _rest(paged(result.summary, result.rows(), "outcome", outcome, limit, cursor))

    @router.get("/{name}")
    async def read_reconciliation(
        request: Request,
        name: str,
        limit: int = Query(DEFAULT_ROWS, ge=1, le=MAX_ROWS),
        status: str = Query("all", pattern=f"^(all|{'|'.join(STATUSES)})$"),
        cursor: str | None = None,
    ) -> dict[str, Any]:
        try:
            spec = get(name)
        except KeyError:
            raise HTTPException(404, f"unknown reconciliation '{name}'") from None
        try:
            result = await run(spec, _header_resolver(request))
        except CredentialError as exc:
            raise HTTPException(401, str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(exc.status, exc.body) from exc
        except OkwanError as exc:
            raise HTTPException(502, str(exc)) from exc
        return _rest(paged(result.summary, result.rows(), "status", status, limit, cursor))

    return router
