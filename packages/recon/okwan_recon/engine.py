"""Match engine — pure over rows. No transport, no connector imports.

Rules are applied in declaration order and consume their matches, so
an exact reference join runs before the fuzzy fallback ever sees the
records it already paired.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .coverage import Coverage
from .declaration import MSISDN, ExactRef, Fuzzy, GroupRef, GroupSum, Reconciliation
from .paths import dig

Row = dict[str, Any]

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

    def value(self, side: str, path: str) -> Any:
        """A field of one side. On a group's side: the sum when every
        member's value is a whole number (amounts, fees), the shared value
        when they all agree (a currency), otherwise None."""
        record = self.left if side == "left" else self.right
        if self.members is None or self.grouped != side:
            return dig(record, path)
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
            }
            for p in self.matched
        ]
        out += [
            {"status": "unmatched_left", "rule": None, "confidence": 0.0,
             "discrepancy": None, "reason": None, "left": r, "right": None}
            for r in self.unmatched_left
        ]
        out += [
            {"status": "unmatched_right", "rule": None, "confidence": 0.0,
             "discrepancy": None, "reason": None, "left": None, "right": r}
            for r in self.unmatched_right
        ]
        out += [
            {"status": "ambiguous", "rule": a.rule, "confidence": 0.0,
             "discrepancy": None, "reason": a.reason,
             "left": a.left if a.side == "left" else None,
             "right": a.left if a.side == "right" else None,
             "candidates": a.candidates}
            for a in self.ambiguous
        ]
        out += [
            {"status": "unverifiable_left", "rule": None, "confidence": 0.0,
             "discrepancy": None, "reason": u.reason, "left": u.record, "right": None}
            for u in self.unverifiable_left
        ]
        out += [
            {"status": "unverifiable_right", "rule": None, "confidence": 0.0,
             "discrepancy": None, "reason": u.reason, "left": None, "right": u.record}
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
        l_minor = _minor(dig(l, rule.amount))
        if l_minor is None:
            still_left.append(l)
            continue
        l_ts = _as_dt(dig(l, rule.timestamp_left))

        candidates: list[tuple[Row, float]] = []
        for r in right:
            if id(r) in consumed:
                continue
            if str(dig(r, rule.right_currency) or "").upper() != l_cur:
                continue
            r_minor = _minor(dig(r, rule.right_amount))
            if r_minor is None:
                continue
            if abs(r_minor - l_minor) > rule.amount_tolerance_minor:
                continue
            if _identity_disagrees(identity, l, r):
                continue
            if rule.lag is not None:
                # Settlement runs one way, in business days (E2).
                days = rule.lag.days(dig(l, rule.timestamp_left), dig(r, rule.timestamp_right))
                if days is None or not rule.lag.min_days <= days <= rule.lag.max_days:
                    continue
                gap = days / (rule.lag.max_days + 1) * span
                candidates.append((r, gap))
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


def _apply_group_ref(
    rule: GroupRef, left: list[Row], right: list[Row], out: ReconResult
) -> tuple[list[Row], list[Row]]:
    """Each one-side record with every many-side record sharing its
    reference (E1). One record under the reference is an ordinary pair."""
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
        bucket = [m for m in index.get(key, ()) if id(m) not in consumed] if key is not None else []
        if not bucket:
            still.append(o)
            continue
        if len(bucket) > rule.max_members:
            out.ambiguous.append(Ambiguity(
                left=o, candidates=bucket, rule=rule.kind, side=one_side,
                reason=f"{len(bucket)} records carry this reference, more than {rule.max_members}",
            ))
            continue
        consumed.update(id(m) for m in bucket)
        out.matched.append(_pair(o, bucket, one_side, rule.kind, 1.0))
    rest = [m for m in many if id(m) not in consumed]
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


def _apply_group_sum(
    rule: GroupSum, left: list[Row], right: list[Row], out: ReconResult
) -> tuple[list[Row], list[Row]]:
    """Each one-side record against the unclaimed many-side records that
    sum to it (E1). Records are taken in order, so the earliest deposit
    claims its batches first; two fitting subsets claim nothing."""
    one_side = "right" if rule.many == "left" else "left"
    ones, many = (right, left) if rule.many == "left" else (left, right)
    span = rule.window_delta.total_seconds()
    still: list[Row] = []
    consumed: set[int] = set()

    def in_time(m: Row, o: Row) -> bool:
        m_ts, o_ts = dig(m, rule.timestamp_many), dig(o, rule.timestamp_one)
        if rule.lag is not None:
            l_ts, r_ts = (m_ts, o_ts) if rule.many == "left" else (o_ts, m_ts)
            return rule.lag.admits(l_ts, r_ts)
        a, b = _as_dt(m_ts), _as_dt(o_ts)
        return a is None or b is None or abs((a - b).total_seconds()) <= span

    for o in ones:
        o_minor = _minor(dig(o, rule.one_amount))
        o_cur = str(dig(o, rule.one_currency) or "").upper()
        if o_minor is None:
            still.append(o)
            continue
        pool = [
            m for m in many
            if id(m) not in consumed
            and str(dig(m, rule.currency) or "").upper() == o_cur
            and _minor(dig(m, rule.amount)) is not None
            and in_time(m, o)
        ]
        groups: dict[tuple, list[Row]] = {}
        for m in pool:
            groups.setdefault(tuple(str(dig(m, f)) for f in rule.share), []).append(m)

        fits: list[list[Row]] = []
        crowded = False
        for members in groups.values():
            if len(members) > rule.max_candidates:
                crowded = True
                continue
            fits += _subsets_summing(members, rule.amount, o_minor, rule)
            if len(fits) > 1:
                break
        if len(fits) == 1 and not crowded:
            consumed.update(id(m) for m in fits[0])
            # A sum found among unreferenced records is weaker evidence
            # than a reference: stated, not hidden.
            out.matched.append(_pair(o, fits[0], one_side, rule.kind, 0.75))
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
    rest = [m for m in many if id(m) not in consumed]
    return (rest, still) if rule.many == "left" else (still, rest)


def _subsets_summing(members: list[Row], path: str, target: int, rule: GroupSum) -> list[list[Row]]:
    """Up to two subsets (at most `max_members` records) whose amounts sum
    to `target` within tolerance. Two is enough to know it is ambiguous."""
    amounts = [int(_minor(dig(m, path))) for m in members]  # type: ignore[arg-type]
    order = sorted(range(len(members)), key=lambda i: amounts[i])
    positive = all(a >= 0 for a in amounts)
    found: list[list[Row]] = []

    def walk(start: int, chosen: list[int], total: int) -> None:
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

        for rule in spec.explains:
            if rule.accounts_for(pair.value(rule.side, rule.path), pair.discrepancy_minor):
                pair.explained_by = rule.label
                break


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

    left_ts, right_ts = spec.timestamp_paths
    result.unmatched_left, result.unverifiable_left = _split_unread(
        left, left_ts, right_coverage
    )
    result.unmatched_right, result.unverifiable_right = _split_unread(
        right, right_ts, left_coverage
    )
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
        paths += [e.path for e in spec.explains if e.side == side]
        for path in paths:
            put(summary, path, pair.value(side, path))
        ref = refs[0] if side == "left" else refs[1]
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
    return None, None


def _ref(record: Row | None, path: str | None) -> str | None:
    if record is None or not path:
        return None
    value = dig(record, path)
    return None if value in (None, "") else str(value)
