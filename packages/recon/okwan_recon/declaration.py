"""Declarative reconciliation spec — the one definition.

A Reconciliation names two connector resources and how their records
correspond. Emitters derive the MCP tool, the DuckDB view and the REST
route from this object and nothing else; per-reconciliation glue code
is a contract violation, exactly as it is for connectors.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Annotated, Any, Literal

from okwan_core import get as get_connector
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNIT = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_window(value: str) -> timedelta:
    m = _DURATION.match(value)
    if not m:
        raise ValueError(f"invalid window {value!r}; expected forms like '48h', '30m', '7d'")
    return timedelta(**{_UNIT[m.group(2)]: int(m.group(1))})


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ResourceRef(Frozen):
    """Points at an operation already defined by a registered connector.

    Nothing here restates the upstream schema or transport — the
    connector definition remains the only place that knows those.
    """

    connector: str
    resource: str
    operation: str = "list"
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def qualified(self) -> str:
        return f"{self.connector}.{self.resource}.{self.operation}"


class ExactRef(Frozen):
    """Deterministic join on a shared reference carried by both sides."""

    kind: Literal["exact_ref"] = "exact_ref"
    left: str
    right: str
    case_sensitive: bool = False


class SettlementLag(Frozen):
    """How many bank business days one side settles after the other (E2).

    Replaces a symmetric calendar window where money settles one way: a
    deposit lands 0 to `max_days` business days after the batch it pays,
    never before it. Days are the Federal Reserve's (weekends and US bank
    holidays skipped, `calendar.py`), counted in the merchant's timezone,
    and a bare date in a record is that day.
    """

    min_days: int = Field(default=0, ge=0, le=30)
    max_days: int = Field(default=3, ge=0, le=30)
    direction: Literal["right_after_left", "left_after_right"] = "right_after_left"
    calendar: Literal["us_bank"] = "us_bank"
    timezone: str = "America/New_York"

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown timezone {v!r}") from None
        return v

    @field_validator("max_days")
    @classmethod
    def _ordered(cls, v: int, info) -> int:
        if v < info.data.get("min_days", 0):
            raise ValueError("max_days must be at least min_days")
        return v

    def days(self, left: Any, right: Any) -> int | None:
        """Business days from the earlier side to the later, or None when
        either date is missing or they run the wrong way."""
        from .calendar import as_date, business_days_after

        l_day, r_day = as_date(left, self.timezone), as_date(right, self.timezone)
        if l_day is None or r_day is None:
            return None
        if self.direction == "right_after_left":
            return business_days_after(l_day, r_day)
        return business_days_after(r_day, l_day)

    def admits(self, left: Any, right: Any) -> bool:
        n = self.days(left, right)
        return n is not None and self.min_days <= n <= self.max_days


class Agree(Frozen):
    """A field both sides of a fuzzy pair must not contradict.

    Amount and day are weak alone: a café rings up two 12.50 coffees on
    the same morning, and both sit in the processor's export. The card's
    last four digits, or the authorization code, tell them apart. A
    candidate whose value differs is no candidate at all; one where
    either side lacks the field stays a candidate, because a column a
    file does not carry is not evidence of a mismatch. Where both carry
    it and agree, the pair's evidence says so.
    """

    left: str
    right: str | None = Field(default=None, description="Path on the right side; defaults to `left`")
    case_sensitive: bool = False

    @property
    def right_path(self) -> str:
        return self.right or self.left


class Fuzzy(Frozen):
    """Amount + currency inside a time window. Fallback after exact refs.

    Both amount paths name integer minor-unit fields, and the engine
    compares them as stored, so an XOF 5000 transaction matches an XOF
    5000 order. `amount_tolerance_minor` is in those same units.
    """

    kind: Literal["fuzzy"] = "fuzzy"
    amount: str = Field(description="Amount path on the left side")
    currency: str = Field(description="Currency path on the left side")
    amount_right: str | None = Field(
        default=None, description="Amount path on the right side; defaults to `amount`"
    )
    currency_right: str | None = Field(
        default=None, description="Currency path on the right; defaults to `currency`"
    )
    timestamp_left: str = "created_at"
    timestamp_right: str = "created_at"
    window: str = Field(
        default="7d",
        description=(
            "Time window for amount matching. Wide is safer than narrow: "
            "surplus candidates report as ambiguous, while a short window "
            "silently drops real matches. Narrow deliberately."
        ),
    )
    amount_tolerance_minor: int = 0
    lag: SettlementLag | None = Field(
        default=None,
        description="Settlement lag in business days; replaces `window` when set",
    )
    agree: tuple[Agree, ...] = Field(
        default=(),
        description="Fields a candidate must not contradict (a card's last four digits)",
    )

    @property
    def right_amount(self) -> str:
        return self.amount_right or self.amount

    @property
    def right_currency(self) -> str:
        return self.currency_right or self.currency

    @field_validator("window")
    @classmethod
    def _check_window(cls, v: str) -> str:
        parse_window(v)
        return v

    @property
    def window_delta(self) -> timedelta:
        return parse_window(self.window)


class GroupRef(Frozen):
    """One record against every record on the other side that carries its
    reference (E1, one-to-many).

    An order paid with two cards on one rail, or a charge and its partial
    refund as separate rows, is one order with several records. ExactRef
    would pair the first and report the rest as payments with no order;
    this pairs the order with all of them and compares the sum. `many`
    names the side holding the several records.
    """

    kind: Literal["group_ref"] = "group_ref"
    left: str
    right: str
    many: Literal["left", "right"] = "right"
    case_sensitive: bool = False
    max_members: int = Field(
        default=20, ge=2, le=100,
        description="More records than this under one reference is not one order: reported ambiguous",
    )


class GroupSum(Frozen):
    """Several records on one side whose amounts sum to one record on the
    other, with no shared reference (E1, many-to-one).

    A deposit pays the batches closed since the last one: a weekend's
    three batches land as one Monday deposit. Candidates are the `many`
    side's unclaimed records in the same currency, inside the window (or
    `lag`), sharing every field in `share` (a merchant id, say); a subset
    of at most `max_members` must sum to the one record within
    `amount_tolerance_minor`. Exactly one subset is a match. Two subsets
    that both fit are ambiguous and claim nothing, the same rule as
    Fuzzy: choosing between them would be invention.
    """

    kind: Literal["group_sum"] = "group_sum"
    many: Literal["left", "right"] = "left"
    amount: str = Field(description="Amount path on the many side, integer minor units")
    amount_one: str | None = Field(default=None, description="Amount path on the one side; defaults to `amount`")
    currency: str = "currency"
    currency_one: str | None = None
    timestamp_many: str = "created_at"
    timestamp_one: str = "created_at"
    share: tuple[str, ...] = Field(
        default=(), description="Many-side fields every member of a group must have equal",
    )
    window: str = "7d"
    lag: SettlementLag | None = None
    max_members: int = Field(default=8, ge=2, le=12)
    ref_many: str | None = Field(default=None, description="A human id on the many side (a batch id), for evidence")
    ref_one: str | None = Field(default=None, description="A human id on the one side (a deposit's FITID)")
    max_candidates: int = Field(
        default=24, ge=2, le=40,
        description="Candidates searched per record; more is reported ambiguous rather than guessed",
    )
    amount_tolerance_minor: int = 0

    @field_validator("window")
    @classmethod
    def _check_window(cls, v: str) -> str:
        parse_window(v)
        return v

    @property
    def window_delta(self) -> timedelta:
        return parse_window(self.window)

    @property
    def one_amount(self) -> str:
        return self.amount_one or self.amount

    @property
    def one_currency(self) -> str:
        return self.currency_one or self.currency

    def sides(self) -> dict[str, dict[str, str]]:
        """Amount, currency and timestamp paths by side."""
        many = {"amount": self.amount, "currency": self.currency, "timestamp": self.timestamp_many}
        one = {"amount": self.one_amount, "currency": self.one_currency, "timestamp": self.timestamp_one}
        return {"left": many, "right": one} if self.many == "left" else {"left": one, "right": many}


MatchRule = Annotated[ExactRef | Fuzzy | GroupRef | GroupSum, Field(discriminator="kind")]


class MSISDN(Frozen):
    """Phone-number identity, normalised to E.164 digits without '+'.

    A guard on fuzzy matches: two same-amount records in the same window
    are not paired if their phone numbers disagree. Silent when either
    side is absent — absence is not evidence of a mismatch.

    A national-format number (leading 0) is ambiguous without knowing
    the country. `country_codes` lists the codes the merchant actually
    operates in; every expansion is kept as a candidate and two numbers
    agree if they share any. One hardcoded code silently blocks correct
    matches for merchants collecting across borders.
    """

    kind: Literal["msisdn"] = "msisdn"
    left: str
    right: str | None = None
    country_codes: tuple[str, ...] = ()

    def candidates(self, raw: Any) -> frozenset[str]:
        """Every plausible E.164 form of `raw`. Empty when unusable."""
        if raw is None:
            return frozenset()
        digits = re.sub(r"\D", "", str(raw))
        if not digits:
            return frozenset()
        forms: set[str] = {digits}
        if digits.startswith("00"):
            # Reading A: international dial-out prefix -> already E.164.
            forms.add(digits[2:])
        if digits.startswith("0"):
            # Reading B: national trunk prefix -> prepend each country code.
            national = digits[1:]
            if national:
                forms.update(cc + national for cc in self.country_codes)
        return frozenset(f for f in forms if f)

    def agrees(self, left_raw: Any, right_raw: Any) -> bool | None:
        """True/False when both sides are present, None when unknown."""
        l, r = self.candidates(left_raw), self.candidates(right_raw)
        if not l or not r:
            return None
        return bool(l & r)


class AmountRef(Frozen):
    """The money figure to compare on an already-matched pair."""

    left: str
    right: str | None = None
    currency: str = "currency"
    currency_right: str | None = None

    @property
    def right_path(self) -> str:
        return self.right or self.left

    @property
    def right_currency(self) -> str:
        return self.currency_right or self.currency


class Explains(Frozen):
    """A field whose value accounts for a discrepancy of the same size.

    `side` names which record carries it. A refund is recorded on the
    order ledger; a fee would be recorded on the rail. Sign follows the
    discrepancy convention (left minus right), so a right-side refund
    explains a positive difference.

    `paths` instead of `path` sums several fields into one figure (E3):
    a processor's fee rule, where a deposit is the gross less the
    discount, the chargebacks and the adjustments, each its own column.
    A missing field counts as nothing; all of them missing, as no figure.
    """

    path: str | None = None
    paths: tuple[str, ...] = ()
    side: Literal["left", "right"] = "right"
    label: str = Field(
        default="adjustment",
        description="What this explains, surfaced in output: refund, fee, chargeback...",
    )
    sign: Literal["positive", "negative", "either"] = "positive"

    @model_validator(mode="after")
    def _one_source(self) -> Explains:
        if (self.path is None) == (not self.paths):
            raise ValueError("an explanation names exactly one of `path` or `paths`")
        return self

    @property
    def fields(self) -> tuple[str, ...]:
        return (self.path,) if self.path is not None else self.paths

    def figure(self, read) -> int | None:
        """The amount this explanation states, read through `read(path)`."""
        total, seen = 0, False
        for path in self.fields:
            value = read(path)
            if value is None:
                continue
            try:
                total += int(value)
            except (TypeError, ValueError):
                return None
            seen = True
        return total if seen else None

    def allows(self, amount: int, discrepancy: int) -> bool:
        """Non-zero, and pointing the way `sign` says."""
        if amount == 0 or discrepancy == 0:
            return False
        if self.sign == "positive" and discrepancy < 0:
            return False
        return not (self.sign == "negative" and discrepancy > 0)

    def accounts_for(self, value: Any, discrepancy: int) -> bool:
        if value is None or discrepancy == 0:
            return False
        try:
            amount = int(value)
        except (TypeError, ValueError):
            return False
        if amount == 0:
            return False
        if self.sign == "positive" and discrepancy < 0:
            return False
        if self.sign == "negative" and discrepancy > 0:
            return False
        # Compare magnitudes. Rails report adjustments with their own
        # sign convention — Shopify states a refund positive, PayPal
        # states a fee negative — and both describe the same size of
        # difference. `sign` above already constrains direction, so
        # requiring the raw value to match as well would mean only
        # rails that happen to agree with our convention can explain
        # anything.
        return abs(amount) == abs(discrepancy)


class Reconciliation(Frozen):
    name: str
    title: str | None = None
    description: str = ""
    left: ResourceRef
    right: ResourceRef
    keys: list[MatchRule]
    amount: AmountRef | None = Field(
        default=None,
        description="Figure to compare on matched pairs; defaults to the first Fuzzy rule",
    )
    explains: list[Explains] = Field(
        default_factory=list,
        description="Known causes that reclassify a discrepancy as explained",
    )
    identity: MSISDN | None = None
    lookback: str | None = Field(
        default=None,
        description=(
            "How far back to read, in the same grammar as a Fuzzy window "
            "('90d', '48h'). Resolved at call time to start_date/end_date and "
            "passed to each side whose list input accepts them; a side that "
            "does not is read as it would be without it."
        ),
    )
    max_records: int = Field(
        default=5_000,
        ge=1,
        le=10_000,
        description=(
            "Records read per side before the read stops and is reported "
            "truncated. A fetch bound, not a page size: callers page the "
            "result rows separately."
        ),
    )

    @field_validator("lookback")
    @classmethod
    def _check_lookback(cls, v: str | None) -> str | None:
        if v is not None:
            parse_window(v)
        return v

    @property
    def lookback_delta(self) -> timedelta | None:
        return parse_window(self.lookback) if self.lookback else None

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", v):
            raise ValueError("name must be lower_snake_case")
        return v

    @field_validator("keys")
    @classmethod
    def _non_empty(cls, v: list[MatchRule]) -> list[MatchRule]:
        if not v:
            raise ValueError("at least one match rule is required")
        return v

    @property
    def resolved_amount(self) -> AmountRef | None:
        """Explicit declaration, or the first Fuzzy rule's paths."""
        if self.amount is not None:
            return self.amount
        for rule in self.keys:
            if isinstance(rule, Fuzzy):
                return AmountRef(
                    left=rule.amount,
                    right=rule.right_amount,
                    currency=rule.currency,
                    currency_right=rule.right_currency,
                )
            if isinstance(rule, GroupSum):
                s = rule.sides()
                return AmountRef(
                    left=s["left"]["amount"], right=s["right"]["amount"],
                    currency=s["left"]["currency"], currency_right=s["right"]["currency"],
                )
        return None

    @property
    def timestamp_paths(self) -> tuple[str | None, str | None]:
        """Where each side dates its records — the first Fuzzy rule's
        paths. None when no rule says, so coverage cannot place a record
        in time and a bounded counterpart cannot vouch for it."""
        for rule in self.keys:
            if isinstance(rule, Fuzzy):
                return rule.timestamp_left, rule.timestamp_right
            if isinstance(rule, GroupSum):
                s = rule.sides()
                return s["left"]["timestamp"], s["right"]["timestamp"]
        return None, None

    @property
    def display_title(self) -> str:
        return self.title or self.name.replace("_", " ").title()

    @property
    def view_name(self) -> str:
        return f"recon.{self.name}"

    @property
    def tool_name(self) -> str:
        return f"reconcile_{self.name}"

    @property
    def rest_path(self) -> str:
        return f"/v1/reconciliations/{self.name}"

    def validate_against_registry(self) -> None:
        """Check both sides resolve, and that neither can mutate state.

        Deferred rather than done at construction so a declaration can
        be imported before its connectors are. Reconciliation is a
        read-only primitive by construction: pointing one at a write
        operation is rejected here, not left to a runtime annotation.
        """
        for side, ref in (("left", self.left), ("right", self.right)):
            try:
                connector = get_connector(ref.connector)
            except KeyError:
                raise ValueError(
                    f"{self.name}.{side}: unknown connector {ref.connector!r}"
                ) from None
            resource = connector.resources.get(ref.resource)
            if resource is None:
                available = ", ".join(sorted(connector.resources)) or "none"
                raise ValueError(
                    f"{self.name}.{side}: connector {ref.connector!r} has no resource "
                    f"{ref.resource!r} (available: {available})"
                )
            op = resource.operations.get(ref.operation)
            if op is None:
                available = ", ".join(sorted(resource.operations)) or "none"
                raise ValueError(
                    f"{self.name}.{side}: resource {ref.qualified!r} has no operation "
                    f"{ref.operation!r} (available: {available})"
                )
            if not op.is_read_only:
                raise ValueError(
                    f"{self.name}.{side}: {ref.qualified} is a write operation; "
                    "reconciliations may only read"
                )
