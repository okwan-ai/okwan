"""Minor-unit arithmetic — shared by connectors and the query layer.

Most payment APIs quote amounts in the currency's minor unit (kobo,
pesewas, cents). Zero-decimal currencies have no minor unit, so the
integer *is* the major amount. This table lives in core because both
connectors and the reconciliation layer depend on it; two copies would
drift and silently mis-match XOF settlements by a factor of 100.
"""
from __future__ import annotations

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


def to_minor(amount_major: float, currency: str | None) -> int:
    """Major amount -> integer minor units."""
    return int(round(float(amount_major) * minor_unit_factor(currency)))
