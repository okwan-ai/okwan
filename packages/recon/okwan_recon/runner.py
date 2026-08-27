"""Fetch both sides, then match. The single execution path all three
emitters call — MCP, REST and the DuckDB view cannot diverge."""
from __future__ import annotations

from typing import Any

from .declaration import Reconciliation
from .engine import ReconResult, match
from .fetch import CredentialResolver, env_credentials, fetch_rows


async def run(
    spec: Reconciliation,
    resolver: CredentialResolver = env_credentials,
    overrides: dict[str, Any] | None = None,
    max_records: int | None = None,
) -> ReconResult:
    spec.validate_against_registry()
    cap = max_records or spec.max_records
    left = await fetch_rows(spec.left, resolver, cap, overrides)
    right = await fetch_rows(spec.right, resolver, cap, overrides)
    return match(spec, left, right)
