"""Vault records.

A credential is stored as ciphertext plus the wrapped data key that opens
it. Neither the plaintext nor the master key is ever written down.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Tenant:
    id: str
    name: str
    #: The ISV that provisioned this tenant, if any. None for a root
    #: account — a solo developer, or an ISV itself.
    parent_id: str | None = None
    created_at: datetime = field(default_factory=_now)

    @property
    def is_root(self) -> bool:
        return self.parent_id is None


@dataclass(frozen=True, slots=True)
class ApiKey:
    """Only the hash is stored. A leaked database does not yield keys."""

    id: str
    tenant_id: str
    prefix: str
    hash_hex: str
    created_at: datetime = field(default_factory=_now)
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True, slots=True)
class SealedCredential:
    tenant_id: str
    connector: str
    field_name: str
    ciphertext: bytes
    wrapped_key: bytes
    key_id: str
    created_at: datetime = field(default_factory=_now)

    @property
    def aad(self) -> bytes:
        """Binds this ciphertext to its tenant, connector and field."""
        return f"{self.tenant_id}|{self.connector}|{self.field_name}".encode()

    def to_row(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "connector": self.connector,
            "field_name": self.field_name,
            "ciphertext": base64.b64encode(self.ciphertext).decode(),
            "wrapped_key": base64.b64encode(self.wrapped_key).decode(),
            "key_id": self.key_id,
            "created_at": self.created_at.isoformat(),
        }
