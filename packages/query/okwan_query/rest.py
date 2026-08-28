"""Read-only REST route for federated SQL."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from okwan_core import CredentialError, OkwanError, UpstreamError

from .catalog import catalog
from .guard import UnsafeStatement
from .mcp import catalog_payload
from .session import DEFAULT_LIMIT, QuerySession


class QueryIn(BaseModel):
    sql: str = Field(description="Read-only SQL over connector.resource tables")
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=5000)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/query", tags=["query"])

    @router.get("/tables")
    async def list_tables() -> dict[str, Any]:
        return catalog_payload()

    @router.post("")
    async def run_query(body: QueryIn) -> dict[str, Any]:
        session = QuerySession(max_records=body.limit)
        try:
            return await session.query(body.sql)
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
