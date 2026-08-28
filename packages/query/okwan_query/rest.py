"""Read-only REST route for federated SQL."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from okwan_core import CredentialError, OkwanError, UpstreamError

from okwan_core import all_connectors

from .catalog import catalog
from .guard import UnsafeStatement
from .mcp import catalog_payload
from .session import DEFAULT_LIMIT, QuerySession


class QueryIn(BaseModel):
    sql: str = Field(description="Read-only SQL over connector.resource tables")
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=5000)


async def _tenant_resolver(tenant):
    """Load every connector credential this tenant has, once.

    Returns a synchronous resolver over a plain dict, which is the shape
    the SDK expects everywhere below this point.
    """
    from okwan_api.auth import load_credentials

    loaded: dict[tuple[str, str], str] = {}
    for connector in all_connectors():
        creds = await load_credentials(
            tenant, connector.name, connector.auth.required_fields
        )
        for field, value in creds.items():
            loaded[(connector.name, field)] = value

    def resolve(connector_name: str, fields: tuple[str, ...]) -> dict[str, str]:
        return {f: loaded.get((connector_name, f), "") for f in fields}

    return resolve


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
