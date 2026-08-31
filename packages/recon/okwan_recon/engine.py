"""Match engine — pure over rows. No transport, no connector imports.

Rules are applied in declaration order and consume their matches, so
an exact reference join runs before the fuzzy fallback ever sees the
records it already paired.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from okwan_core.currency import to_minor

from .declaration import MSISDN, ExactRef, Fuzzy, Reconciliation
from .paths import dig

Row = dict[str, Any]


@dataclass(slots=True)
class MatchedPair:
    left: Row
    right: Row
    rule: str
    confidence: float
    #: left minus right, in the source's own units. None when the
    #: declaration names no amount, a value is missing, or the two sides
    #: report different currencies — a cross-currency difference is not a
    #: number, and reporting one would be worse than reporting nothing.
    discrepancy_minor: int | None = None
    #: Label of the recorded adjustment accounting for the discrepancy.
    #: A break with a known cause is a different finding from one without.
    explained_by: str | None = None

    @property
    def is_unexplained(self) -> bool:
        """A discrepancy nobody can account for — the one to chase."""
        return self.agrees is False and self.explained_by is None

    @property
    def agrees(self) -> bool | None:
        """True when the amounts match, None when they cannot be compared."""
        return None if self.discrepancy_minor is None else self.discrepancy_minor == 0


@dataclass(slots=True)
class Ambiguity:
    """A record with more than one equally valid counterpart.

    Neither matched nor unmatched: the counterpart exists, but which one
    cannot be determined from the data. Surfacing the candidates is the
    answer — choosing between them would be invention.
    """

    left: Row
    candidates: list[Row]
    rule: str


@dataclass(slots=True)
class ReconResult:
    name: str
    matched: list[MatchedPair] = field(default_factory=list)
    unmatched_left: list[Row] = field(default_factory=list)
    unmatched_right: list[Row] = field(default_factory=list)
    ambiguous: list[Ambiguity] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, Any]:
        total = len(self.matched) + len(self.unmatched_left)
        discrepant = [p for p in self.matched if p.agrees is False]
        unexplained = [p for p in discrepant if p.explained_by is None]
        return {
            "reconciliation": self.name,
            "matched": len(self.matched),
            # A matched pair whose amounts disagree is not a clean match.
            # Collapsing the two hides the discrepancy inside a success count.
            "matched_in_agreement": sum(1 for p in self.matched if p.agrees is True),
            "matched_with_discrepancy": len(discrepant),
            # Split by whether the break has a recorded cause. An explained
            # discrepancy is real but accounted for; an unexplained one is
            # the finding that needs a human.
            "matched_with_explained_discrepancy": len(discrepant) - len(unexplained),
            "matched_with_unexplained_discrepancy": len(unexplained),
            "net_discrepancy_minor": sum(p.discrepancy_minor or 0 for p in discrepant),
            "net_unexplained_minor": sum(p.discrepancy_minor or 0 for p in unexplained),
            "unmatched_left": len(self.unmatched_left),
            "unmatched_right": len(self.unmatched_right),
            # Records whose counterpart exists but cannot be identified.
            # Counted apart from both matches and misses: reporting these
            # as either would overstate what the data supports.
            "ambiguous": len(self.ambiguous),
            "match_rate": round(len(self.matched) / total, 4) if total else 0.0,
        }

    def rows(self) -> list[Row]:
        """Flat, view-shaped output — what the DuckDB view exposes."""
        out: list[Row] = [
            {
                "status": (
                    "matched" if p.agrees is not False
                    else "matched_explained" if p.explained_by
                    else "matched_discrepant"
                ),
                "rule": p.rule,
                "confidence": p.confidence,
                "discrepancy": p.discrepancy_minor,
                "explained_by": p.explained_by,
                "left": p.left,
                "right": p.right,
            }
            for p in self.matched
        ]
        out += [
            {"status": "unmatched_left", "rule": None, "confidence": 0.0,
             "discrepancy": None, "left": r, "right": None}
            for r in self.unmatched_left
        ]
        out += [
            {"status": "unmatched_right", "rule": None, "confidence": 0.0,
             "discrepancy": None, "left": None, "right": r}
            for r in self.unmatched_right
        ]
        out += [
            {"status": "ambiguous", "rule": a.rule, "confidence": 0.0,
             "discrepancy": None, "left": a.left, "right": None,
             "candidates": a.candidates}
            for a in self.ambiguous
        ]
        return out


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _ref_key(value: Any, case_sensitive: bool) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return s if case_sensitive else s.casefold()


def _identity_disagrees(spec: MSISDN | None, left: Row, right: Row) -> bool:
    """True only when both sides carry numbers that cannot be the same."""
    if spec is None:
        return False
    verdict = spec.agrees(
        dig(left, spec.left), dig(right, spec.right or spec.left)
    )
    return verdict is False


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
    """Pair on amount, currency and time window.

    Collects every viable candidate rather than the closest one. A single
    candidate is a match; several are an ambiguity. Candidates are not
    consumed when ambiguous — they remain available to later rules and to
    the unmatched report, since none of them was claimed.
    """
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

        candidates: list[tuple[Row, float]] = []
        for r in right:
            if id(r) in consumed:
                continue
            if str(dig(r, rule.right_currency) or "").upper() != l_cur:
                continue
            r_amt = dig(r, rule.right_amount)
            if r_amt is None:
                continue
            if abs(to_minor(r_amt, l_cur) - l_minor) > rule.amount_tolerance_minor:
                continue
            if _identity_disagrees(identity, l, r):
                continue
            r_ts = _as_dt(dig(r, rule.timestamp_right))
            if l_ts and r_ts:
                gap = abs((l_ts - r_ts).total_seconds())
                if gap > span:
                    continue
            else:
                gap = span
            candidates.append((r, gap))

        if not candidates:
            still_left.append(l)
            continue

        if len(candidates) > 1:
            # Equal amount, equal currency, both in window. Time proximity
            # is not evidence enough to choose — settlement lag is the least
            # reliable signal a rail has.
            out.ambiguous.append(
                Ambiguity(left=l, candidates=[r for r, _ in candidates], rule=rule.kind)
            )
            continue

        best, gap = candidates[0]
        consumed.add(id(best))
        confidence = round(max(0.5, 1.0 - gap / span * 0.5), 4)
        out.matched.append(MatchedPair(l, best, rule.kind, confidence))

    return still_left, [r for r in right if id(r) not in consumed]


def _score_discrepancies(spec: Reconciliation, result: ReconResult) -> None:
    """Compare amounts on every pair, however it was matched.

    Runs after all rules so exact-reference pairs — which never read an
    amount while matching — are compared too. That is where the useful
    finding lives: a pair the reference says belongs together while the
    money says otherwise.
    """
    ref = spec.resolved_amount
    if ref is None:
        return
    for pair in result.matched:
        l_amt, r_amt = dig(pair.left, ref.left), dig(pair.right, ref.right_path)
        if l_amt is None or r_amt is None:
            continue
        l_cur = str(dig(pair.left, ref.currency) or "").upper()
        r_cur = str(dig(pair.right, ref.right_currency) or "").upper()
        if l_cur and r_cur and l_cur != r_cur:
            continue
        try:
            pair.discrepancy_minor = int(l_amt) - int(r_amt)
        except (TypeError, ValueError):
            continue

        for rule in spec.explains:
            row = pair.left if rule.side == "left" else pair.right
            if rule.accounts_for(dig(row, rule.path), pair.discrepancy_minor):
                pair.explained_by = rule.label
                break


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
    _score_discrepancies(spec, result)
    return result
