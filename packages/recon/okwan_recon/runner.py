"""Fetch both sides, then match. The single execution path all three
emitters call — MCP, REST and the DuckDB view cannot diverge."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .declaration import Reconciliation, ResourceRef
from .engine import ReconResult, match
from .fetch import CredentialResolver, env_credentials, fetch_side


def resolve_window(
    spec: Reconciliation, now: datetime | None = None
) -> dict[str, datetime]:
    """The declaration's lookback as concrete bounds, fixed once per run
    so both sides are read against the same instant."""
    delta = spec.lookback_delta
    if delta is None:
        return {}
    end = now or datetime.now(UTC)
    return {"start_date": end - delta, "end_date": end}


def side_overrides(
    ref: ResourceRef,
    window: dict[str, datetime],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    """Window below the side's own params, caller overrides above both.

    A side whose input model has no start_date/end_date is unaffected:
    fetch_rows drops keys the operation does not accept.
    """
    own = {k: v for k, v in window.items() if k not in ref.params}
    return {**own, **(overrides or {})}


async def run(
    spec: Reconciliation,
    resolver: CredentialResolver = env_credentials,
    overrides: dict[str, Any] | None = None,
    max_records: int | None = None,
) -> ReconResult:
    spec.validate_against_registry()
    cap = max_records or spec.max_records
    window = resolve_window(spec)
    left, left_cov = await fetch_side(
        spec.left, resolver, cap, side_overrides(spec.left, window, overrides)
    )
    right, right_cov = await fetch_side(
        spec.right, resolver, cap, side_overrides(spec.right, window, overrides)
    )
    return match(spec, left, right, left_cov, right_cov)
