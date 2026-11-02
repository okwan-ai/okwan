"""Minor-unit arithmetic — shared by connectors and the query layer.

Most payment APIs quote amounts in the currency's minor unit (kobo,
pesewas, cents). Zero-decimal currencies have no minor unit, so the
integer *is* the major amount. This table lives in core because both
connectors and the reconciliation layer depend on it; two copies would
drift and silently mis-match XOF settlements by a factor of 100.

Rails that quote decimal strings in major units (Shopify, PayPal)
convert through `money_to_minor`, the one conversion into minor units.
There is deliberately no float version.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: ISO-4217 currencies with no minor unit (exponent 0).
ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    {"XOF", "XAF", "JPY", "KRW", "VND", "CLP", "ISK", "PYG", "RWF", "UGX", "VUV", "XPF"}
)


def minor_unit_factor(currency: str | None) -> int:
    """Multiplier between major and minor units for `currency`."""
    return 1 if (currency or "").upper() in ZERO_DECIMAL_CURRENCIES else 100


def to_major(amount_minor: float, currency: str | None) -> float:
    """Minor units -> human-facing major amount."""
    return float(amount_minor) / minor_unit_factor(currency)


def money_to_minor(value: Any, currency: str | None) -> int | None:
    """Decimal-string major amount -> integer minor units.

    Never routes through float: `299.10 * 100` is 29909.999... in binary
    floating point, and money that rounds the wrong way is the bug class
    reconciliation exists to catch. A float input is read through its
    shortest repr, so a JSON number `299.1` converts as "299.1".

    Returns None, not 0, when there is no amount to read: zero is a real
    amount, and absence must not read as a balanced row. A caller whose
    upstream omits a figure to mean zero says so at its own call site.

    Precision finer than the currency's minor unit rounds half-up. No
    rail sends it for a currency this table knows the exponent of.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    scaled = amount * minor_unit_factor(currency)
    return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))
