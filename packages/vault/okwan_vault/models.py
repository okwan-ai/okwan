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
class RunRecord:
    """One reconciliation run as a tenant, on any surface.

    `rows` holds the trimmed rows okwan_recon produces (what a page
    renders), never raw rail records; None when a listing left them out or
    the run failed. `error` is scrubbed of stored values and cut to 300
    characters.
    """

    id: str
    tenant_id: str
    #: across | pair
    kind: str
    name: str
    #: dashboard | rest | mcp
    surface: str
    #: ok | failed
    status: str
    started_at: datetime
    finished_at: datetime
    summary: dict | None = None
    rows: list | None = None
    error: str | None = None


#: Runs kept per (tenant, kind, name); older ones are pruned on insert.
RUNS_KEPT = 50

#: A support conversation is kept this long after its last message.
SUPPORT_KEPT_DAYS = 30


@dataclass(frozen=True, slots=True)
class SupportConversation:
    """One customer's conversation with the support assistant.

    `messages` is the transcript exactly as sent to and returned by the
    model, so it can be replayed unchanged: customer text after secret
    redaction, the assistant's content blocks, and the read-only tools'
    results. It is the tenant's own data and lives under row-level security.
    """

    id: str
    tenant_id: str
    messages: list
    turns: int
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


@dataclass(frozen=True, slots=True)
class SupportTicket:
    """A conversation the assistant handed to a person."""

    id: str
    tenant_id: str
    conversation_id: str | None
    #: sev1 | sev2 | sev3
    severity: str
    category: str
    summary: str
    #: Whether the escalation mail was accepted for delivery.
    notified: bool
    created_at: datetime = field(default_factory=_now)
    #: The assistant's brief: findings, likely_cause, resolution, draft_reply.
    brief: dict | None = None
    #: open | answered | resolved
    status: str = "open"
    resolved_at: datetime | None = None


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
