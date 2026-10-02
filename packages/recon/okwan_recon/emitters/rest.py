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
from ..registry import all_across, all_reconciliations, get, get_across
from ..runner import run
from .mcp import across_metadata, tool_metadata


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
        limit: int = Query(100, ge=1, le=1000),
        outcome: str = Query("all", pattern=f"^(all|{'|'.join(OUTCOMES)})$"),
    ) -> dict[str, Any]:
        try:
            spec = get_across(name)
        except KeyError:
            raise HTTPException(404, f"unknown across-rails fold '{name}'") from None
        try:
            result = await run_across(spec, _header_resolver(request), max_records=limit)
        except CredentialError as exc:
            raise HTTPException(401, str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(exc.status, exc.body) from exc
        except OkwanError as exc:
            raise HTTPException(502, str(exc)) from exc
        rows = result.rows()
        if outcome != "all":
            rows = [r for r in rows if r["outcome"] == outcome]
        return {"summary": result.summary, "data": rows}

    @router.get("/{name}")
    async def read_reconciliation(
        request: Request,
        name: str,
        limit: int = Query(100, ge=1, le=1000),
        status: str = Query("all", pattern="^(all|matched|unmatched_left|unmatched_right|unverifiable_left|unverifiable_right)$"),
    ) -> dict[str, Any]:
        try:
            spec = get(name)
        except KeyError:
            raise HTTPException(404, f"unknown reconciliation '{name}'") from None
        try:
            result = await run(spec, _header_resolver(request), max_records=limit)
        except CredentialError as exc:
            raise HTTPException(401, str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(exc.status, exc.body) from exc
        except OkwanError as exc:
            raise HTTPException(502, str(exc)) from exc
        rows = result.rows()
        if status != "all":
            rows = [r for r in rows if r["status"] == status]
        return {"summary": result.summary, "data": rows}

    return router
