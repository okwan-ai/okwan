"""Core money conversion — the one path from a decimal string to minor units.

Shopify and PayPal each carried their own copy, and they disagreed: one
truncated and read absence as zero, the other rounded half-up and read
absence as None. These tests pin the single core version both now use.
Pure functions over literal values; no transport.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from okwan_core import currency
from okwan_core.currency import minor_unit_factor, money_to_minor, to_major


@pytest.mark.parametrize(
    ("value", "code", "expected"),
    [
        ("299.10", "USD", 29910),   # the float path gives 29909.999...
        ("0.07", "USD", 7),
        ("1234.56", "USD", 123456),
        ("5000.00", "USD", 500000),
        ("5000", "USD", 500000),
        ("0.01", "USD", 1),
        ("-4.55", "USD", -455),     # refunds keep their sign
        ("1500", "KRW", 1500),      # zero-decimal: the integer is major
        ("1500.00", "JPY", 1500),
        ("5000", "XOF", 5000),
        ("5000", "xof", 5000),      # the code is case-insensitive
        (" 12.50 ", "USD", 1250),
        (Decimal("12.34"), "USD", 1234),
        (12, "USD", 1200),
    ],
)
def test_decimal_strings_convert_exactly(value, code, expected):
    assert money_to_minor(value, code) == expected


def test_a_float_converts_through_its_repr_not_its_binary_value():
    """A JSON number arrives as a float; 299.1 * 100 must still be 29910."""
    assert 299.1 * 100 != 29910
    assert money_to_minor(299.1, "USD") == 29910


@pytest.mark.parametrize("value", [None, "", "n/a", "NaN", "Infinity", "-inf", True])
def test_no_readable_amount_is_none_not_zero(value):
    """Zero is a real amount. Absence must not read as a balanced row."""
    assert money_to_minor(value, "USD") is None


def test_zero_is_an_amount():
    assert money_to_minor("0.00", "USD") == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.005", 1),
        ("0.0049", 0),
        ("-0.005", -1),   # half-up rounds away from zero on both sides
        ("1.235", 124),
    ],
)
def test_sub_minor_precision_rounds_half_up(value, expected):
    assert money_to_minor(value, "USD") == expected


def test_there_is_no_float_conversion_into_minor_units():
    """The float `to_minor` is gone; nothing should bring it back."""
    assert not hasattr(currency, "to_minor")


def test_the_factor_and_display_conversion_agree():
    assert minor_unit_factor("XOF") == 1
    assert minor_unit_factor("NGN") == 100
    assert minor_unit_factor(None) == 100
    assert to_major(money_to_minor("299.10", "USD"), "USD") == 299.10
    assert to_major(money_to_minor("5000", "XOF"), "XOF") == 5000
