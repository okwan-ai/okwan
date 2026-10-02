"""One order ledger against every rail that collects for it.

Each two-sided declaration answers "was this order paid on this rail?".
No single one can answer "was it paid once?" — an order collected on
both Stripe and PayPal is a clean match on each. This folds several
declarations sharing a left side into one verdict per order.

The fold is a declaration like any other: `AcrossRails` produces an MCP
tool, a REST route and a DuckDB view, with no per-fold code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, field_validator

from .coverage import Coverage
from .declaration import Frozen, Reconciliation
from .engine import ReconResult, match
from .fetch import CredentialResolver, env_credentials, fetch_side
from .paths import dig

Row = dict[str, Any]

#: Per-order outcomes, in the order they are decided.
OUTCOMES = (
    "collected_twice",
    "split_tender",
    "collected_inconsistent",
    "collected",
    "unverifiable",
    "uncollected",
)


class Rail(Frozen):
    """One member declaration and where its rail states what it took."""

    reconciliation: str = Field(description="Name of a registered Reconciliation")
    collected: str = Field(
        description=(
            "Gross amount the rail collected, minor units, on its rows. Gross "
            "because fees and refunds are each rail's own business; the "
            "question here is how much was taken for the order."
        )
    )
    currency: str = "currency"


class AcrossRails(Frozen):
    name: str
    title: str | None = None
    description: str = ""
    rails: list[Rail] = Field(min_length=2)
    ledger_total: str = Field(
        description=(
            "The full order total on the ledger row, minor units. Not what the "
            "ledger says it received: a ledger only sees its own checkout, so "
            "it cannot know a second rail collected too."
        )
    )
    ledger_currency: str = "currency"
    tolerance_bps: int = Field(
        default=100,
        ge=0,
        le=10_000,
        description=(
            "How near a rail's collection must be to the order total to count "
            "as the full total, in basis points of it. Integer arithmetic only"
        ),
    )

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", v):
            raise ValueError("name must be lower_snake_case")
        return v

    @property
    def display_title(self) -> str:
        return self.title or f"Across {self.name.replace('_', ' ')}"

    @property
    def tool_name(self) -> str:
        return f"reconcile_across_{self.name}"

    @property
    def view_name(self) -> str:
        return f"recon.across_{self.name}"

    @property
    def rest_path(self) -> str:
        return f"/v1/reconciliations/across/{self.name}"

    def members(self) -> list[Reconciliation]:
        from .registry import get

        return [get(r.reconciliation) for r in self.rails]

    def validate_against_registry(self) -> None:
        """Every member resolves, is itself valid, and reads the same
        ledger the same way — otherwise "the same order" means nothing
        across them. Named paths must be fields the schemas produce."""
        from okwan_core import get as get_connector

        from .registry import get

        members: list[Reconciliation] = []
        for rail in self.rails:
            try:
                spec = get(rail.reconciliation)
            except KeyError:
                raise ValueError(
                    f"{self.name}: unknown reconciliation {rail.reconciliation!r}"
                ) from None
            spec.validate_against_registry()
            members.append(spec)

        first = members[0]
        for spec in members[1:]:
            if spec.left != first.left:
                raise ValueError(
                    f"{self.name}: {spec.name} reads {spec.left.qualified} with "
                    f"{spec.left.params}, but {first.name} reads "
                    f"{first.left.qualified} with {first.left.params}"
                )
            if spec.lookback != first.lookback:
                raise ValueError(
                    f"{self.name}: {spec.name} looks back {spec.lookback}, "
                    f"{first.name} {first.lookback}; the ledger is read once"
                )

        def fields(ref) -> set[str]:
            schema = get_connector(ref.connector).resources[ref.resource].schema
            return set(schema.model_fields) | set(schema.model_computed_fields)

        if self.ledger_total not in fields(first.left):
            raise ValueError(
                f"{self.name}: ledger has no field {self.ledger_total!r}"
            )
        for rail, spec in zip(self.rails, members, strict=True):
            if rail.collected not in fields(spec.right):
                raise ValueError(
                    f"{self.name}: {spec.right.qualified} has no field "
                    f"{rail.collected!r}"
                )


# ── result ──────────────────────────────────────────────────────────

@dataclass(slots=True)
class RailVerdict:
    """What one rail said about one order."""

    rail: str
    reconciliation: str
    #: matched · unmatched · ambiguous · unverifiable
    status: str
    collected_minor: int | None = None
    currency: str | None = None
    discrepancy_minor: int | None = None
    explained_by: str | None = None
    reason: str | None = None
    record: Row | None = None
    candidates: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "rail": self.rail,
            "reconciliation": self.reconciliation,
            "status": self.status,
            "collected_minor": self.collected_minor,
            "currency": self.currency,
            "discrepancy_minor": self.discrepancy_minor,
            "explained_by": self.explained_by,
            "reason": self.reason,
            "candidates": self.candidates,
            "record": self.record,
        }


@dataclass(slots=True)
class OrderVerdict:
    order: Row
    outcome: str
    order_total_minor: int | None
    rails: list[RailVerdict]
    reason: str | None = None

    @property
    def collected_on(self) -> list[str]:
        return [r.rail for r in self.rails if r.status == "matched"]

    @property
    def collected_minor(self) -> int | None:
        amounts = [r.collected_minor for r in self.rails if r.status == "matched"]
        if not amounts or any(a is None for a in amounts):
            return None
        return sum(a for a in amounts if a is not None)

    @property
    def unverified_rails(self) -> list[str]:
        """Rails that could not rule this order in or out. On a collected
        order these are why a second collection cannot be excluded."""
        return [r.rail for r in self.rails if r.status in ("unverifiable", "ambiguous")]


@dataclass(slots=True)
class AcrossResult:
    name: str
    orders: list[OrderVerdict] = field(default_factory=list)
    ledger_coverage: Coverage | None = None
    #: Each member's own result, for what the fold does not cover: rail
    #: records with no order on the ledger.
    members: dict[str, ReconResult] = field(default_factory=dict)

    @property
    def summary(self) -> dict[str, Any]:
        counts = {o: 0 for o in OUTCOMES}
        for v in self.orders:
            counts[v.outcome] += 1
        twice = [v for v in self.orders if v.outcome == "collected_twice"]
        return {
            "reconciliation": self.name,
            "orders": len(self.orders),
            **counts,
            # The orders taken twice, at their order totals, and what was
            # taken beyond those totals — the figure owed back.
            "collected_twice_minor": sum(v.order_total_minor or 0 for v in twice),
            "overcollected_minor": sum(
                (v.collected_minor or 0) - (v.order_total_minor or 0) for v in twice
            ),
            "ledger_coverage": (
                self.ledger_coverage.as_dict() if self.ledger_coverage else None
            ),
            "rails": {
                name: {
                    "coverage": (
                        r.right_coverage.as_dict() if r.right_coverage else None
                    ),
                    # Rail records with no order: outside the per-order fold.
                    "unmatched_right": len(r.unmatched_right),
                    "unverifiable_right": len(r.unverifiable_right),
                }
                for name, r in self.members.items()
            },
        }

    def rows(self) -> list[Row]:
        return [
            {
                "outcome": v.outcome,
                "order": v.order,
                "order_total_minor": v.order_total_minor,
                "collected_minor": v.collected_minor,
                "collected_on": v.collected_on,
                "unverified_rails": v.unverified_rails,
                "reason": v.reason,
                "rails": [r.as_dict() for r in v.rails],
            }
            for v in self.orders
        ]


# ── fold ────────────────────────────────────────────────────────────

def _verdicts(
    spec: Reconciliation, rail: Rail, result: ReconResult
) -> dict[int, RailVerdict]:
    """This rail's verdict on every ledger row, keyed by row identity.

    The ledger is fetched once and shared, so the same dict is the left
    record in every member's result.
    """
    name = spec.right.connector
    out: dict[int, RailVerdict] = {}
    for p in result.matched:
        out[id(p.left)] = RailVerdict(
            rail=name, reconciliation=spec.name, status="matched",
            collected_minor=_int(dig(p.right, rail.collected)),
            currency=_cur(dig(p.right, rail.currency)),
            discrepancy_minor=p.discrepancy_minor, explained_by=p.explained_by,
            record=p.right,
        )
    for a in result.ambiguous:
        out[id(a.left)] = RailVerdict(
            rail=name, reconciliation=spec.name, status="ambiguous",
            candidates=len(a.candidates),
            reason=f"{len(a.candidates)} equal candidates on {name}; none identifiable",
        )
    for u in result.unverifiable_left:
        out[id(u.record)] = RailVerdict(
            rail=name, reconciliation=spec.name, status="unverifiable", reason=u.reason
        )
    for r in result.unmatched_left:
        out[id(r)] = RailVerdict(rail=name, reconciliation=spec.name, status="unmatched")
    return out


def fold(
    spec: AcrossRails,
    ledger: list[Row],
    results: list[tuple[Reconciliation, Rail, ReconResult]],
    ledger_coverage: Coverage | None = None,
) -> AcrossResult:
    """One verdict per order. Pure: no transport, no registry."""
    per_rail = [_verdicts(s, rail, r) for s, rail, r in results]
    out = AcrossResult(
        name=spec.name,
        ledger_coverage=ledger_coverage,
        members={s.name: r for s, _, r in results},
    )
    for order in ledger:
        rails = [v[id(order)] for v in per_rail if id(order) in v]
        out.orders.append(_decide(spec, order, rails))
    return out


def _decide(spec: AcrossRails, order: Row, rails: list[RailVerdict]) -> OrderVerdict:
    total = _int(dig(order, spec.ledger_total))
    matched = [r for r in rails if r.status == "matched"]

    def verdict(outcome: str, reason: str | None = None) -> OrderVerdict:
        return OrderVerdict(order, outcome, total, rails, reason)

    # A match is positive evidence, whatever another rail could not read.
    if len(matched) == 1:
        return verdict("collected")
    if len(matched) > 1:
        return _classify_multi(spec, order, total, matched, verdict)

    # No rail has it. "Uncollected" needs every rail to have looked.
    unread = [r for r in rails if r.status in ("unverifiable", "ambiguous")]
    if unread or len(rails) < len(spec.rails):
        reasons = [f"{r.rail}: {r.reason}" for r in unread]
        return verdict("unverifiable", "; ".join(reasons) or "a rail did not report")
    return verdict("uncollected")


def _classify_multi(spec, order, total, matched, verdict) -> OrderVerdict:
    amounts = [r.collected_minor for r in matched]
    listed = ", ".join(f"{r.rail} {r.collected_minor}" for r in matched)
    if total is None or any(a is None for a in amounts):
        return verdict("collected_inconsistent", f"amount missing: {listed}")
    order_cur = _cur(dig(order, spec.ledger_currency))
    if any(r.currency != order_cur for r in matched):
        return verdict(
            "collected_inconsistent",
            f"currencies differ from the order's {order_cur}: "
            + ", ".join(f"{r.rail} {r.currency}" for r in matched),
        )

    def near(x: int) -> bool:
        return abs(x - total) * 10_000 <= spec.tolerance_bps * abs(total)

    if all(near(a) for a in amounts):
        return verdict("collected_twice", f"each rail took the full total: {listed}")
    if near(sum(amounts)):
        return verdict("split_tender")
    return verdict(
        "collected_inconsistent",
        f"{listed} against an order total of {total}: neither a duplicate nor a split",
    )


def _int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _cur(value: Any) -> str | None:
    return str(value).upper() if value else None


# ── run ─────────────────────────────────────────────────────────────

async def run_across(
    spec: AcrossRails,
    resolver: CredentialResolver = env_credentials,
    overrides: dict[str, Any] | None = None,
    max_records: int | None = None,
) -> AcrossResult:
    """Fetch the ledger once, each rail once, match each pair, fold."""
    from .runner import resolve_window, side_overrides

    spec.validate_against_registry()
    members = spec.members()
    first = members[0]
    cap = max_records or first.max_records
    window = resolve_window(first)

    ledger, ledger_cov = await fetch_side(
        first.left, resolver, cap, side_overrides(first.left, window, overrides)
    )
    results: list[tuple[Reconciliation, Rail, ReconResult]] = []
    for member, rail in zip(members, spec.rails, strict=True):
        right, right_cov = await fetch_side(
            member.right, resolver, cap, side_overrides(member.right, window, overrides)
        )
        results.append((member, rail, match(member, ledger, right, ledger_cov, right_cov)))
    return fold(spec, ledger, results, ledger_cov)
