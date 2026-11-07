"""Read-only REST route for federated SQL."""
from __future__ import annotations

from typing import Any

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from okwan_core import CredentialError, OkwanError, UpstreamError
from pydantic import BaseModel, Field

from .guard import UnsafeStatement
from .mcp import catalog_payload
from .session import DEFAULT_LIMIT, QuerySession, ReusedRun, TooManyRuns


class QueryIn(BaseModel):
    sql: str = Field(description="Read-only SQL over connector.resource tables")
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=5000)
    fresh: bool = Field(
        default=False,
        description=(
            "Run every `recon.*` table live instead of reading this account's run of it from "
            "the last 5 minutes. Live runs are limited to 12 a minute."
        ),
    )


async def _tenant_resolver(tenant):
    """A synchronous resolver over this tenant's stored credentials."""
    from okwan_api.auth import get_store
    from okwan_vault import resolver_for

    return await resolver_for(get_store(), tenant.id)


def sql_run_recorder(tenant, resolver):
    """Store a reconciliation a query ran, like any other run (surface
    `sql`), and keep its result for the reuse window. Returns the run id."""
    from okwan_api.recon_reuse import cache
    from okwan_api.runs import record_run

    async def on_run(spec, started, *, result=None, error=None) -> str | None:
        run_id = await record_run(tenant, spec, "sql", started, result=result, error=error, resolver=resolver)
        if result is not None:
            cache.put(tenant.id, spec, result, run_id)
        return run_id

    return on_run


def sql_run_reuse(tenant):
    """This tenant's run of a declaration from the reuse window, if any."""
    from okwan_api.recon_reuse import cache

    def reuse(spec) -> ReusedRun | None:
        entry = cache.get(tenant.id, spec)
        if entry is None:
            return None
        return ReusedRun(result=entry.result, run_id=entry.run_id, age_seconds=entry.age(cache.now()))

    return reuse


def sql_run_admit(tenant):
    """The per-tenant limit on live runs through SQL (SQL_RECON_TENANT)."""
    import math

    from okwan_api.ratelimit import SQL_RECON_TENANT, limiter

    def admit(runs: int) -> None:
        wait = limiter.take((SQL_RECON_TENANT, tenant.id), cost=runs)
        if wait is not None:
            raise TooManyRuns(
                f"this account has started {SQL_RECON_TENANT.limit} live reconciliation runs in the "
                f"last minute; try again in {math.ceil(wait)} s, or read the recent run without fresh",
                retry_after=wait,
            )

    return admit


def sql_session(tenant, resolver, *, max_records: int, fresh: bool) -> QuerySession:
    return QuerySession(
        resolver=resolver, max_records=max_records, fresh=fresh,
        on_run=sql_run_recorder(tenant, resolver), reuse=sql_run_reuse(tenant),
        admit=sql_run_admit(tenant),
    )


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
        resolver = await _tenant_resolver(tenant)
        session = sql_session(tenant, resolver, max_records=body.limit, fresh=body.fresh)
        try:
            result = await session.query(body.sql)
            # Reading a recent run is reading a result, which is free
            # (§3.4); a query counts when it read a provider.
            if session.upstream_reads:
                await meter(tenant, "rest:query")
            return result
        except TooManyRuns as exc:
            import math

            raise HTTPException(429, str(exc), headers={"Retry-After": str(math.ceil(exc.retry_after))}) from exc
        except UnsafeStatement as exc:
            raise HTTPException(400, str(exc)) from exc
        except duckdb.Error as exc:
            # The SQL itself failed: a typo, an unknown column, or the
            # sandbox refusing a file or the network. The caller's to fix.
            raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
        except CredentialError as exc:
            raise HTTPException(401, str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(exc.status, exc.body) from exc
        except OkwanError as exc:
            raise HTTPException(502, str(exc)) from exc
        finally:
            session.close()

    return router
