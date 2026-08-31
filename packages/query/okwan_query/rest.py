"""Read-only REST route for federated SQL."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from okwan_core import CredentialError, OkwanError, UpstreamError
from pydantic import BaseModel, Field

from .guard import UnsafeStatement
from .mcp import catalog_payload
from .session import DEFAULT_LIMIT, QuerySession


class QueryIn(BaseModel):
    sql: str = Field(description="Read-only SQL over connector.resource tables")
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=5000)


async def _tenant_resolver(tenant):
    """A synchronous resolver over this tenant's stored credentials."""
    from okwan_api.auth import get_store
    from okwan_vault import resolver_for

    return await resolver_for(get_store(), tenant.id)


def build_router() -> APIRouter:
    from okwan_api.auth import check_quota, current_tenant, meter

    router = APIRouter(prefix="/v1/query", tags=["query"])

    @router.get("/tables")
    async def list_tables(tenant=Depends(current_tenant)) -> dict[str, Any]:
        return catalog_payload(await _tenant_resolver(tenant))

    @router.post("")
    async def run_query(
        body: QueryIn, tenant=Depends(check_quota)
    ) -> dict[str, Any]:
        session = QuerySession(
            resolver=await _tenant_resolver(tenant), max_records=body.limit
        )
        try:
            result = await session.query(body.sql)
            await meter(tenant, "rest:query")
            return result
        except UnsafeStatement as exc:
            raise HTTPException(400, str(exc)) from exc
        except CredentialError as exc:
            raise HTTPException(401, str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(exc.status, exc.body) from exc
        except OkwanError as exc:
            raise HTTPException(502, str(exc)) from exc
        finally:
            session.close()

    return router
