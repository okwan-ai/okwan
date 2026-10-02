"""What one side of a reconciliation actually read.

An unmatched record is a finding only if its counterpart would have been
read had it existed. A side truncated at the record cap, or bounded to a
window, can leave a real counterpart unfetched — and "no payment was
read" must not be reported as "no payment exists".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Coverage:
    #: The operation read, e.g. ``paypal.transactions.list``.
    source: str
    records: int
    cap: int
    #: The cap was reached while upstream still had more. Which records
    #: were cut depends on the rail's ordering, so nothing on the other
    #: side can be judged against this one.
    truncated: bool
    #: The time span the connector says its walk covered. None on both
    #: means the list is not bounded in time.
    span_start: datetime | None = None
    span_end: datetime | None = None
    #: Set when the upstream ledger's own currency clamped `span_end`.
    horizon: datetime | None = None

    @property
    def bounded(self) -> bool:
        return self.span_start is not None or self.span_end is not None

    def reads(self, at: datetime | None) -> tuple[bool, str | None]:
        """Whether a counterpart dated `at` would have been fetched here.

        Returns the verdict and, when False, why — phrased for the record
        on the other side, which is reported unverifiable with it.
        """
        if self.truncated:
            return False, (
                f"{self.source} stopped at the {self.cap}-record cap with more "
                "available; a counterpart may exist but was not read"
            )
        if not self.bounded:
            return True, None
        if at is None:
            return False, (
                f"{self.source} read only {self._span()}, and this record has "
                "no timestamp to place inside it"
            )
        if self.span_start is not None and at < self.span_start:
            return False, (
                f"{self.source} read from {_iso(self.span_start)}; this record "
                f"at {_iso(at)} is earlier"
            )
        if self.span_end is not None and at > self.span_end:
            limit = (
                "its ledger horizon"
                if self.horizon is not None and self.span_end == self.horizon
                else "the end of its span"
            )
            return False, (
                f"{self.source} read to {_iso(self.span_end)} ({limit}); this "
                f"record at {_iso(at)} is later"
            )
        return True, None

    def _span(self) -> str:
        start = _iso(self.span_start) if self.span_start else "the beginning"
        end = _iso(self.span_end) if self.span_end else "now"
        return f"{start} to {end}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "records": self.records,
            "cap": self.cap,
            "truncated": self.truncated,
            "span_start": _iso(self.span_start),
            "span_end": _iso(self.span_end),
            "horizon": _iso(self.horizon),
        }


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None
