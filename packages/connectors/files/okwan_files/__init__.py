"""Imported files: settlement reports and bank statements a tenant uploads.

`parse` reads CSV, XLSX and OFX; `guard` refuses any file holding a card
number; `mapping` turns rows into the three canonical kinds (`schemas`).
`prepare` is the one path from uploaded bytes to rows ready to store.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

from .guard import CardNumberFound
from .mapping import Mapping, MappingError, Normalized, apply, describe, when
from .parse import FORMATS, MAX_FILE_BYTES, MAX_ROWS, FileRefused, Sheet, read
from .schemas import KINDS


@dataclass(frozen=True, slots=True)
class Prepared:
    format: str
    sha256: str
    normalized: Normalized
    first_date: date | None
    last_date: date | None


def prepare(data: bytes, filename: str, mapping: Mapping) -> Prepared:
    """Uploaded bytes to rows ready to store, or a refusal.

    Raises FileRefused (unreadable), CardNumberFound (a card number
    anywhere) or MappingError (the mapping does not fit). The bytes are
    not kept by anything here; the caller drops them when this returns.
    """
    sheet = read(data, filename, header_row=mapping.header_row)
    normalized = apply(mapping, sheet)
    first, last = when(normalized.rows)
    return Prepared(format=sheet.format, sha256=hashlib.sha256(data).hexdigest(),
                    normalized=normalized, first_date=first, last_date=last)


__all__ = [
    "FORMATS", "KINDS", "MAX_FILE_BYTES", "MAX_ROWS", "CardNumberFound", "FileRefused",
    "Mapping", "MappingError", "Normalized", "Prepared", "Sheet", "apply", "describe",
    "prepare", "read",
]
