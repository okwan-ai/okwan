"""The card guard: Okwan never stores a card number.

It runs on every file before anything is kept, in two parts.

**A full card number anywhere refuses the file.** Every cell, the header
row included, is searched for 12 to 19 digits, optionally separated by
single spaces or dashes. A run is a card number when its length and
leading digits belong to a card network *and* it passes the Luhn check.
Luhn alone passes one random number in ten, so ordinary references in a
bank or processor file would refuse it; the network rule is what keeps
the guard from crying wolf (§9 2026-11-06). The refusal names the row and
the column, never the value.

**Card columns are never stored.** A column whose header names a card
number (Card Number, PAN, CC #, …) can only be mapped to `card_last4`,
which keeps four digits of a masked value. Any column not mapped is
dropped, so an unmapped card column is never read past the search above.
"""
from __future__ import annotations

import re
from collections.abc import Iterable


class CardNumberFound(ValueError):
    """The file holds a full card number. Nothing from it is stored."""

    def __init__(self, where: str, column: str) -> None:
        self.where = where
        self.column = column
        super().__init__(
            f"{where}, column \"{column}\", holds what looks like a full card number. "
            "Okwan never stores card numbers: remove the column or mask it to the last "
            "four digits, then upload the file again. Nothing from this file was kept."
        )


#: (lowest prefix, highest prefix, lengths): issuer ranges for the card
#: networks a US merchant takes, Maestro's narrow ranges only.
_RANGES: tuple[tuple[str, str, frozenset[int]], ...] = (
    ("4", "4", frozenset({13, 16, 19})),                       # Visa
    ("51", "55", frozenset({16})),                             # Mastercard
    ("2221", "2720", frozenset({16})),                         # Mastercard 2-series
    ("34", "34", frozenset({15})), ("37", "37", frozenset({15})),  # American Express
    ("6011", "6011", frozenset(range(16, 20))),                # Discover
    ("644", "649", frozenset(range(16, 20))),
    ("65", "65", frozenset(range(16, 20))),
    ("622126", "622925", frozenset(range(16, 20))),
    ("300", "305", frozenset(range(14, 20))),                  # Diners Club
    ("36", "36", frozenset(range(14, 20))),
    ("38", "39", frozenset(range(16, 20))),
    ("3528", "3589", frozenset(range(16, 20))),                # JCB
    ("62", "62", frozenset(range(16, 20))),                    # UnionPay
    *((p, p, frozenset(range(12, 20)))                         # Maestro
      for p in ("5018", "5020", "5038", "5893", "6304", "6759", "6761", "6762", "6763")),
)


def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def is_card_number(digits: str) -> bool:
    """Digits only. A network's prefix and length, and the Luhn check."""
    n = len(digits)
    if not 12 <= n <= 19 or not digits.isdigit():
        return False
    for lo, hi, lengths in _RANGES:
        if n in lengths and lo <= digits[: len(lo)] <= hi:
            return luhn(digits)
    return False


_RUN = re.compile(r"(?<![0-9])[0-9](?:[ -]?[0-9]){11,}(?![0-9])")


def holds_card_number(text: str) -> bool:
    """Whether a cell holds a full card number, spaced or dashed or not.

    A run of digit groups is tried at every group boundary, so a card
    number followed by another number in the same cell is still found.
    """
    if len(text) < 12:
        return False
    for match in _RUN.finditer(text):
        groups = re.split(r"[ -]", match.group())
        for i in range(len(groups)):
            acc = ""
            for group in groups[i:]:
                acc += group
                if len(acc) > 19:
                    break
                if len(acc) >= 12 and is_card_number(acc):
                    return True
    return False


def _norm(header: str) -> str:
    return re.sub(r"[\s_.\-/]+", "", header.strip().lower())


_CARD_HEADER = re.compile(
    r"^(?:"
    r"(?:credit|debit|masked)?card(?:holder)?(?:number|num|nbr|no|#)"
    r"|creditcard|debitcard|maskedcard"
    r"|cc(?:number|num|nbr|no|#)?"
    r"|(?:masked)?pan|primaryaccountnumber|cardaccountnumber"
    r")$"
)


def is_card_column(header: str) -> bool:
    """A header naming a card number: Card Number, PAN, CC #, Masked Card…"""
    return bool(_CARD_HEADER.match(_norm(header)))


def last4(value: str) -> str | None:
    """The last four digits of a value, the only part of a card kept."""
    digits = re.sub(r"[^0-9]", "", value)
    return digits[-4:] if len(digits) >= 4 else None


def scan(rows: Iterable[list[str]], names: list[str], where) -> None:
    """Refuse the file if any cell holds a full card number.

    `rows` is everything read from the file, preamble and header included;
    `names` labels columns for the message and `where(i)` labels row i
    ("Row 14", "Transaction 3"). The message never carries the value.
    """
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            if cell and holds_card_number(cell):
                name = names[j].strip() if j < len(names) and names[j].strip() else f"column {j + 1}"
                raise CardNumberFound(where(i), name)
