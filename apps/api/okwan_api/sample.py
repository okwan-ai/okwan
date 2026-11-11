"""The sample check: a first result with nothing connected.

`GET /v1/sample/reconciliations/across/rails` answers as
`GET /v1/reconciliations/across/rails` does, for a made-up store's week
(okwan_recon.sample): the real fold over sample rows. It needs no key and
no account, because it holds nothing of anyone's: a new account sees a
finished check in its first minute, and an integrator sees the response
they will parse before issuing a key. Nothing is stored or metered.

`trim=true` gives the rows as a stored run carries them, with
`twice_currency`: the shape the dashboard draws.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from okwan_recon import sample
from okwan_recon.across import OUTCOMES, AcrossResult
from okwan_recon.emitters.mcp import paged
from okwan_recon.emitters.rest import rest_page
from okwan_recon.paging import DEFAULT_ROWS, MAX_ROWS
from okwan_recon.registry import get_across

from .ratelimit import SAMPLE_IP, client_ip, enforce
from .runs import twice_currency

#: The result for the current hour: the week is counted back from the hour
#: it runs in, so every caller in that hour gets the same answer and the
#: route costs one run an hour, however many ask.
_held: tuple[datetime, AcrossResult] | None = None


async def _result(now: datetime | None = None) -> AcrossResult:
    global _held
    hour = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    if _held is None or _held[0] != hour:
        _held = (hour, await sample.run(hour))
    return _held[1]


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/sample", tags=["sample"])

    @router.get("/reconciliations/across/{name}")
    async def sample_across(
        name: str,
        request: Request,
        limit: int = Query(DEFAULT_ROWS, ge=1, le=MAX_ROWS),
        outcome: str = Query("all", pattern=f"^(all|{'|'.join(OUTCOMES)})$"),
        cursor: str | None = None,
        trim: bool = Query(False, description="Rows as a stored run carries them, as the dashboard draws them"),
    ) -> dict[str, Any]:
        """A finished check over a made-up store's week: the same fold,
        figures and response as `GET /v1/reconciliations/across/{name}`,
        with no key and nothing of yours read. `sample` is always true."""
        if name != sample.FOLD:
            raise HTTPException(404, f"no sample for '{name}'; the sample check is '{sample.FOLD}'")
        enforce(request, (SAMPLE_IP, client_ip(request)))
        result = await _result()
        rows = result.trimmed_rows(get_across(name).ledger_currency) if trim else result.rows()
        page = rest_page(paged(result.summary, rows, "outcome", outcome, limit, cursor))
        extra = {"twice_currency": twice_currency(rows, result.summary)} if trim else {}
        return {"sample": True, "store": sample.STORE, **extra, **page}

    return router
