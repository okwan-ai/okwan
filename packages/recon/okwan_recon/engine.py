"""Match engine — pure over rows. No transport, no connector imports.

Rules are applied in declaration order and consume their matches, so
an exact reference join runs before the fuzzy fallback ever sees the
records it already paired.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from okwan_core.currency import to_minor

from .declaration import ExactRef, Fuzzy, MSISDN, Reconciliation
from .paths import dig

Row = dict[str, Any]


@dataclass(slots=True)
class MatchedPair:
    left: Row
    right: Row
    rule: str
    confidence: float


@dataclass(slots=True)
class ReconResult:
    name: str
    matched: list[MatchedPair] = field(default_factory=list)
    unmatched_left: list[Row] = field(default_factory=list)
    unmatched_right: list[Row] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, Any]:
        total = len(self.matched) + len(self.unmatched_left)
        return {
            "reconciliation": self.name,
            "matched": len(self.matched),
            "unmatched_left": len(self.unmatched_left),
            "unmatched_right": len(self.unmatched_right),
            "match_rate": round(len(self.matched) / total, 4) if total else 0.0,
        }

    def rows(self) -> list[Row]:
        """Flat, view-shaped output — what the DuckDB view exposes."""
        out: list[Row] = [
            {
                "status": "matched",
                "rule": p.rule,
                "confidence": p.confidence,
                "left": p.left,
                "right": p.right,
            }
            for p in self.matched
        ]
        out += [
            {"status": "unmatched_left", "rule": None, "confidence": 0.0,
             "left": r, "right": None}
            for r in self.unmatched_left
        ]
        out += [
            {"status": "unmatched_right", "rule": None, "confidence": 0.0,
             "left": None, "right": r}
            for r in self.unmatched_right
        ]
        return out


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ref_key(value: Any, case_sensitive: bool) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return s if case_sensitive else s.casefold()


def _identity_key(spec: MSISDN | None, row: Row, side: str) -> str | None:
    if spec is None:
        return None
    path = spec.left if side == "left" else (spec.right or spec.left)
    return spec.normalize(dig(row, path))


def _apply_exact(
    rule: ExactRef, left: list[Row], right: list[Row], out: ReconResult
) -> tuple[list[Row], list[Row]]:
    index: dict[str, list[Row]] = {}
    for r in right:
        key = _ref_key(dig(r, rule.right), rule.case_sensitive)
        if key is not None:
            index.setdefault(key, []).append(r)

    still_left: list[Row] = []
    consumed: set[int] = set()
    for l in left:
        key = _ref_key(dig(l, rule.left), rule.case_sensitive)
        bucket = index.get(key) if key is not None else None
        hit = next((r for r in bucket if id(r) not in consumed), None) if bucket else None
        if hit is None:
            still_left.append(l)
            continue
        consumed.add(id(hit))
        out.matched.append(MatchedPair(l, hit, rule.kind, 1.0))
    return still_left, [r for r in right if id(r) not in consumed]


def _apply_fuzzy(
    rule: Fuzzy,
    identity: MSISDN | None,
    left: list[Row],
    right: list[Row],
    out: ReconResult,
) -> tuple[list[Row], list[Row]]:
    span = rule.window_delta.total_seconds() or 1.0
    still_left: list[Row] = []
    consumed: set[int] = set()

    for l in left:
        l_cur = str(dig(l, rule.currency) or "").upper()
        l_amt = dig(l, rule.amount)
        if l_amt is None:
            still_left.append(l)
            continue
        l_minor = to_minor(l_amt, l_cur)
        l_ts = _as_dt(dig(l, rule.timestamp_left))
        l_id = _identity_key(identity, l, "left")

        best: Row | None = None
        best_gap: float | None = None
        for r in right:
            if id(r) in consumed:
                continue
            if str(dig(r, rule.currency) or "").upper() != l_cur:
                continue
            r_amt = dig(r, rule.amount)
            if r_amt is None:
                continue
            if abs(to_minor(r_amt, l_cur) - l_minor) > rule.amount_tolerance_minor:
                continue
            if identity is not None:
                r_id = _identity_key(identity, r, "right")
                if l_id and r_id and l_id != r_id:
                    continue
            r_ts = _as_dt(dig(r, rule.timestamp_right))
            if l_ts and r_ts:
                gap = abs((l_ts - r_ts).total_seconds())
                if gap > span:
                    continue
            else:
                gap = span
            if best_gap is None or gap < best_gap:
                best, best_gap = r, gap

        if best is None:
            still_left.append(l)
            continue
        consumed.add(id(best))
        confidence = round(max(0.5, 1.0 - (best_gap or 0.0) / span * 0.5), 4)
        out.matched.append(MatchedPair(l, best, rule.kind, confidence))

    return still_left, [r for r in right if id(r) not in consumed]


def match(spec: Reconciliation, left_rows: list[Row], right_rows: list[Row]) -> ReconResult:
    result = ReconResult(name=spec.name)
    left, right = list(left_rows), list(right_rows)

    for rule in spec.keys:
        if not left or not right:
            break
        if isinstance(rule, ExactRef):
            left, right = _apply_exact(rule, left, right, result)
        elif isinstance(rule, Fuzzy):
            left, right = _apply_fuzzy(rule, spec.identity, left, right, result)

    result.unmatched_left = left
    result.unmatched_right = right
    return result
