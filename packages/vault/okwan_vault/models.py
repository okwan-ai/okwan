"""Vault records.

A credential is stored as ciphertext plus the wrapped data key that opens
it. Neither the plaintext nor the master key is ever written down.
"""
from __future__ import annotations

import base64
import calendar
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime


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


#: What an API key may do. A `read` key reads data through the REST
#: connector routes, SQL and the hosted MCP server; it cannot reach an
#: administration route (merchants, keys, credentials, imports, runs,
#: support). An `admin` key reaches both, within its tenant's subtree. A
#: key's scope is stated where it is made, never defaulted by the store.
READ = "read"
ADMIN = "admin"
KEY_SCOPES = (READ, ADMIN)


@dataclass(frozen=True, slots=True)
class ApiKey:
    """Only the hash is stored. A leaked database does not yield keys."""

    id: str
    tenant_id: str
    prefix: str
    hash_hex: str
    created_at: datetime = field(default_factory=_now)
    revoked_at: datetime | None = None
    scope: str = ADMIN

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
    #: The check read the account's imported files. Such a run holds
    #: references, amounts and dates taken from them, so it is deleted with
    #: any of them: when a file is deleted and when one expires.
    reads_files: bool = False


#: Runs kept per (tenant, kind, name): the newest RUNS_KEPT that finished
#: well and, apart from them, the newest RUNS_KEPT that failed, newest by
#: when each read; older ones are pruned on insert. Failed runs cannot push
#: a good one out of history, however many come in an outage. A run that
#: read imported files goes sooner, with the files.
RUNS_KEPT = 50

#: A support conversation is kept this long after its last message.
SUPPORT_KEPT_DAYS = 30

#: An imported file is kept this long after upload: a card chargeback
#: window and a year-over-year comparison (§9 2026-11-06).
IMPORT_KEPT_MONTHS = 13

#: Skipped row numbers kept on a file's record; the count is always whole.
SKIPPED_ROWS_KEPT = 200

#: Saved column mappings per tenant.
LAYOUTS_KEPT = 100


def add_months(at: datetime, months: int) -> datetime:
    """The same moment `months` later, on the month's last day if the day
    does not exist there (31 January + 1 month is 28 or 29 February)."""
    total = at.month - 1 + months
    year, month = at.year + total // 12, total % 12 + 1
    day = min(at.day, calendar.monthrange(year, month)[1])
    return at.replace(year=year, month=month, day=day)


class FilesGone(Exception):
    """A run read rows of an imported file that is no longer there. It is
    not stored: a stored result quotes its files and never outlives one."""


#: What a person can say about an open item. `open` puts back one that was
#: closed; `assigned` gives it to someone and leaves it open; `explained`
#: (nothing to fix: a deposit due Monday, a fee the processor kept) and
#: `resolved` (fixed where it started) close it.
DECISION_STATUSES = ("open", "assigned", "explained", "resolved")
CLOSED_STATUSES = frozenset({"explained", "resolved"})
#: Decisions one account can record on one item. A person decides an item a
#: few times; more is a script. Counted per account, so a merchant cannot
#: use up its platform's place on its own item, and an item's history,
#: bounded by the accounts that may decide it, is read whole.
ITEM_DECISIONS_MAX = 100


@dataclass(frozen=True, slots=True)
class Decision:
    """One thing a person decided about one finding of a check.

    Decisions are only ever added, never changed: an item's history is the
    record an accountant asks for, and its status is that of its newest
    decision. `seq` orders them; `prior_status` is the item's status before
    this one, None when nobody had decided anything yet. The item is the
    finding keyed by okwan_recon.exceptions in the tenant's check (`kind`,
    `name`), and `run_id` is the stored run it was found in when decided.
    `terms` is what it put at stake then (okwan_recon.exceptions.terms): a
    decision that closes an item closes it only while the item's terms are
    the same. `closed_before` says whether the decision before this one had
    closed it on these same terms.

    Who decided is the account the session or key belongs to
    (`actor_tenant_id`, its name then as `actor_name`) and the credential,
    as the access log names it: "session", or a key by its public prefix.
    Never a model: a read key, which an AI client is given, is refused.
    """

    id: str
    seq: int
    tenant_id: str
    kind: str
    name: str
    key: str
    status: str
    prior_status: str | None
    owner: str | None
    note: str | None
    actor_tenant_id: str
    actor_name: str
    credential: str
    run_id: str
    #: The check reads imported files: the note may quote them, so the
    #: decision goes with any of the tenant's files, as such a run does.
    reads_files: bool
    idempotency_key: str
    request_hash: str
    created_at: datetime
    terms: str = ""
    closed_before: bool = False
    #: The suggestion the person decided from (Suggestion), and how:
    #: `as_suggested` (explained, with its note word for word) or `edited`.
    #: The decision is the person's either way; the note is theirs.
    suggestion_id: str | None = None
    from_suggestion: str | None = None

    @property
    def closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    def closes(self, terms: str) -> bool:
        """Whether this decision closes an item that now has these terms."""
        return self.closed and self.terms == terms


#: What a suggestion proposes: `explained`, with the note a person would
#: record, or `none`: the records do not explain the item, and the note says
#: what to look at.
SUGGESTION_STATUSES = ("explained", "none")
#: How a person answered a suggestion, once: decided from it word for word,
#: decided from it with their own changes, or turned it down.
SUGGESTION_ANSWERS = ("as_suggested", "edited", "rejected")
#: Suggestions one account can ask for on one item. Each is a model call; a
#: person asks a few times at most.
ITEM_SUGGESTIONS_MAX = 20


@dataclass(frozen=True, slots=True)
class Suggestion:
    """What Okwan's model suggests one open item is, and the records it
    rests on. It is never a decision: it closes nothing, and only a person's
    decision that cites it (Decision.suggestion_id) acts on it.

    `evidence` is [{ref, why, record}]: the model chose which records and
    says why in `why`; `record` is the record as Okwan stored it when the
    suggestion was made, never the model's restatement. `terms` and
    `run_id` are the item's when the suggestion was made: a decision can
    cite it only while the item has the same terms. `answer` is set once,
    by the decision that cites it or by a person turning it down, with who
    did and when.
    """

    id: str
    tenant_id: str
    kind: str
    name: str
    key: str
    terms: str
    run_id: str
    reads_files: bool
    status: str
    note: str
    evidence: list
    confidence: str
    #: The model that answered, as the API named it.
    model: str
    actor_tenant_id: str
    actor_name: str
    credential: str
    created_at: datetime
    answer: str | None = None
    answered_by_tenant_id: str | None = None
    answered_by_name: str | None = None
    answered_credential: str | None = None
    answered_at: datetime | None = None
    #: Why a person turned it down, when they said.
    answer_note: str | None = None
    decision_id: str | None = None


class SuggestionAnswered(Exception):
    """The suggestion was already answered: decided from, or turned down."""


class SuggestionsFull(Exception):
    """The account has already asked for ITEM_SUGGESTIONS_MAX suggestions on the item."""


class DecisionConflict(Exception):
    """An Idempotency-Key already used for a different decision."""


class HistoryFull(Exception):
    """The account has already recorded ITEM_DECISIONS_MAX decisions on the item."""


class ItemBusy(Exception):
    """Another decision on the same item is being recorded. Nothing waits
    for it: the caller sends this one again."""


class ItemGone(Exception):
    """The stored run an item was decided against is no longer there: a
    file it read was deleted, or it was pruned, while the decision was
    being recorded. Nothing is stored."""


class DuplicateImport(Exception):
    """The same file, for the same kind, is already imported."""

    def __init__(self, existing_id: str | None) -> None:
        self.existing_id = existing_id
        super().__init__(f"this file is already imported as {existing_id}" if existing_id
                         else "this file is already imported")


@dataclass(frozen=True, slots=True)
class ImportFile:
    """One uploaded file: what it was, how it was mapped, what it covers.

    The file's bytes are never kept. Its rows hold only the mapped fields
    (okwan_files), and both expire at `expires_at`.
    """

    id: str
    tenant_id: str
    #: settlement_batches | settlement_transactions | bank_deposits | delivery_lines | delivery_payouts | tax_filings
    kind: str
    filename: str
    #: csv | xlsx | ofx
    format: str
    #: The mapping's name, recorded on every row it produced.
    layout: str
    mapping: dict
    sha256: str
    row_count: int
    skipped: int
    skipped_rows: list
    #: Card columns the file had. Never stored; at most four digits of a
    #: masked one, through card_last4.
    card_columns: list
    first_date: date | None
    last_date: date | None
    created_at: datetime
    expires_at: datetime


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


#: What the daily mail calls aged and large, before an account says: an
#: item open this many days (the dashboard's red), and one that puts this
#: much at stake, in whole units of its own currency (0: none is).
AGING_DAYS, LARGE_AMOUNT = 7, 1000
AGING_DAYS_RANGE, LARGE_AMOUNT_MAX = (1, 365), 10**12

#: An account's choices about the daily check, before it has made any.
NOTIFY_DEFAULTS = {"daily_checks": True, "finding_emails": True, "unsubscribe_token": None, "mailed_at": None,
                   "aging_days": AGING_DAYS, "large_amount": LARGE_AMOUNT}


@dataclass(frozen=True)
class DailyCheck:
    """What the daily check keeps about one merchant, and nothing else may
    write: when it last tried. What its mail has told the account is
    `mail_told`, about the open items whatever ran the checks."""

    tenant_id: str
    checked_at: datetime


#: `unsub_<tenant id>.<random>`: the tenant is named so the token is looked
#: up inside that tenant's scope, never across tenants.
UNSUBSCRIBE_MARK = "unsub_"


def new_unsubscribe_token(tenant_id: str) -> str:
    import secrets

    return f"{UNSUBSCRIBE_MARK}{tenant_id}.{secrets.token_urlsafe(24)}"


_UNSUBSCRIBE = re.compile(r"unsub_(ten_[0-9a-f]{16})\.[A-Za-z0-9_-]{32}")


def unsubscribe_tenant(token: str) -> str | None:
    """The tenant an unsubscribe token names, or None when the token is not
    exactly the shape issued: the id goes into a database scope, so nothing
    else is let through."""
    found = _UNSUBSCRIBE.fullmatch(token)
    return found.group(1) if found else None


# ── webhooks ─────────────────────────────────────────────────────────

#: Addresses one account may have Okwan send its events to.
WEBHOOK_ENDPOINTS_MAX = 5


@dataclass(frozen=True, slots=True)
class WebhookEndpoint:
    """An address an account has Okwan send its events to. Its secret is
    sealed in the store and opened only to sign; `hint` is its first
    characters, so a person can tell which one they hold. `failing_since`:
    every delivery since then has failed. `pending`: deliveries waiting."""

    id: str
    tenant_id: str
    url: str
    hint: str
    created_at: datetime
    created_by: str
    disabled_at: datetime | None = None
    disabled_why: str | None = None
    failing_since: datetime | None = None
    last_ok_at: datetime | None = None
    pending: int = 0


@dataclass(frozen=True, slots=True)
class WebhookClaim:
    """One delivery a round is sending: the event as signed, the address and
    the secret to sign it with. Held by that round until `claimed_until`."""

    endpoint_id: str
    event_id: str
    tenant_id: str
    url: str
    secret: str
    body: str
    attempts: int
    event_at: datetime


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """One event as it was sent, with what became of each delivery of it
    (address id, state, attempts, last status, when last tried)."""

    id: str
    tenant_id: str
    merchant_id: str
    type: str
    created_at: datetime
    body: str
    deliveries: tuple[dict, ...] = ()


class EndpointsFull(Exception):
    """The account already has WEBHOOK_ENDPOINTS_MAX addresses."""
