"""Reuse a recent reconciliation run for `recon.*` SQL tables.

Naming `recon.across_rails` in a query runs the check live: three upstream
reads of up to 5,000 records each. An agent that queries in a loop would
re-run it every few seconds against the merchant's providers. So a query
reuses the result of this tenant's last run of the same reconciliation
from the last `REUSE_SECONDS` (5 minutes) unless the caller asks for
`fresh`, and a live run is limited per tenant (ratelimit.SQL_RECON_TENANT).

The full result is kept, in memory, rather than read back from the stored
run: a stored run is trimmed for the dashboard (no records, at most
MAX_ROWS rows), so a view rebuilt from it would disagree with a live one.
The run itself was stored when it happened; a reuse names that run's id
and age, and stores nothing new.

Per process and per tenant: one tenant never sees another's entry, and a
credential saved for the tenant forgets its entries, so a fixed key is
read at once rather than after the window.
"""
from __future__ import annotations

import os
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

REUSE_SECONDS = float(os.environ.get("OKWAN_RECON_REUSE_SECONDS", "300"))
#: Results held at once. Each can be a few MB at the record caps; the
#: oldest goes first.
MAX_ENTRIES = 128


@dataclass(slots=True)
class Reused:
    result: Any
    run_id: str | None
    at: float

    def age(self, now: float) -> int:
        return int(now - self.at)


class RunCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl: float = REUSE_SECONDS) -> None:
        self._clock = clock
        self.ttl = ttl
        self._entries: OrderedDict[tuple[str, str, str], Reused] = OrderedDict()

    @staticmethod
    def _key(tenant_id: str, spec) -> tuple[str, str, str]:
        from okwan_recon.across import AcrossRails

        return (tenant_id, "across" if isinstance(spec, AcrossRails) else "pair", spec.name)

    def get(self, tenant_id: str, spec) -> Reused | None:
        key = self._key(tenant_id, spec)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() - entry.at > self.ttl:
            del self._entries[key]
            return None
        return entry

    def put(self, tenant_id: str, spec, result: Any, run_id: str | None) -> None:
        key = self._key(tenant_id, spec)
        self._entries[key] = Reused(result=result, run_id=run_id, at=self._clock())
        self._entries.move_to_end(key)
        while len(self._entries) > MAX_ENTRIES:
            self._entries.popitem(last=False)

    def forget(self, tenant_id: str) -> None:
        for key in [k for k in self._entries if k[0] == tenant_id]:
            del self._entries[key]

    def now(self) -> float:
        return self._clock()

    def clear(self) -> None:
        self._entries.clear()


cache = RunCache()
