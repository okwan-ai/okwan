"""Match engine — pure over rows. No transport, no connector imports.

Rules are applied in declaration order and consume their matches, so
an exact reference join runs before the fuzzy fallback ever sees the
records it already paired.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from itertools import combinations
from typing import Any

from .coverage import Coverage
from .declaration import (
    MSISDN,
    Agree,
    ExactRef,
    Fuzzy,
    GroupRef,
    GroupSum,
    PeriodGroup,
    Reconciliation,
)
from .paths import dig

Row = dict[str, Any]

#: How much comparing one fallback rule may do over one reconciliation
#: (E7). A record offered to another as a possible member of its group, and
#: a step of a subset search, each count one; a pair weighed in full by the
#: fuzzy rule counts PAIR. The rows are the merchant's own, so without a
#: bound the merchant decides how long a check takes: thousands of records
#: sharing an amount and a day cost the square of their number. Records
#: are filed by currency, amount and date first, so ordinary data stays far
#: below this; past it the rule stops and says so. A second or two of work.
MATCH_WORK = 4_000_000
PAIR = 4
#: Candidates listed for one ambiguous record. Past this it is ambiguous
#: whatever the rest are, so the rest are not looked at.
CANDIDATES_LISTED = 50
#: Why a record was left undecided when a rule stopped (never "unmatched":
#: nothing was established about it).
STOPPED = ("the fallback match stopped before it reached this record: too many records share an "
           "amount and a date to compare them all; none chosen")
#: And for a record of the other side that a record the rule did not reach
#: might have paired with: not called unmatched either.
STOPPED_OTHER = ("the fallback match stopped before every record that might pair with this one was "
                 "looked at; whether it has a counterpart was not established")


class _Work:
    """What one rule may still spend of MATCH_WORK."""

    __slots__ = ("left",)

    def __init__(self, allowance: int | None = None) -> None:
        self.left = MATCH_WORK if allowance is None else allowance

    def spend(self, n: int = 1) -> bool:
        """Take `n`; False once the allowance is gone."""
        self.left -= n
        return self.left >= 0

    @property
    def spent_out(self) -> bool:
        return self.left < 0


#: Every row status `ReconResult.rows()` emits — the filter vocabulary.
STATUSES = (
    "matched",
    "matched_explained",
    "matched_discrepant",
    "unmatched_left",
    "unmatched_right",
    "ambiguous",
    "unverifiable_left",
    "unverifiable_right",
)


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
    #: A group match (GroupRef, GroupSum): the records on the `grouped`
    #: side that together answer the other side's one record. That side's
    #: `left`/`right` is then a summary carrying them under "members".
    members: list[Row] | None = None
    grouped: str | None = None
    #: How this pair was established and judged (E4): the key and its
    #: value, the window or lag and where the pair fell in it, the
    #: amounts compared, the members of a group, and each part of the
    #: explanation. References and amounts only, never a record.
    evidence: dict[str, Any] = field(default_factory=dict)

    def value(self, side: str, path: str) -> Any:
        """A field of one side. On a group's side: the sum when every
        member's value is a whole number (amounts, fees), the shared value
        when they all agree (a currency), otherwise None."""
        record = self.left if side == "left" else self.right
        if self.members is None or self.grouped != side:
            return dig(record, path)
        if not self.members:
            # A period with nothing in it (E6): its amounts sum to zero, and
            # amounts are the `*_minor` fields by convention; nothing else
            # has a value.
            return 0 if path.endswith("_minor") else None
        values = [dig(m, path) for m in self.members]
        if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            return sum(values)
        first = values[0]
        return first if all(v == first for v in values) else None

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
    #: Which side `left` (the record in question) came from. A group rule
    #: whose one record is on the right reports it there.
    side: str = "left"
    #: Why, when it is not simply "several equal candidates".
    reason: str | None = None
    #: For a group rule: each grouping that fitted, so a person can see
    #: the choice the engine declined to make.
    alternatives: list[list[Row]] | None = None
    #: There are more candidates than are listed (CANDIDATES_LISTED).
    more: bool = False


@dataclass(slots=True)
class Unverifiable:
    """A record whose counterpart side could not have read its match.

    Neither matched nor unmatched: the other side stopped at its record
    cap, or its span does not reach this record's date, so absence there
    proves nothing. Reported with the reason rather than as a miss —
    "no payment was read" is not "no payment exists".
    """

    record: Row
    reason: str


@dataclass(slots=True)
class ReconResult:
    name: str
    matched: list[MatchedPair] = field(default_factory=list)
    unmatched_left: list[Row] = field(default_factory=list)
    unmatched_right: list[Row] = field(default_factory=list)
    ambiguous: list[Ambiguity] = field(default_factory=list)
    unverifiable_left: list[Unverifiable] = field(default_factory=list)
    unverifiable_right: list[Unverifiable] = field(default_factory=list)
    #: Records a rule knows are waiting rather than missing (a sale after
    #: the latest return imported for its state), by side, kept apart from
    #: the misses until coverage is applied.
    waiting: dict[str, list[Unverifiable]] = field(default_factory=lambda: {"left": [], "right": []})
    #: What each side read. None when the caller supplied no coverage, in
    #: which case nothing can be classed unverifiable.
    left_coverage: Coverage | None = None
    right_coverage: Coverage | None = None

    @property
    def summary(self) -> dict[str, Any]:
        total = len(self.matched) + len(self.unmatched_left)
        unverifiable = len(self.unverifiable_left) + len(self.unverifiable_right)
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
            # Findings: the counterpart side read this record's date and
            # holds nothing for it.
            "unmatched_left": len(self.unmatched_left),
            "unmatched_right": len(self.unmatched_right),
            # Records whose counterpart exists but cannot be identified.
            # Counted apart from both matches and misses: reporting these
            # as either would overstate what the data supports.
            "ambiguous": len(self.ambiguous),
            # Records whose counterpart side was not read for them. Not
            # findings. Each row carries why.
            "unverifiable_left": len(self.unverifiable_left),
            "unverifiable_right": len(self.unverifiable_right),
            # None while anything is unverifiable. Excluding those records
            # gives a rate over whatever happened to be read — 1.0 from a
            # three-record read — and including them counts them on no
            # evidence. No figure is better than one the data cannot support;
            # the counts above remain.
            "match_rate": (
                None if unverifiable
                else round(len(self.matched) / total, 4) if total
                else 0.0
            ),
            "coverage": (
                None
                if self.left_coverage is None and self.right_coverage is None
                else {
                    "left": self.left_coverage.as_dict() if self.left_coverage else None,
                    "right": self.right_coverage.as_dict() if self.right_coverage else None,
                }
            ),
        }

    def trimmed_rows(
        self, left_ref: str | None = None, right_ref: str | None = None,
        limit: int | None = None,
    ) -> list[Row]:
        """The rows without their records: status, rule, confidence, the
        discrepancy and its explanation, the reason, each side's one human
        reference (the first exact key, see `ref_paths`), and for an
        ambiguous row how many candidates there were, never the candidates
        themselves. Findings first (PAIR_DISPLAY_ORDER), so a cut at
        `limit` drops clean matches, not breaks. What is stored and shown;
        a record never leaves the process this way."""
        rank = {s: i for i, s in enumerate(PAIR_DISPLAY_ORDER)}
        rows = sorted(self.rows(), key=lambda r: rank.get(r["status"], len(rank)))
        out = [
            {
                "status": r["status"],
                "rule": r.get("rule"),
                "confidence": r.get("confidence"),
                "discrepancy": r.get("discrepancy"),
                "explained_by": r.get("explained_by"),
                "reason": r.get("reason"),
                "candidates": (
                    len(r["candidates"]) if isinstance(r.get("candidates"), list) else None
                ),
                # A group match: how many records answered the one.
                "members": r.get("members"),
                # How the verdict was reached (E4). References and amounts.
                "evidence": r.get("evidence"),
                "left_ref": _ref(r.get("left"), left_ref),
                "right_ref": _ref(r.get("right"), right_ref),
            }
            for r in rows
        ]
        return out[:limit] if limit is not None else out

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
                "reason": None,
                "left": p.left,
                "right": p.right,
                "members": len(p.members) if p.members is not None else None,
                "evidence": p.evidence,
            }
            for p in self.matched
        ]
        # A miss is a finding only because the other side looked: say what
        # it read.
        looked_right = {"counterpart": self.right_coverage.as_dict()} if self.right_coverage else None
        looked_left = {"counterpart": self.left_coverage.as_dict()} if self.left_coverage else None
        out += [
            {"status": "unmatched_left", "rule": None, "confidence": 0.0,
             "discrepancy": None, "reason": None, "left": r, "right": None,
             "evidence": looked_right}
            for r in self.unmatched_left
        ]
        out += [
            {"status": "unmatched_right", "rule": None, "confidence": 0.0,
             "discrepancy": None, "reason": None, "left": None, "right": r,
             "evidence": looked_left}
            for r in self.unmatched_right
        ]
        out += [
            {"status": "ambiguous", "rule": a.rule, "confidence": 0.0,
             "discrepancy": None, "reason": a.reason,
             "left": a.left if a.side == "left" else None,
             "right": a.left if a.side == "right" else None,
             "candidates": a.candidates,
             "evidence": {
                 "candidates": len(a.candidates),
                 **({"more": True} if a.more else {}),
                 **({"groupings": [len(g) for g in a.alternatives]} if a.alternatives else {}),
             }}
            for a in self.ambiguous
        ]
        out += [
            {"status": "unverifiable_left", "rule": None, "confidence": 0.0,
             "discrepancy": None, "reason": u.reason, "left": u.record, "right": None,
             "evidence": looked_right}
            for u in self.unverifiable_left
        ]
        out += [
            {"status": "unverifiable_right", "rule": None, "confidence": 0.0,
             "discrepancy": None, "reason": u.reason, "left": None, "right": u.record,
             "evidence": looked_left}
            for u in self.unverifiable_right
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
        parsed = datetime.fromisoformat(str(value))
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


def _agreement(rules: tuple[Agree, ...], left: Row, right: Row) -> list[str] | None:
    """The fields both records carry and agree on, or None when one of
    them contradicts. A field either side lacks says nothing either way."""
    agreed: list[str] = []
    for rule in rules:
        l_key = _ref_key(dig(left, rule.left), rule.case_sensitive)
        r_key = _ref_key(dig(right, rule.right_path), rule.case_sensitive)
        if l_key is None or r_key is None:
            continue
        if l_key != r_key:
            return None
        agreed.append(rule.left)
    return agreed


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
        out.matched.append(MatchedPair(l, hit, rule.kind, 1.0, evidence={
            "rule": rule.kind,
            "key": {"left": rule.left, "right": rule.right, "value": str(dig(l, rule.left))},
        }))
    return still_left, [r for r in right if id(r) not in consumed]


def _minor(value: Any) -> int | None:
    """An amount a declaration names, already in integer minor units.

    Fuzzy paths point at `*_minor` fields, so the engine compares them as
    stored. It does not convert from major units: scaling both sides by
    the currency factor kept equality but made `amount_tolerance_minor`
    100x tighter than its name.
    """
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _lag_days(lag, day: date, of: str = "left") -> tuple[int, int] | None:
    """The other side's days a settlement lag admits for a record dated
    `day` on the `of` side, as ordinals (first, last), walked on the lag's
    own calendar. Business days only grow with distance, so the admitted
    days are one unbroken run, and the walk ends a few days past
    `max_days`: it never depends on how far apart two records' dates are."""
    from .calendar import is_business_day

    # The other side is the later one when it is the right of a lag that
    # runs right after left, or the left of one that runs left after right.
    step = 1 if (of == "left") == (lag.direction == "right_after_left") else -1
    first = day if lag.min_days == 0 else None
    last, count, at = day, 0, day
    try:
        while True:
            # Going forward the day arrived at is counted; going back, the
            # day left behind (business days in (earlier, later]).
            if step == 1:
                at += timedelta(days=1)
                count += is_business_day(at)
            else:
                count += is_business_day(at)
                at -= timedelta(days=1)
            if count > lag.max_days:
                break
            if first is None and count >= lag.min_days:
                first = at
            last = at
    except OverflowError:  # the end of the calendar
        pass
    if first is None:
        return None
    return (first.toordinal(), last.toordinal()) if step == 1 else (last.toordinal(), first.toordinal())


class _Filed:
    """One side's records filed for the fuzzy rule, each read once: under
    their currency and amount, in date order, and again under their tag
    when the rule has one. The tag is a record's value for the first field
    a pair must not contradict (a card's last four digits), or None. A left
    record is then offered only the records that could pair with it: by
    its tag when it has one, and at most two places an amount either way."""

    __slots__ = ("minors", "tagging", "timed", "untimed")

    def __init__(self, tagging: bool) -> None:
        self.tagging = tagging
        #: (currency, amount) or (currency, amount, tag) -> [(when, position)], sorted.
        self.timed: dict[tuple, list[tuple[float, int]]] = {}
        #: The same keys -> [position], for records with no date.
        self.untimed: dict[tuple, list[int]] = {}
        #: currency -> its amounts, sorted.
        self.minors: dict[str, list[int]] = {}

    def add(self, currency: str, minor: int, tag: str | None, when: float | None, position: int) -> None:
        for key in ((currency, minor), (currency, minor, tag)) if self.tagging else ((currency, minor),):
            if when is None:
                self.untimed.setdefault(key, []).append(position)
            else:
                self.timed.setdefault(key, []).append((when, position))

    def close(self) -> None:
        for entries in self.timed.values():
            entries.sort()
        amounts: dict[str, set[int]] = {}
        for key in list(self.timed) + list(self.untimed):
            amounts.setdefault(key[0], set()).add(key[1])
        self.minors = {currency: sorted(found) for currency, found in amounts.items()}

    def amounts_near(self, currency: str, minor: int, tolerance: int) -> list[int]:
        """This currency's filed amounts within tolerance of one."""
        found = self.minors.get(currency)
        if not found:
            return []
        return found[bisect_left(found, minor - tolerance):bisect_right(found, minor + tolerance)]

    def keys(self, currency: str, amounts: list[int], tag: str | None) -> list[tuple]:
        """Where a record of this tag looks: under its own tag and under
        none (a record with no tag contradicts nothing), or, having no tag
        itself, among all the records of each amount. Never a place per tag
        on the other side: those are the other side's to choose."""
        if not self.tagging or tag is None:
            return [(currency, m) for m in amounts]
        return [(currency, m, t) for m in amounts for t in (tag, None)]

    def all(self, key: tuple) -> list[int]:
        return [pos for _, pos in self.timed.get(key, ())] + list(self.untimed.get(key, ()))

    def within(self, key: tuple, first: float | None, last: float | None, undated: bool) -> list[int]:
        """Positions under a key dated in [first, last] (all of them when
        `first` is None), plus the undated ones when asked."""
        entries = self.timed.get(key, ())
        if first is None:
            out = [pos for _, pos in entries]
        else:
            lo = bisect_left(entries, (first, -1))
            hi = bisect_right(entries, (last, float("inf")))
            out = [pos for _, pos in entries[lo:hi]]
        if undated:
            out += self.untimed.get(key, ())
        return out


def _apply_fuzzy(
    rule: Fuzzy,
    identity: MSISDN | None,
    left: list[Row],
    right: list[Row],
    out: ReconResult,
    work: _Work | None = None,
) -> tuple[list[Row], list[Row]]:
    """Pair on amount, currency and time window.

    Collects every viable candidate rather than the closest one. A single
    candidate is a match; several are an ambiguity. Candidates are not
    consumed when ambiguous — they remain available to later rules and to
    the unmatched report, since none of them was claimed.

    The right side is filed by currency, amount and date first (E7), so a
    left record is compared only with records that could pair with it, and
    the comparing is bounded: past CANDIDATES_LISTED candidates a record is
    ambiguous whatever the rest are, and past MATCH_WORK the rule stops and
    reports the records it did not reach as undecided, never as unmatched.
    """
    from .calendar import as_date

    work = work or _Work()
    span = rule.window_delta.total_seconds() or 1.0
    lag = rule.lag
    tagged = rule.agree[0] if rule.agree else None
    filed = _Filed(tagging=tagged is not None)
    right_when: list[datetime | None] = [None] * len(right)
    for position, r in enumerate(right):
        minor = _minor(dig(r, rule.right_amount))
        if minor is None:
            continue
        currency = str(dig(r, rule.right_currency) or "").upper()
        tag = _ref_key(dig(r, tagged.right_path), tagged.case_sensitive) if tagged else None
        if lag is not None:
            day = as_date(dig(r, rule.timestamp_right), lag.timezone)
            if day is not None:  # an undated record can never be inside a lag
                filed.add(currency, minor, tag, float(day.toordinal()), position)
        else:
            when = _as_dt(dig(r, rule.timestamp_right))
            right_when[position] = when
            filed.add(currency, minor, tag, None if when is None else when.timestamp(), position)
    filed.close()

    still_left: list[Row] = []
    consumed: set[int] = set()
    lag_days: dict[date, tuple[int, int] | None] = {}
    #: Where the records the rule did not reach would have looked.
    unreached: set[tuple] = set()

    for l in left:
        l_cur = str(dig(l, rule.currency) or "").upper()
        l_minor = _minor(dig(l, rule.amount))
        if l_minor is None:
            still_left.append(l)
            continue
        amounts = filed.amounts_near(l_cur, l_minor, rule.amount_tolerance_minor)
        if not amounts:
            still_left.append(l)
            continue
        l_tag = _ref_key(dig(l, tagged.left), tagged.case_sensitive) if tagged else None
        keys = filed.keys(l_cur, amounts, l_tag)
        # The amounts within tolerance are the other side's to multiply too.
        if work.spent_out or not work.spend(len(keys)):
            unreached.update(keys)
            out.ambiguous.append(Ambiguity(left=l, candidates=[], rule=rule.kind, reason=STOPPED, more=True))
            continue

        l_ts = None
        if lag is not None:
            l_day = as_date(dig(l, rule.timestamp_left), lag.timezone)
            if l_day is None:
                still_left.append(l)
                continue
            if l_day not in lag_days:
                lag_days[l_day] = _lag_days(lag, l_day)
            admitted = lag_days[l_day]
            if admitted is None:
                still_left.append(l)
                continue
            first, last, undated = float(admitted[0]), float(admitted[1]), False
        else:
            l_ts = _as_dt(dig(l, rule.timestamp_left))
            # A second wider than the window: the exact test is below.
            first, last = (None, None) if l_ts is None else (l_ts.timestamp() - span - 1, l_ts.timestamp() + span + 1)
            undated = True

        candidates: list[tuple[int, Row, float, dict]] = []
        stopped = False
        for key in keys:
            for position in filed.within(key, first, last, undated):
                if not work.spend(PAIR):
                    stopped = True
                    break
                r = right[position]
                if id(r) in consumed:
                    continue
                if _identity_disagrees(identity, l, r):
                    continue
                agreed = _agreement(rule.agree, l, r)
                if agreed is None:
                    continue  # same money, same day, a different card
                corroborated = {"agreed": agreed} if agreed else {}
                if lag is not None:
                    # Settlement runs one way, in business days (E2).
                    days = lag.days(dig(l, rule.timestamp_left), dig(r, rule.timestamp_right))
                    if days is None or not lag.min_days <= days <= lag.max_days:
                        continue
                    gap = days / (lag.max_days + 1) * span
                    candidates.append((position, r, gap, {"lag": _lag_evidence(lag, days), **corroborated}))
                else:
                    r_ts = right_when[position]
                    if l_ts and r_ts:
                        gap = abs((l_ts - r_ts).total_seconds())
                        if gap > span:
                            continue
                    else:
                        gap = span
                    candidates.append((position, r, gap, {
                        "window": rule.window, "gap_seconds": int(gap) if l_ts and r_ts else None, **corroborated}))
                if len(candidates) > CANDIDATES_LISTED:
                    break
            if stopped or len(candidates) > CANDIDATES_LISTED:
                break

        candidates.sort(key=lambda c: c[0])  # the order the right side came in
        if len(candidates) > CANDIDATES_LISTED:
            out.ambiguous.append(Ambiguity(
                left=l, candidates=[r for _, r, _, _ in candidates[:CANDIDATES_LISTED]], rule=rule.kind, more=True,
                reason=f"more than {CANDIDATES_LISTED} candidates; none chosen"))
            continue
        if stopped:
            # Not every record was looked at, so one candidate is not known
            # to be the only one, and none is not known to be none.
            unreached.update(keys)
            out.ambiguous.append(Ambiguity(
                left=l, candidates=[r for _, r, _, _ in candidates], rule=rule.kind, reason=STOPPED, more=True))
            continue
        if not candidates:
            still_left.append(l)
            continue

        if len(candidates) > 1:
            # Equal amount, equal currency, both in window. Time proximity
            # is not evidence enough to choose — settlement lag is the least
            # reliable signal a rail has.
            out.ambiguous.append(
                Ambiguity(left=l, candidates=[r for _, r, _, _ in candidates], rule=rule.kind)
            )
            continue

        _, best, gap, timing = candidates[0]
        consumed.add(id(best))
        confidence = round(max(0.5, 1.0 - gap / span * 0.5), 4)
        out.matched.append(MatchedPair(l, best, rule.kind, confidence, evidence={
            "rule": rule.kind, **timing,
            "amount_tolerance_minor": rule.amount_tolerance_minor,
        }))

    # A right record that a left record the rule did not reach might have
    # paired with is not known to have no counterpart: undecided, like it.
    waiting: set[int] = set()
    for key in unreached:
        for position in filed.all(key):
            r = right[position]
            if id(r) not in consumed and id(r) not in waiting:
                waiting.add(id(r))
                out.waiting["right"].append(Unverifiable(r, STOPPED_OTHER))
    return still_left, [r for r in right if id(r) not in consumed and id(r) not in waiting]


def _apply_group_ref(
    rule: GroupRef, left: list[Row], right: list[Row], out: ReconResult
) -> tuple[list[Row], list[Row]]:
    """Each one-side record with every many-side record sharing its
    reference (E1). One record under the reference is an ordinary pair.

    A reference's records are claimed all together or not at all, so each
    is looked at once however many records ask for it, and an ambiguous
    one lists CANDIDATES_LISTED of them with the true count in its reason
    (E7): ten thousand records under one reference, asked for ten thousand
    times, was a hundred million references held in memory."""
    one_side = "left" if rule.many == "right" else "right"
    ones, many = (left, right) if one_side == "left" else (right, left)
    one_path, many_path = (rule.left, rule.right) if one_side == "left" else (rule.right, rule.left)

    index: dict[str, list[Row]] = {}
    for m in many:
        key = _ref_key(dig(m, many_path), rule.case_sensitive)
        if key is not None:
            index.setdefault(key, []).append(m)

    still: list[Row] = []
    consumed: set[int] = set()
    for o in ones:
        key = _ref_key(dig(o, one_path), rule.case_sensitive)
        bucket = index.get(key) if key is not None else None
        if not bucket:
            still.append(o)
            continue
        if len(bucket) > rule.max_members:
            out.ambiguous.append(Ambiguity(
                left=o, candidates=bucket[:CANDIDATES_LISTED], rule=rule.kind, side=one_side,
                reason=f"{len(bucket)} records carry this reference, more than {rule.max_members}",
                more=len(bucket) > CANDIDATES_LISTED,
            ))
            continue
        del index[key]  # claimed whole: nothing is left under it
        consumed.update(id(m) for m in bucket)
        pair = _pair(o, bucket, one_side, rule.kind, 1.0)
        pair.evidence = {"rule": rule.kind,
                         "key": {"left": rule.left, "right": rule.right, "value": str(dig(o, one_path))}}
        out.matched.append(pair)
    rest = [m for m in many if id(m) not in consumed]
    return (still, rest) if one_side == "left" else (rest, still)


def _apply_period_group(
    rule: PeriodGroup, left: list[Row], right: list[Row], out: ReconResult, work: _Work | None = None,
) -> tuple[list[Row], list[Row]]:
    """Each period-stating record with every record inside its period that
    shares its keys (E6). Periods are taken earliest first; a record two
    periods would both cover belongs to the first, so an overlapping second
    period reads empty rather than counting a sale twice. An empty period is
    still a pair: its sum is zero.

    The many side is filed by its keys and day first (E7), so a period
    looks only at the records inside it, and that looking is bounded by
    MATCH_WORK like the other fallback rules."""
    from .calendar import as_date

    work = work or _Work()

    one_side = rule.one
    ones, many = (left, right) if one_side == "left" else (right, left)

    def key(record: Row, paths: tuple[str, ...]) -> tuple[str | None, ...]:
        return tuple(_ref_key(dig(record, p), False) for p in paths)

    one_paths = tuple(o for o, _ in rule.share)
    many_paths = tuple(m for _, m in rule.share)
    days = [(m, as_date(dig(m, rule.timestamp_many), rule.timezone), key(m, many_paths)) for m in many]
    by_key: dict[tuple[str | None, ...], list[tuple[int, int]]] = {}
    for position, (_, day, k) in enumerate(days):
        if day is not None:
            by_key.setdefault(k, []).append((day.toordinal(), position))
    for entries in by_key.values():
        entries.sort()
    consumed: set[int] = set()
    still: list[Row] = []
    periods: dict[tuple[str | None, ...], list[tuple[date, date]]] = {}
    #: Keys of the periods the rule did not look into.
    unreached: set[tuple[str | None, ...]] = set()

    def start_of(record: Row):
        return as_date(dig(record, rule.start), rule.timezone)

    def earliest(record: Row) -> tuple[bool, str]:
        day = start_of(record)
        return day is None, day.isoformat() if day else ""

    for o in sorted(ones, key=earliest):
        first, last = start_of(o), as_date(dig(o, rule.end), rule.timezone)
        wanted = key(o, one_paths)
        if first is None or last is None or first > last or any(k is None for k in wanted):
            still.append(o)
            continue
        entries = by_key.get(wanted, [])
        lo = bisect_left(entries, (first.toordinal(), -1))
        hi = bisect_right(entries, (last.toordinal(), len(days)))
        if work.spent_out or not work.spend(hi - lo):
            # The period was not looked into, so it is not paired with an
            # empty one: nothing was established about it, or its records.
            unreached.add(wanted)
            out.ambiguous.append(Ambiguity(
                left=o, candidates=[], rule=rule.kind, side=one_side, reason=STOPPED, more=True))
            continue
        members = [days[position][0] for position in sorted(position for _, position in entries[lo:hi])
                   if id(days[position][0]) not in consumed]
        periods.setdefault(wanted, []).append((first, last))
        if len(members) > rule.max_members:
            out.ambiguous.append(Ambiguity(
                left=o, candidates=members, rule=rule.kind, side=one_side,
                reason=f"{len(members)} records fall in this period, more than {rule.max_members}"))
            continue
        consumed.update(id(m) for m in members)
        pair = _pair(o, members, one_side, rule.kind, 1.0)
        pair.evidence = {"rule": rule.kind, "period": {"start": first.isoformat(), "end": last.isoformat()},
                         "share": {o_path: str(dig(o, o_path)) for o_path in one_paths},
                         "members": len(members)}
        out.matched.append(pair)

    # A record outside every imported period of its key: before the
    # earliest, it predates the returns imported; after the latest, its
    # return cannot be filed yet. Either way it waits. One in a gap between
    # two periods, or of a key with no period at all, is a real miss.
    waiting = out.waiting["right" if one_side == "left" else "left"]
    rest: list[Row] = []
    for m, day, k in days:
        if id(m) in consumed:
            continue
        if k in unreached and day is not None:
            waiting.append(Unverifiable(m, STOPPED_OTHER))
            continue
        spans = periods.get(k)
        named = ", ".join(str(x) for x in k) or "this record"
        if day is None or not spans:
            rest.append(m)
        elif day > max(e for _, e in spans):
            latest = max(e for _, e in spans).isoformat()
            waiting.append(Unverifiable(m, f"after the latest period imported for {named} (to {latest}): "
                                           "waiting for the next"))
        elif day < min(s for s, _ in spans):
            earliest = min(s for s, _ in spans).isoformat()
            waiting.append(Unverifiable(m, f"before the earliest period imported for {named} (from {earliest})"))
        else:
            rest.append(m)
    return (still, rest) if one_side == "left" else (rest, still)


def _pair(one: Row, members: list[Row], one_side: str, rule: str, confidence: float) -> MatchedPair:
    """A pair, or a group pair when several records answer the one."""
    if len(members) == 1:
        l, r = (one, members[0]) if one_side == "left" else (members[0], one)
        return MatchedPair(l, r, rule, confidence)
    group = {"members": members, "member_count": len(members)}
    l, r = (one, group) if one_side == "left" else (group, one)
    return MatchedPair(l, r, rule, confidence, members=members,
                       grouped="right" if one_side == "left" else "left")


class _OutOfWork(Exception):
    """A search used up its rule's allowance (MATCH_WORK)."""


def _apply_group_sum(
    rule: GroupSum, left: list[Row], right: list[Row], out: ReconResult, work: _Work | None = None,
) -> tuple[list[Row], list[Row]]:
    """Each one-side record against the unclaimed many-side records that
    sum to it (E1). Records are taken in order, so the earliest deposit
    claims its batches first; two fitting subsets claim nothing.

    The many side is filed by currency and date first (E7), so a record is
    offered only the records inside its window, and both the offering and
    the search for a sum are bounded by MATCH_WORK: past it the rule stops
    and reports the records it did not reach as undecided."""
    from .calendar import as_date

    work = work or _Work()
    one_side = "right" if rule.many == "left" else "left"
    ones, many = (right, left) if rule.many == "left" else (left, right)
    span = rule.window_delta.total_seconds()
    lag = rule.lag
    still: list[Row] = []
    consumed: set[int] = set()

    # Each many-side record read once: its currency, whether it has an
    # amount, when it happened, and what its group must share.
    timed: dict[str, list[tuple[float, int]]] = {}
    untimed: dict[str, list[int]] = {}
    shares: list[tuple] = []
    many_when: list[datetime | None] = [None] * len(many)
    many_currency: list[str | None] = [None] * len(many)
    #: Currencies of the records the rule did not reach.
    unreached: set[str] = set()
    for position, m in enumerate(many):
        shares.append(tuple(str(dig(m, f)) for f in rule.share))
        if _minor(dig(m, rule.amount)) is None:
            continue
        currency = str(dig(m, rule.currency) or "").upper()
        many_currency[position] = currency
        stamp = dig(m, rule.timestamp_many)
        if lag is not None:
            day = as_date(stamp, lag.timezone)
            if day is not None:  # an undated record is inside no lag
                timed.setdefault(currency, []).append((float(day.toordinal()), position))
        else:
            when = _as_dt(stamp)
            many_when[position] = when
            if when is None:
                untimed.setdefault(currency, []).append(position)
            else:
                timed.setdefault(currency, []).append((when.timestamp(), position))
    for entries in timed.values():
        entries.sort()
    lag_days: dict[date, tuple[int, int] | None] = {}

    def offered(o: Row, currency: str) -> list[int] | None:
        """Positions of the many-side records inside `o`'s window, in the
        order they came; None when none can be."""
        entries = timed.get(currency, [])
        if lag is not None:
            # The days a lag admits are one unbroken run (`_lag_days`), so
            # the records dated inside it are exactly the ones in time.
            day = as_date(dig(o, rule.timestamp_one), lag.timezone)
            if day is None:
                return None
            if day not in lag_days:
                lag_days[day] = _lag_days(lag, day, of=one_side)
            admitted = lag_days[day]
            if admitted is None:
                return None
            lo = bisect_left(entries, (float(admitted[0]), -1))
            hi = bisect_right(entries, (float(admitted[1]), float("inf")))
            return sorted(position for _, position in entries[lo:hi])
        when = _as_dt(dig(o, rule.timestamp_one))
        if when is None:
            found = [position for _, position in entries]
        else:
            # A second wider than the window, then the exact test.
            lo = bisect_left(entries, (when.timestamp() - span - 1, -1))
            hi = bisect_right(entries, (when.timestamp() + span + 1, float("inf")))
            found = [position for _, position in entries[lo:hi]
                     if abs((many_when[position] - when).total_seconds()) <= span]  # type: ignore[operator]
        return sorted(found + untimed.get(currency, []))

    def undecided(o: Row, currency: str) -> None:
        unreached.add(currency)
        out.ambiguous.append(Ambiguity(
            left=o, candidates=[], rule=rule.kind, side=one_side, reason=STOPPED, more=True))

    for o in ones:
        o_minor = _minor(dig(o, rule.one_amount))
        o_cur = str(dig(o, rule.one_currency) or "").upper()
        if o_minor is None:
            still.append(o)
            continue
        if o_cur not in timed and o_cur not in untimed:
            still.append(o)
            continue
        if work.spent_out:
            undecided(o, o_cur)
            continue
        positions = offered(o, o_cur)
        if not positions:
            still.append(o)
            continue
        if not work.spend(len(positions)):
            undecided(o, o_cur)
            continue
        groups: dict[tuple, list[Row]] = {}
        for i in positions:
            m = many[i]
            if id(m) not in consumed:
                groups.setdefault(shares[i], []).append(m)

        fits: list[list[Row]] = []
        crowded = False
        try:
            for members in groups.values():
                if len(members) > rule.max_candidates:
                    crowded = True
                    continue
                fits += _subsets_summing(members, rule.amount, o_minor, rule, work)
                if len(fits) > 1:
                    break
        except _OutOfWork:
            undecided(o, o_cur)
            continue
        if len(fits) == 1 and not crowded:
            consumed.update(id(m) for m in fits[0])
            # A sum found among unreferenced records is weaker evidence
            # than a reference: stated, not hidden.
            pair = _pair(o, fits[0], one_side, rule.kind, 0.75)
            pair.evidence = {
                "rule": rule.kind,
                **({"lag": {"min_days": lag.min_days, "max_days": lag.max_days,
                            "calendar": lag.calendar, "timezone": lag.timezone}}
                   if lag else {"window": rule.window}),
                "share": {f: dig(fits[0][0], f) for f in rule.share},
                "amount_tolerance_minor": rule.amount_tolerance_minor,
            }
            out.matched.append(pair)
        elif fits or crowded:
            seen: dict[int, Row] = {}
            for fit in fits:
                for m in fit:
                    seen.setdefault(id(m), m)
            out.ambiguous.append(Ambiguity(
                left=o, candidates=list(seen.values()), rule=rule.kind, side=one_side,
                reason=(f"more than {rule.max_candidates} candidates in the window; none chosen"
                        if crowded and not fits else f"{len(fits)} groupings sum to this amount"),
                alternatives=fits or None,
            ))
        else:
            still.append(o)
    # A many-side record in the currency of a record the rule did not reach
    # might have been part of its sum: undecided, not "never paid".
    rest: list[Row] = []
    waiting = out.waiting[rule.many]
    for position, m in enumerate(many):
        if id(m) in consumed:
            continue
        if many_currency[position] in unreached:
            waiting.append(Unverifiable(m, STOPPED_OTHER))
        else:
            rest.append(m)
    return (rest, still) if rule.many == "left" else (still, rest)


def _subsets_summing(
    members: list[Row], path: str, target: int, rule: GroupSum, work: _Work | None = None,
) -> list[list[Row]]:
    """Up to two subsets (at most `max_members` records) whose amounts sum
    to `target` within tolerance. Two is enough to know it is ambiguous.
    Each step of the search is counted against `work` (E7): records whose
    amounts prune nothing (zeros, or a sum no subset reaches) would
    otherwise be searched through every combination."""
    work = work or _Work()
    amounts = [int(_minor(dig(m, path))) for m in members]  # type: ignore[arg-type]
    order = sorted(range(len(members)), key=lambda i: amounts[i])
    positive = all(a >= 0 for a in amounts)
    found: list[list[Row]] = []

    def walk(start: int, chosen: list[int], total: int) -> None:
        if not work.spend():
            raise _OutOfWork
        if len(found) > 1:
            return
        if chosen and abs(total - target) <= rule.amount_tolerance_minor:
            found.append([members[i] for i in sorted(chosen)])
        if len(chosen) == rule.max_members:
            return
        for k in range(start, len(order)):
            i = order[k]
            # Sorted ascending and all non-negative: once a record overshoots,
            # every later one does too.
            if positive and total + amounts[i] > target + rule.amount_tolerance_minor:
                break
            walk(k + 1, chosen + [i], total + amounts[i])
            if len(found) > 1:
                return

    walk(0, [], 0)
    return found


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
        l_amt, r_amt = pair.value("left", ref.left), pair.value("right", ref.right_path)
        if l_amt is None or r_amt is None:
            continue
        l_cur = str(pair.value("left", ref.currency) or "").upper()
        r_cur = str(pair.value("right", ref.right_currency) or "").upper()
        if l_cur and r_cur and l_cur != r_cur:
            continue
        try:
            pair.discrepancy_minor = int(l_amt) - int(r_amt)
        except (TypeError, ValueError):
            continue

        pair.evidence["compared"] = {
            "left_path": ref.left, "right_path": ref.right_path,
            "left_minor": int(l_amt), "right_minor": int(r_amt),
            "currency": l_cur or r_cur or None,
        }
        parts = _explanation(spec, pair)
        if parts:
            pair.explained_by = "+".join(p["label"] for p in parts)
            pair.evidence["explanation"] = parts


#: Most causes combined to explain one difference. Beyond this a match is
#: more likely coincidence than accounting.
MAX_EXPLANATION_PARTS = 4


def _explanation(spec: Reconciliation, pair: MatchedPair) -> list[dict[str, Any]] | None:
    """The causes that account for a pair's difference, exactly (E3).

    One declared cause first, in declaration order, as before. Failing
    that, the smallest combination of causes whose amounts add up to the
    difference: a fee and a refund on the same order. Each part must be
    non-zero and point the way its `sign` says; magnitudes are summed,
    because rails state adjustments with their own sign conventions.
    """
    gap = pair.discrepancy_minor
    if not gap:
        return None
    stated = []
    for rule in spec.explains:
        amount = rule.figure(lambda path, side=rule.side: pair.value(side, path))
        if amount is not None and rule.allows(amount, gap):
            stated.append((rule, amount))
    for size in range(1, min(len(stated), MAX_EXPLANATION_PARTS) + 1):
        for combo in combinations(stated, size):
            if sum(abs(a) for _, a in combo) == abs(gap):
                return [{"label": r.label, "side": r.side, "paths": list(r.fields), "amount_minor": a}
                        for r, a in combo]
    return None


def _lag_evidence(lag, days: int) -> dict[str, Any]:
    return {"business_days": days, "min_days": lag.min_days, "max_days": lag.max_days,
            "calendar": lag.calendar, "timezone": lag.timezone}


def match(
    spec: Reconciliation,
    left_rows: list[Row],
    right_rows: list[Row],
    left_coverage: Coverage | None = None,
    right_coverage: Coverage | None = None,
) -> ReconResult:
    result = ReconResult(
        name=spec.name, left_coverage=left_coverage, right_coverage=right_coverage
    )
    left, right = list(left_rows), list(right_rows)

    for rule in spec.keys:
        if not left or not right:
            break
        if isinstance(rule, ExactRef):
            left, right = _apply_exact(rule, left, right, result)
        elif isinstance(rule, Fuzzy):
            left, right = _apply_fuzzy(rule, spec.identity, left, right, result)
        elif isinstance(rule, GroupRef):
            left, right = _apply_group_ref(rule, left, right, result)
        elif isinstance(rule, GroupSum):
            left, right = _apply_group_sum(rule, left, right, result)
        elif isinstance(rule, PeriodGroup):
            left, right = _apply_period_group(rule, left, right, result)

    left_ts, right_ts = spec.timestamp_paths
    result.unmatched_left, result.unverifiable_left = _split_unread(
        left, left_ts, right_coverage
    )
    result.unmatched_right, result.unverifiable_right = _split_unread(
        right, right_ts, left_coverage
    )
    result.unverifiable_left += result.waiting["left"]
    result.unverifiable_right += result.waiting["right"]
    _score_discrepancies(spec, result)
    _describe_groups(spec, result)
    return result


def _describe_groups(spec: Reconciliation, result: ReconResult) -> None:
    """Give each group's summary the figures a person checks it by: the
    summed amount and the shared currency at the declaration's own paths,
    and the members' references joined, so a stored row reads
    "B-1001+B-1002" rather than nothing."""
    from .paths import put

    amount = spec.resolved_amount
    refs = ref_paths(spec)
    for pair in result.matched:
        if pair.members is None:
            continue
        side = pair.grouped
        summary = pair.left if side == "left" else pair.right
        paths = []
        if amount is not None:
            paths += [amount.left, amount.currency] if side == "left" else [amount.right_path, amount.right_currency]
        paths += [f for e in spec.explains if e.side == side for f in e.fields]
        for path in paths:
            put(summary, path, pair.value(side, path))
        ref = refs[0] if side == "left" else refs[1]
        amount_path = None if amount is None else (amount.left if side == "left" else amount.right_path)
        # A reference and an amount per member, the figures the privacy
        # policy says a stored result holds; never a member's record.
        pair.evidence["members"] = [
            {"ref": None if not ref else dig(m, ref),
             "amount_minor": None if not amount_path else dig(m, amount_path)}
            for m in pair.members
        ]
        if ref:
            # Distinct, in order: a GroupRef's members all carry the one
            # reference, a GroupSum's each carry their own.
            parts = list(dict.fromkeys(str(dig(m, ref)) for m in pair.members if dig(m, ref) not in (None, "")))
            if parts:
                put(summary, ref, "+".join(parts))


def _split_unread(
    rows: list[Row], timestamp: str | None, counterpart: Coverage | None
) -> tuple[list[Row], list[Unverifiable]]:
    """Separate real misses from records the other side never read for.

    Without coverage nothing is known about what was read, so every
    record stays unmatched — the behaviour before coverage existed.
    """
    if counterpart is None:
        return rows, []
    missed: list[Row] = []
    unread: list[Unverifiable] = []
    for r in rows:
        read, reason = counterpart.reads(_as_dt(dig(r, timestamp)) if timestamp else None)
        if read:
            missed.append(r)
        else:
            unread.append(Unverifiable(r, reason or "counterpart side not read"))
    return missed, unread


#: Worst first, how a stored two-sided run lists its rows: the breaks and
#: misses, then what could not be read, then the clean matches.
PAIR_DISPLAY_ORDER = (
    "matched_discrepant",
    "unmatched_left",
    "unmatched_right",
    "ambiguous",
    "matched_explained",
    "unverifiable_left",
    "unverifiable_right",
    "matched",
)


def ref_paths(spec: Reconciliation) -> tuple[str | None, str | None]:
    """The one human reference each side carries: the first exact key."""
    for rule in spec.keys:
        if isinstance(rule, (ExactRef, GroupRef)):
            return rule.left, rule.right
    for rule in spec.keys:
        if isinstance(rule, GroupSum) and (rule.ref_many or rule.ref_one):
            return (rule.ref_many, rule.ref_one) if rule.many == "left" else (rule.ref_one, rule.ref_many)
    return None, None


def _ref(record: Row | None, path: str | None) -> str | None:
    if record is None or not path:
        return None
    value = dig(record, path)
    return None if value in (None, "") else str(value)
