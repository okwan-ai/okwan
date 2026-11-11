"""The matcher's work is bounded, and bounding it changed no answer (E7).

Found by the attacker's pass of 2026-11-11 (docs/audit/red-team.md): a
fallback rule compared every record of one side with every record of the
other, and the rows are the merchant's own. Two thousand payments sharing
one reference and amount took fourteen seconds of the API's one worker;
the same held for the group-sum rule, whose search for a sum also walked
every combination of records that prune nothing, and for a lag between
two dates a thousand years apart, counted a day at a time.

The rules now file the other side by currency, amount and date and look
only at what could pair, and what they still do is counted against
MATCH_WORK. What must hold:

- on ordinary rows the answer is the one the plain rules gave: the plain
  rules are kept below, as they were, and random rows for every
  reconciliation that uses them come out the same;
- on rows shaped to cost the most, the work stays inside the bound, and a
  record the rule did not get to is reported as undecided, never as
  unmatched: nothing was established about it;
- matching runs off the event loop, so the API answers meanwhile.
"""
from __future__ import annotations

import json
import random
import threading
import time
from datetime import date, timedelta

import okwan_api.main  # noqa: F401  (registers the connectors and declarations)
import pytest
from okwan_recon import across, calendar, engine, runner
from okwan_recon.calendar import as_date
from okwan_recon.coverage import Coverage
from okwan_recon.declaration import (
    MSISDN,
    ExactRef,
    Fuzzy,
    GroupRef,
    GroupSum,
    PeriodGroup,
    SettlementLag,
)
from okwan_recon.engine import (
    CANDIDATES_LISTED,
    MATCH_WORK,
    PAIR,
    STOPPED,
    STOPPED_OTHER,
    Ambiguity,
    MatchedPair,
    ReconResult,
    Row,
    Unverifiable,
    _agreement,
    _as_dt,
    _identity_disagrees,
    _lag_days,
    _lag_evidence,
    _minor,
    _pair,
    _ref_key,
    _subsets_summing,
    _Work,
    match,
)
from okwan_recon.paths import dig
from okwan_recon.registry import all_reconciliations, get, get_across

# ── the plain rules, as they were ───────────────────────────────────
# Kept as the definition the filed rules are held to. They compare
# everything with everything, which is why they are no longer the engine.

def plain_fuzzy(
    rule: Fuzzy,
    identity: MSISDN | None,
    left: list[Row],
    right: list[Row],
    out: ReconResult,
) -> tuple[list[Row], list[Row]]:
    """The fuzzy rule as it was: every left record against every right one."""
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
            agreed = _agreement(rule.agree, l, r)
            if agreed is None:
                continue  # same money, same day, a different card
            corroborated = {"agreed": agreed} if agreed else {}
            if rule.lag is not None:
                # Settlement runs one way, in business days (E2).
                days = rule.lag.days(dig(l, rule.timestamp_left), dig(r, rule.timestamp_right))
                if days is None or not rule.lag.min_days <= days <= rule.lag.max_days:
                    continue
                gap = days / (rule.lag.max_days + 1) * span
                candidates.append((r, gap, {"lag": _lag_evidence(rule.lag, days), **corroborated}))
                continue
            r_ts = _as_dt(dig(r, rule.timestamp_right))
            if l_ts and r_ts:
                gap = abs((l_ts - r_ts).total_seconds())
                if gap > span:
                    continue
            else:
                gap = span
            candidates.append((r, gap, {"window": rule.window, "gap_seconds": int(gap) if l_ts and r_ts else None,
                                        **corroborated}))

        if not candidates:
            still_left.append(l)
            continue

        if len(candidates) > 1:
            # Equal amount, equal currency, both in window. Time proximity
            # is not evidence enough to choose — settlement lag is the least
            # reliable signal a rail has.
            out.ambiguous.append(
                Ambiguity(left=l, candidates=[r for r, _, _ in candidates], rule=rule.kind)
            )
            continue

        best, gap, timing = candidates[0]
        consumed.add(id(best))
        confidence = round(max(0.5, 1.0 - gap / span * 0.5), 4)
        out.matched.append(MatchedPair(l, best, rule.kind, confidence, evidence={
            "rule": rule.kind, **timing,
            "amount_tolerance_minor": rule.amount_tolerance_minor,
        }))

    return still_left, [r for r in right if id(r) not in consumed]


def plain_group_sum(
    rule: GroupSum, left: list[Row], right: list[Row], out: ReconResult
) -> tuple[list[Row], list[Row]]:
    """The group-sum rule as it was: every one-side record offered every many-side one."""
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
            pair = _pair(o, fits[0], one_side, rule.kind, 0.75)
            pair.evidence = {
                "rule": rule.kind,
                **({"lag": {"min_days": rule.lag.min_days, "max_days": rule.lag.max_days,
                            "calendar": rule.lag.calendar, "timezone": rule.lag.timezone}}
                   if rule.lag else {"window": rule.window}),
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
    rest = [m for m in many if id(m) not in consumed]
    return (rest, still) if rule.many == "left" else (still, rest)


def plain_period_group(
    rule: PeriodGroup, left: list[Row], right: list[Row], out: ReconResult
) -> tuple[list[Row], list[Row]]:
    """The period rule as it was: every period against every record."""
    one_side = rule.one
    ones, many = (left, right) if one_side == "left" else (right, left)

    def key(record: Row, paths: tuple[str, ...]) -> tuple[str | None, ...]:
        return tuple(_ref_key(dig(record, p), False) for p in paths)

    one_paths = tuple(o for o, _ in rule.share)
    many_paths = tuple(m for _, m in rule.share)
    days = [(m, as_date(dig(m, rule.timestamp_many), rule.timezone), key(m, many_paths)) for m in many]
    consumed: set[int] = set()
    still: list[Row] = []
    periods: dict[tuple[str | None, ...], list[tuple[date, date]]] = {}

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
        members = [m for m, day, k in days
                   if id(m) not in consumed and day is not None and first <= day <= last and k == wanted]
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


def plain_group_ref(
    rule: GroupRef, left: list[Row], right: list[Row], out: ReconResult
) -> tuple[list[Row], list[Row]]:
    """The group-reference rule as it was: every asking record filtered its reference's records again."""
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
        pair = _pair(o, bucket, one_side, rule.kind, 1.0)
        pair.evidence = {"rule": rule.kind,
                         "key": {"left": rule.left, "right": rule.right, "value": str(dig(o, one_path))}}
        out.matched.append(pair)
    rest = [m for m in many if id(m) not in consumed]
    return (still, rest) if one_side == "left" else (rest, still)


# ── random rows ─────────────────────────────────────────────────────

AMOUNTS = [0, 100, 100, 250, 250, 250, 999, 1000, None]
CURRENCIES = ["USD", "USD", "USD", "usd", "EUR", None]
DAYS = [f"2026-10-{d:02d}" for d in range(1, 16)] + [
    "2026-11-27", "2026-12-25", None, "not a date", "0001-01-01", "9999-12-31"]
STAMPS = [f"2026-10-{d:02d}T{h:02d}:00:00+00:00" for d in range(1, 12) for h in (0, 9, 23)] + [None, 1759000000, "junk"]
BOUNDED = [s.name for s in all_reconciliations()
           if any(isinstance(k, Fuzzy | GroupSum | PeriodGroup | GroupRef) for k in s.keys)]


def put(row: Row, path: str, value) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        row = row.setdefault(part, {})
    row[parts[-1]] = value


def rows_for(spec, side: str, n: int, rng: random.Random) -> list[Row]:
    """Rows carrying every field the reconciliation's rules read, drawn
    from small sets so that amounts, days and references collide."""
    out = []
    for i in range(n):
        row: Row = {}
        for rule in spec.keys:
            if isinstance(rule, ExactRef):
                put(row, rule.left if side == "L" else rule.right, rng.choice([f"R{rng.randrange(n * 3)}", None, f"R{i}"]))
            elif isinstance(rule, Fuzzy):
                put(row, rule.amount if side == "L" else rule.right_amount, rng.choice(AMOUNTS))
                put(row, rule.currency if side == "L" else rule.right_currency, rng.choice(CURRENCIES))
                put(row, rule.timestamp_left if side == "L" else rule.timestamp_right,
                    rng.choice(DAYS if rule.lag else STAMPS + DAYS))
                for a in rule.agree:
                    put(row, a.left if side == "L" else a.right_path, rng.choice(["1111", "2222", None, "", " 1111 "]))
            elif isinstance(rule, GroupRef):
                put(row, rule.left if side == "L" else rule.right, rng.choice([f"G{rng.randrange(max(2, n // 3))}", None]))
            elif isinstance(rule, GroupSum):
                many = (side == "L") == (rule.many == "left")
                put(row, rule.amount if many else rule.one_amount, rng.choice([*AMOUNTS, 350, 500, 1100]))
                put(row, rule.currency if many else rule.one_currency, rng.choice(CURRENCIES))
                put(row, rule.timestamp_many if many else rule.timestamp_one, rng.choice(DAYS))
                if many:
                    for f in rule.share:
                        put(row, f, rng.choice(["M1", "M1", "M2", None]))
            elif isinstance(rule, PeriodGroup):
                one = (side == "L") == (rule.one == "left")
                if one:
                    a, b = sorted(rng.sample(range(1, 28), 2))
                    put(row, rule.start, rng.choice([f"2026-10-{a:02d}", None]))
                    put(row, rule.end, rng.choice([f"2026-10-{b:02d}", f"2026-10-{a:02d}"]))
                else:
                    put(row, rule.timestamp_many, rng.choice([f"2026-10-{rng.randrange(1, 29):02d}", None, "2026-11-03"]))
                for o_path, m_path in rule.share:
                    put(row, o_path if one else m_path, rng.choice(["CA", "CA", "NY", None]))
        out.append(row)
    return out


def told(result: ReconResult) -> str:
    """Everything a result says: its rows, in order, and its summary."""
    return json.dumps({"rows": result.rows(), "summary": result.summary}, sort_keys=True, default=str)


def plainly(monkeypatch, spec, left, right) -> ReconResult:
    """The same reconciliation with the plain rules in the engine's place."""
    with monkeypatch.context() as m:
        m.setattr(engine, "_apply_fuzzy", plain_fuzzy)
        m.setattr(engine, "_apply_group_ref", plain_group_ref)
        m.setattr(engine, "_apply_group_sum", plain_group_sum)
        m.setattr(engine, "_apply_period_group", plain_period_group)
        return match(spec, left, right)


# ── bounding it changed no answer ───────────────────────────────────

def test_every_reconciliation_with_a_fallback_rule_is_covered():
    assert set(BOUNDED) >= {"shopify_paypal", "shopify_stripe", "batches_deposits", "clover_payments_settlements",
                            "square_payouts_deposits", "delivery_payouts_deposits", "avatax_collected_filed",
                            "clover_orders_payments", "delivery_payout_lines"}
    unbounded = [s.name for s in all_reconciliations() if s.name not in BOUNDED]
    # What is left pairs by an exact reference alone: one lookup a record.
    assert all(isinstance(k, ExactRef) for name in unbounded for k in get(name).keys), unbounded


@pytest.mark.parametrize("name", BOUNDED)
def test_random_rows_come_out_as_the_plain_rules_gave_them(name, monkeypatch):
    """With the bounds lifted the filed rules and the plain ones must agree
    on every row of every result: what is matched to what, on which
    evidence, what is ambiguous among which candidates, what is left."""
    monkeypatch.setattr(engine, "CANDIDATES_LISTED", 10**9)
    monkeypatch.setattr(engine, "MATCH_WORK", 10**12)
    spec, rng = get(name), random.Random(f"okwan {name}")
    seen: set[tuple] = set()
    for _ in range(40):
        left = rows_for(spec, "L", rng.choice([0, 1, 4, 12, 40, 110]), rng)
        right = rows_for(spec, "R", rng.choice([0, 1, 4, 12, 40, 110]), rng)
        filed = match(spec, left, right)
        assert told(filed) == told(plainly(monkeypatch, spec, left, right))
        seen |= {(row["status"], row.get("rule")) for row in filed.rows()}
    statuses = {status for status, _ in seen}
    assert {"matched", "unmatched_left", "unmatched_right"} <= statuses, "the rows exercised the rules"
    assert any(rule in {"fuzzy", "group_sum", "period_group", "group_ref"} for _, rule in seen)


@pytest.mark.parametrize("name", BOUNDED)
def test_with_the_bounds_on_ordinary_rows_still_come_out_the_same(name, monkeypatch):
    """The bounds as shipped. Rows spread the way a business's are (many
    amounts, many days) never reach them, so nothing differs at all."""
    spec, rng = get(name), random.Random(f"ordinary {name}")
    for _ in range(6):
        left, right = rows_for(spec, "L", 60, rng), rows_for(spec, "R", 60, rng)
        for i, row in enumerate(left + right):          # spread the amounts: few records share one
            for rule in spec.keys:
                if isinstance(rule, Fuzzy):
                    for path in (rule.amount, rule.right_amount):
                        if dig(row, path) is not None:
                            put(row, path, 100 + (i % 23) * 7)
        filed = match(spec, left, right)
        assert not any(a.more for a in filed.ambiguous)
        assert told(filed) == told(plainly(monkeypatch, spec, left, right))


@pytest.mark.parametrize("direction", ["right_after_left", "left_after_right"])
@pytest.mark.parametrize("of", ["left", "right"])
@pytest.mark.parametrize(("least", "most"), [(0, 0), (0, 3), (1, 2), (2, 2), (0, 30), (5, 9)])
def test_the_days_a_lag_admits_are_exactly_the_days_it_admits(direction, of, least, most):
    """The filed rules look only at the other side's days a lag admits.
    Those days, worked out once for a date, are the ones `SettlementLag`
    itself says yes to, across weekends, holidays and a year's end."""
    lag = SettlementLag(min_days=least, max_days=most, direction=direction)
    for start in (date(2026, 10, 5), date(2026, 11, 25), date(2026, 12, 24), date(2026, 12, 31), date(2027, 1, 16),
                  date(2026, 7, 3), date(2026, 5, 23)):
        found = _lag_days(lag, start, of=of)
        admitted = []
        for offset in range(-80, 81):
            other = start + timedelta(days=offset)
            left, right = (start, other) if of == "left" else (other, start)
            if lag.admits(left.isoformat(), right.isoformat()):
                admitted.append(other.toordinal())
        assert admitted, "every lag admits some day"
        assert found == (admitted[0], admitted[-1])
        assert admitted == list(range(admitted[0], admitted[-1] + 1)), "one unbroken run"


def test_a_lag_at_the_end_of_the_calendar_ends_there():
    lag = SettlementLag(max_days=3)
    assert _lag_days(lag, date.max)[1] == date.max.toordinal()
    assert _lag_days(SettlementLag(max_days=3, direction="left_after_right"), date.min)[0] == date.min.toordinal()


# ── rows shaped to cost the most ────────────────────────────────────

def _fuzzy_of(name: str) -> Fuzzy:
    return next(k for k in get(name).keys if isinstance(k, Fuzzy))


def _sharing_one_amount(name: str, n: int, *, left_at: str, right_at: str) -> tuple[list[Row], list[Row]]:
    """The exploit's rows: every record of both sides the same amount and
    time, and no reference in common."""
    rule = _fuzzy_of(name)
    left, right = [], []
    for i in range(n):
        l: Row = {}
        put(l, rule.amount, 1000), put(l, rule.currency, "USD"), put(l, rule.timestamp_left, left_at)
        r: Row = {}
        put(r, rule.right_amount, 1000), put(r, rule.right_currency, "USD"), put(r, rule.timestamp_right, right_at)
        left.append({**l, "name": f"#{i}"}), right.append({**r, "invoice_id": "#x", "order_ref": "#x"})
    return left, right


def test_records_sharing_one_amount_cost_their_number_not_its_square():
    """The attacker's exploit, turned around. It timed 500 and 2,000
    colliding rows and showed the cost growing sixfold and more for four
    times the rows. Counted here instead of timed: the pairs weighed are
    at most CANDIDATES_LISTED + 1 a record, and one more for where it looks."""
    rule = _fuzzy_of("shopify_paypal")
    spent = {}
    for n in (500, 2000):
        left, right = _sharing_one_amount("shopify_paypal", n, left_at="2026-10-05T10:00:00+00:00",
                                          right_at="2026-10-05T11:00:00+00:00")
        work, out = _Work(), ReconResult(name="x")
        still, rest = engine._apply_fuzzy(rule, None, left, right, out, work)
        spent[n] = MATCH_WORK - work.left
        assert spent[n] <= n * (PAIR * (CANDIDATES_LISTED + 1) + 1)
        assert (len(out.ambiguous), still, len(rest)) == (n, [], n), "every one ambiguous, none claimed, none lost"
        assert all(a.more and len(a.candidates) == CANDIDATES_LISTED for a in out.ambiguous)
        assert out.ambiguous[0].reason == f"more than {CANDIDATES_LISTED} candidates; none chosen"
    assert spent[2000] == 4 * spent[500], "four times the rows, four times the work"


def test_the_whole_check_on_five_thousand_colliding_rows_is_seconds_not_minutes():
    """End to end, at the most a side may hold. It took about a minute and
    a half of the one worker; the bound here is loose on purpose, for a
    slow machine."""
    left, right = _sharing_one_amount("shopify_paypal", 5000, left_at="2026-10-05T10:00:00+00:00",
                                      right_at="2026-10-05T11:00:00+00:00")
    began = time.perf_counter()
    result = match(get("shopify_paypal"), left, right)
    assert time.perf_counter() - began < 20
    assert result.summary["ambiguous"] == 5000 and result.summary["unmatched_left"] == 0


def test_a_rule_that_runs_out_of_work_stops_and_says_so(monkeypatch):
    """Records the rule did not get to are undecided: not matched, and not
    reported as having no counterpart, which nothing established."""
    monkeypatch.setattr(engine, "MATCH_WORK", PAIR * 40)
    spec = get("shopify_paypal")
    rule = _fuzzy_of("shopify_paypal")
    left, right = _sharing_one_amount("shopify_paypal", 30, left_at="2026-10-05T10:00:00+00:00",
                                      right_at="2026-10-05T11:00:00+00:00")
    lone: Row = {"name": "#lone"}
    put(lone, rule.amount, 77), put(lone, rule.currency, "USD"), put(lone, rule.timestamp_left, "2026-10-05T10:00:00+00:00")
    result = match(spec, [*left, lone], right)
    # The first order was weighed against all thirty payments inside the
    # allowance: an ordinary ambiguity. The rest were not reached.
    assert [a.reason for a in result.ambiguous] == [None] + [STOPPED] * 29
    assert [a.more for a in result.ambiguous] == [False] + [True] * 29
    assert result.unmatched_left == [lone], "one with no record of its amount at all is still plainly unmatched"
    assert result.matched == []
    row = [r for r in result.rows() if r["status"] == "ambiguous"][1]
    assert row["reason"] == STOPPED and row["evidence"]["more"] is True


def test_a_stopped_rule_never_turns_a_lone_candidate_into_a_match(monkeypatch):
    """One candidate found before the work ran out is not known to be the
    only one, so it is not taken."""
    rule = _fuzzy_of("shopify_paypal")
    left, right = _sharing_one_amount("shopify_paypal", 3, left_at="2026-10-05T10:00:00+00:00",
                                      right_at="2026-10-05T11:00:00+00:00")
    out = ReconResult(name="x")
    engine._apply_fuzzy(rule, None, left[:1], right, out, _Work(PAIR + 1))    # where to look, and one pair
    [undecided] = out.ambiguous
    assert out.matched == [] and undecided.reason == STOPPED and len(undecided.candidates) == 1


def _batches(n: int, *, net=lambda i: 1000 + i, closed="2026-10-05") -> list[Row]:
    return [{"batch_id": f"B{i}", "mid": "M1", "net_minor": net(i), "currency": "USD", "closed_on": closed}
            for i in range(n)]


def _deposits(n: int, *, amount=lambda i: 9_000_000 + i, posted="2026-10-06") -> list[Row]:
    return [{"fitid": f"F{i}", "amount_minor": amount(i), "currency": "USD", "posted_on": posted} for i in range(n)]


def test_a_sum_no_subset_reaches_is_not_searched_through_every_combination():
    """Twenty-four batches of nothing and a deposit of one cent: no subset
    sums to it, nothing prunes, and each deposit cost a third of a second.
    Five thousand deposits were most of half an hour."""
    spec = get("batches_deposits")
    rule = spec.keys[0]
    work = _Work()
    with pytest.raises(engine._OutOfWork):
        for _ in range(8):
            _subsets_summing(_batches(24, net=lambda i: 0), rule.amount, 1, rule, work)
    began = time.perf_counter()
    result = match(spec, _batches(24, net=lambda i: 0), _deposits(5000, amount=lambda i: 1))
    assert time.perf_counter() - began < 20
    reasons = {a.reason for a in result.ambiguous}
    assert reasons == {STOPPED} and len(result.ambiguous) + len(result.unmatched_right) == 5000
    assert len(result.unmatched_right) <= 5, "only the deposits searched to the end are called unmatched"
    assert result.matched == []


def test_deposits_and_batches_in_one_window_cost_what_they_are_offered(monkeypatch):
    spec = get("batches_deposits")
    offered = []
    real = _Work.spend

    def counting(self, n=1):
        offered.append(n)
        return real(self, n)

    monkeypatch.setattr(_Work, "spend", counting)
    result = match(spec, _batches(5000), _deposits(5000))
    assert sum(offered) <= MATCH_WORK + 5000, "it stopped at the bound"
    assert len(result.ambiguous) == 5000 and result.unmatched_right == []
    crowded = [a for a in result.ambiguous if a.reason != STOPPED]
    assert crowded and all(a.reason.startswith("more than 24 candidates") for a in crowded)


def test_two_dates_ten_thousand_years_apart_cost_a_few_days_of_counting(monkeypatch):
    """A lag was counted a day at a time between the two records' own
    dates: 3.6 million steps for one pair at the calendar's ends."""
    steps = []
    real = calendar.is_business_day
    monkeypatch.setattr(calendar, "is_business_day", lambda day: steps.append(day) or real(day))
    assert SettlementLag(max_days=3).days("0001-01-01", "9999-12-31") == 4, "more than three: no further is counted"
    assert len(steps) < 10
    steps.clear()
    result = match(get("batches_deposits"), _batches(200, closed="0001-01-01"), _deposits(200, posted="9999-12-31"))
    assert len(steps) < 200 and result.matched == [] and result.ambiguous == []
    clover = get("clover_payments_settlements")
    rule = _fuzzy_of("clover_payments_settlements")
    left, right = _sharing_one_amount("clover_payments_settlements", 200, left_at="0001-01-01", right_at="9999-12-31")
    steps.clear()
    result = match(clover, left, right)
    assert len(steps) < 200 and result.matched == [] and rule.lag is not None


def test_a_days_single_price_sales_are_told_apart_by_their_cards():
    """Not an attack: a busy shop with one price. Five thousand payments
    of one amount on one day against their settlement lines, each told
    apart by its card. Filed by the card too, each is offered its own line
    and they all match; compared one with every other this was twenty-five
    million pairs, and with a bound alone it would have stopped."""
    rule = _fuzzy_of("clover_payments_settlements")
    assert rule.agree, "this reconciliation tells same-amount payments apart by a field"
    first = rule.agree[0]
    left, right = [], []
    for i in range(5000):
        l: Row = {}
        put(l, rule.amount, 450), put(l, rule.currency, "USD"), put(l, rule.timestamp_left, "2026-10-05")
        put(l, first.left, f"{i:04d}")
        r: Row = {}
        put(r, rule.right_amount, 450), put(r, rule.right_currency, "USD"), put(r, rule.timestamp_right, "2026-10-06")
        put(r, first.right_path, f"{i:04d}")
        left.append(l), right.append(r)
    result = match(get("clover_payments_settlements"), left, right[::-1])
    assert len(result.matched) == 5000 and result.ambiguous == []
    assert all(dig(p.left, first.left) == dig(p.right, first.right_path) for p in result.matched)


def test_a_period_looks_only_at_the_records_inside_it(monkeypatch):
    spec = get("avatax_collected_filed")
    rule = spec.keys[0]
    assert isinstance(rule, PeriodGroup)
    (o_path, m_path), = rule.share[:1] or [("state", "state")]
    ones_left = rule.one == "left"
    periods, sales = [], []
    for month in range(1, 13):
        one: Row = {}
        put(one, rule.start, f"2026-{month:02d}-01"), put(one, rule.end, f"2026-{month:02d}-28")
        for a, _ in rule.share:
            put(one, a, "CA")
        periods.append(one)
    for i in range(4800):
        sale: Row = {}
        put(sale, rule.timestamp_many, f"2026-{i % 12 + 1:02d}-{i % 28 + 1:02d}")
        for _, b in rule.share:
            put(sale, b, "CA")
        sales.append(sale)
    left, right = (periods, sales) if ones_left else (sales, periods)
    spent = []
    real = _Work.spend
    monkeypatch.setattr(_Work, "spend", lambda self, n=1: spent.append(n) or real(self, n))
    result = match(spec, left, right)
    assert sum(spent) == 4800, "each sale looked at once, by the period it falls in"
    assert told(result) == told(plainly(monkeypatch, spec, left, right))
    assert o_path and m_path


# ── what an attacker's pass found in the bound itself ───────────────
# A second fresh agent attacked this change (docs/audit/red-team.md,
# 2026-11-11) and proved four gaps. Its exploits, turned around.

def test_the_other_sides_tags_do_not_multiply_where_a_record_looks():
    """Five thousand payments with no card against five thousand
    settlement lines each with a different one: a place to look was made
    for every card, for every payment. Twenty-five million places, none of
    them counted, nineteen seconds."""
    rule = _fuzzy_of("clover_payments_settlements")
    first = rule.agree[0]
    left, right = [], []
    for i in range(5000):
        l: Row = {}
        put(l, rule.amount, 450), put(l, rule.currency, "USD"), put(l, rule.timestamp_left, "2026-10-05")
        r: Row = {}
        put(r, rule.right_amount, 450), put(r, rule.right_currency, "USD"), put(r, rule.timestamp_right, "2026-09-01")
        put(r, first.right_path, f"{i:04d}")
        left.append(l), right.append(r)
    out, work = ReconResult(name="x"), _Work()
    still, rest = engine._apply_fuzzy(rule, None, left, right, out, work)
    assert MATCH_WORK - work.left == 5000, "one place a payment, and no line inside its lag to weigh"
    assert (len(still), len(rest), out.matched, out.ambiguous) == (5000, 5000, [], [])
    filed = engine._Filed(tagging=True)
    assert filed.keys("USD", [450], None) == [("USD", 450)]
    assert filed.keys("USD", [450, 451], "1234") == [("USD", 450, "1234"), ("USD", 450, None),
                                                    ("USD", 451, "1234"), ("USD", 451, None)]


def test_amounts_within_a_wide_tolerance_are_counted_too(monkeypatch):
    rule = _fuzzy_of("shopify_paypal").model_copy(update={"amount_tolerance_minor": 10**6})
    left, right = [], []
    for i in range(3000):
        l: Row = {}
        put(l, rule.amount, 5000), put(l, rule.currency, "USD"), put(l, rule.timestamp_left, "2026-10-05T10:00:00+00:00")
        r: Row = {}
        put(r, rule.right_amount, i), put(r, rule.right_currency, "USD"), put(r, rule.timestamp_right, "2020-01-01T00:00:00+00:00")
        left.append(l), right.append(r)
    out, work = ReconResult(name="x"), _Work()
    engine._apply_fuzzy(rule, None, left, right, out, work)
    assert work.spent_out, "three thousand amounts, three thousand times: it is counted, and it stops"
    assert {a.reason for a in out.ambiguous} == {STOPPED} and out.matched == []


def test_ten_thousand_records_under_one_reference_are_not_listed_ten_thousand_times():
    """`delivery_payout_lines` reads ten thousand rows a side. All under
    one payout id, each asking record was given the whole list again: a
    hundred million references held in memory, and most of a gigabyte."""
    spec = get("delivery_payout_lines")
    rule = spec.keys[0]
    assert isinstance(rule, GroupRef) and rule.max_members < 10_000
    left = [{rule.left: "PO-1", "n": i} for i in range(10_000)]
    right = [{rule.right: "PO-1", "n": i} for i in range(10_000)]
    out = ReconResult(name="x")
    began = time.perf_counter()
    engine._apply_group_ref(rule, left, right, out)
    assert time.perf_counter() - began < 5
    assert len(out.ambiguous) == 10_000 and out.matched == []
    assert sum(len(a.candidates) for a in out.ambiguous) == 10_000 * CANDIDATES_LISTED
    assert all(a.more and a.reason == f"10000 records carry this reference, more than {rule.max_members}"
               for a in out.ambiguous), "the true count is still said"


def test_a_reference_claimed_once_is_not_looked_through_again():
    rule = get("clover_orders_payments").keys[0]
    assert isinstance(rule, GroupRef)
    one_path, many_path = (rule.left, rule.right) if rule.many == "right" else (rule.right, rule.left)
    ones = [{one_path: "O-1", "n": i} for i in range(3)]
    many = [{many_path: "O-1", "n": i} for i in range(4)]
    left, right = (ones, many) if rule.many == "right" else (many, ones)
    out = ReconResult(name="x")
    still_left, still_right = engine._apply_group_ref(rule, left, right, out)
    [pair] = out.matched
    assert pair.members == many, "the first order takes all four payments"
    assert (still_left if rule.many == "right" else still_right) == ones[1:], "and the other two find none"


def test_a_stopped_sum_does_not_call_the_other_sides_records_never_paid():
    """The bound's own fault, and the worst of the four: when the search
    stopped, the deposit it had not reached was undecided, but its batch
    was returned as unclaimed and reported as a batch never paid. Nothing
    had established that."""
    spec = get("batches_deposits")
    batches = [*_batches(24, net=lambda i: 0), {"batch_id": "REAL", "mid": "M2", "net_minor": 5000,
                                                 "currency": "USD", "closed_on": "2026-10-05"}]
    deposits = [*_deposits(5000, amount=lambda i: 1), {"fitid": "REAL", "amount_minor": 5000, "currency": "USD",
                                                       "posted_on": "2026-10-06"}]
    alone = match(spec, batches[-1:], deposits[-1:])
    assert len(alone.matched) == 1, "on their own the two pair"
    result = match(spec, batches, deposits)
    assert not any(b["batch_id"] == "REAL" for b in result.unmatched_left), "not reported as never paid"
    [waiting] = [u for u in result.unverifiable_left if u.record["batch_id"] == "REAL"]
    assert waiting.reason == STOPPED_OTHER
    [undecided] = [a for a in result.ambiguous if a.left["fitid"] == "REAL"]
    assert undecided.reason == STOPPED
    assert result.unmatched_left == [], "no batch is called unpaid by a search that was cut short"
    row = next(r for r in result.rows() if r["status"] == "unverifiable_left")
    assert row["reason"] == STOPPED_OTHER


def test_a_stopped_fuzzy_rule_does_not_call_the_other_sides_records_orphans(monkeypatch):
    monkeypatch.setattr(engine, "MATCH_WORK", PAIR * 40)
    spec, rule = get("shopify_paypal"), _fuzzy_of("shopify_paypal")
    left, right = _sharing_one_amount("shopify_paypal", 30, left_at="2026-10-05T10:00:00+00:00",
                                      right_at="2026-10-05T11:00:00+00:00")
    other: Row = {"invoice_id": "#other"}
    put(other, rule.right_amount, 77), put(other, rule.right_currency, "USD")
    put(other, rule.timestamp_right, "2026-10-05T11:00:00+00:00")
    result = match(spec, left, [*right, other])
    assert result.unmatched_right == [other], "a payment no stopped order could have paired with is plainly unmatched"
    assert [u.reason for u in result.unverifiable_right] == [STOPPED_OTHER] * 30
    assert result.matched == []


def test_a_period_that_was_not_looked_into_leaves_its_records_undecided(monkeypatch):
    spec = get("avatax_collected_filed")
    rule = spec.keys[0]
    period: Row = {}
    put(period, rule.start, "2026-10-01"), put(period, rule.end, "2026-10-31")
    for a, _ in rule.share:
        put(period, a, "CA")
    sales = []
    for i in range(20):
        sale: Row = {"n": i}
        put(sale, rule.timestamp_many, f"2026-10-{i + 1:02d}")
        for _, b in rule.share:
            put(sale, b, "CA")
        sales.append(sale)
    left, right = ([period], sales) if rule.one == "left" else (sales, [period])
    monkeypatch.setattr(engine, "MATCH_WORK", 5)
    result = match(spec, left, right)
    many_unmatched = result.unmatched_right if rule.one == "left" else result.unmatched_left
    many_waiting = result.unverifiable_right if rule.one == "left" else result.unverifiable_left
    assert many_unmatched == [] and [u.reason for u in many_waiting] == [STOPPED_OTHER] * 20
    assert [a.reason for a in result.ambiguous] == [STOPPED] and result.matched == []


def test_no_shape_of_rows_found_so_far_takes_more_than_a_few_seconds():
    """Every case above and below, timed loosely for a slow machine: a
    match that is cut off by a timeout goes on in its thread until it
    ends, so how long one can last is what bounds that too."""
    cases = {
        "one amount, one hour": (get("shopify_paypal"), *_sharing_one_amount(
            "shopify_paypal", 5000, left_at="2026-10-05T10:00:00+00:00", right_at="2026-10-05T11:00:00+00:00")),
        "one window": (get("batches_deposits"), _batches(5000), _deposits(5000)),
        "sums of nothing": (get("batches_deposits"), _batches(24, net=lambda i: 0), _deposits(5000, amount=lambda i: 1)),
        "one payout id": (get("delivery_payout_lines"),
                          [{get("delivery_payout_lines").keys[0].left: "P", "n": i} for i in range(10_000)],
                          [{get("delivery_payout_lines").keys[0].right: "P", "n": i} for i in range(10_000)]),
    }
    for name, (spec, left, right) in cases.items():
        began = time.perf_counter()
        result = match(spec, left, right)
        result.rows(), result.summary
        assert time.perf_counter() - began < 20, name


# ── what a person is shown ───────────────────────────────────────────

def test_the_across_rails_verdict_says_more_than_were_listed():
    """Shopify orders against PayPal and Stripe: an order with more
    candidates than are listed reads so, not as "50 equal candidates"."""
    from datetime import UTC, datetime

    from okwan_recon import sample

    spec = get_across("rails")
    rows = sample.rows(datetime(2026, 11, 11, 15, 0, tzinfo=UTC))
    order, payment = rows["shopify"][0], rows["paypal"][0]
    ledger = [{**order, "name": f"#9{i:03d}"} for i in range(60)]
    paypal = [{**payment, "transaction_id": f"PP{i}", "invoice_id": None,
               "amount_minor": order["total_price_minor"]} for i in range(60)]
    members = list(zip(spec.members(), spec.rails, strict=True))
    first, rail = members[0]
    result = match(first, ledger, paypal)
    assert len(result.ambiguous) == 60 and all(a.more for a in result.ambiguous)
    folded = across.fold(spec, ledger, [(first, rail, result)] + [
        (m, r, match(m, ledger, [])) for m, r in members[1:]], None)
    reasons = {v.reason for row in folded.orders for v in row.rails if v.status == "ambiguous"}
    assert reasons == {f"more than {CANDIDATES_LISTED} candidates; none chosen (on {first.right.connector})"}


def test_an_ordinary_ambiguity_reads_as_it_always_did():
    rule = _fuzzy_of("shopify_paypal")
    left, right = _sharing_one_amount("shopify_paypal", 3, left_at="2026-10-05T10:00:00+00:00",
                                      right_at="2026-10-05T11:00:00+00:00")
    out = ReconResult(name="x")
    engine._apply_fuzzy(rule, None, left, right, out)
    assert [(len(a.candidates), a.more, a.reason) for a in out.ambiguous] == [(3, False, None)] * 3
    assert "more" not in out.rows()[0]["evidence"]


# ── off the event loop ───────────────────────────────────────────────

async def test_matching_runs_off_the_event_loop(monkeypatch):
    """Bounded is still up to a second of plain computing. The loop that
    answers every other request is not where it happens."""
    loop_thread = threading.get_ident()
    where: list[int] = []
    real = engine.match

    def seen(*args, **kwargs):
        where.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(across, "match", seen)
    monkeypatch.setattr(runner, "match", seen)

    async def fetch(ref, resolver, cap, overrides):
        return [], Coverage(source=ref.qualified, records=0, cap=cap, truncated=False)

    monkeypatch.setattr(runner, "fetch_side", fetch)
    await across.run_across(get_across("rails"), lambda c, f: dict.fromkeys(f, ""), fetch=fetch)
    await runner.run(get("shopify_paypal"), lambda c, f: dict.fromkeys(f, ""))
    assert len(where) == 3 and loop_thread not in where
