"""Declarative reconciliation spec — the one definition.

A Reconciliation names two connector resources and how their records
correspond. Emitters derive the MCP tool, the DuckDB view and the REST
route from this object and nothing else; per-reconciliation glue code
is a contract violation, exactly as it is for connectors.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from okwan_core import get as get_connector

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


class Fuzzy(Frozen):
    """Amount + currency inside a time window. Fallback after exact refs.

    Amounts are compared in integer minor units via okwan_core.currency,
    so an XOF 5000 transaction matches an XOF 5000 order rather than
    being scaled by 100.
    """

    kind: Literal["fuzzy"] = "fuzzy"
    amount: str
    currency: str
    timestamp_left: str = "created_at"
    timestamp_right: str = "created_at"
    window: str = "48h"
    amount_tolerance_minor: int = 0

    @field_validator("window")
    @classmethod
    def _check_window(cls, v: str) -> str:
        parse_window(v)
        return v

    @property
    def window_delta(self) -> timedelta:
        return parse_window(self.window)


MatchRule = Annotated[Union[ExactRef, Fuzzy], Field(discriminator="kind")]


class MSISDN(Frozen):
    """Phone-number identity, normalised to E.164 digits without '+'.

    Used as a guard on fuzzy matches: two same-amount records in the
    same window will not be paired if both carry phone numbers that
    disagree. Silent on either side when a number is absent.
    """

    kind: Literal["msisdn"] = "msisdn"
    left: str
    right: str | None = None
    default_country_code: str | None = None

    def normalize(self, raw: Any) -> str | None:
        if raw is None:
            return None
        digits = re.sub(r"\D", "", str(raw))
        if not digits:
            return None
        if digits.startswith("00"):
            digits = digits[2:]
        elif digits.startswith("0") and self.default_country_code:
            digits = self.default_country_code + digits[1:]
        return digits or None


class Reconciliation(Frozen):
    name: str
    title: str | None = None
    description: str = ""
    left: ResourceRef
    right: ResourceRef
    keys: list[MatchRule]
    identity: MSISDN | None = None
    max_records: int = Field(default=500, ge=1, le=10_000)

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
