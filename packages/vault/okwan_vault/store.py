"""Credential storage.

The Store interface is what the gateway depends on; the in-memory
implementation is what tests and local development use. A Postgres-backed
implementation drops in behind the same interface without touching the
call sites.

Sealing and opening happen here rather than in the caller, so plaintext
credentials exist only inside a single function's stack frame.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from . import access as access_rules
from . import accounts, apikey
from .access import Seen
from .accounts import AccountRefused
from .crypto import new_key, open_sealed, seal
from .keys import MasterKeyProvider
from .models import (
    IMPORT_KEPT_MONTHS,
    KEY_SCOPES,
    LAYOUTS_KEPT,
    NOTIFY_DEFAULTS,
    RUNS_KEPT,
    SKIPPED_ROWS_KEPT,
    SUPPORT_KEPT_DAYS,
    ApiKey,
    DailyCheck,
    DuplicateImport,
    ImportFile,
    RunRecord,
    SealedCredential,
    SupportConversation,
    SupportTicket,
    Tenant,
    add_months,
    new_unsubscribe_token,
)
from .usage import (
    CHECKOUTS_LISTED,
    DEFAULT_PLAN,
    NOT_ROOT_PLAN,
    PLANS,
    UNPAID_CHECKOUT_KEPT,
    PlanRefused,
    hour_bucket,
)

#: A billing record before any checkout was opened for it.


class Store(Protocol):
    async def create_tenant(self, name: str, parent_id: str | None = None) -> Tenant: ...
    async def get_tenant(self, tenant_id: str) -> Tenant | None: ...
    async def children_of(self, tenant_id: str) -> list[Tenant]: ...
    async def issue_key(self, tenant_id: str, *, scope: str) -> tuple[str, ApiKey]: ...
    async def revoke_key(self, key_id: str) -> None: ...
    async def tenant_for_key(self, full_key: str) -> Tenant | None: ...
    async def access_for_key(self, full_key: str) -> tuple[Tenant, str] | None: ...
    async def put_credential(
        self, tenant_id: str, connector: str, field_name: str, value: str
    ) -> None: ...
    async def credentials_for(
        self, tenant_id: str, connector: str, fields: tuple[str, ...]
    ) -> dict[str, str]: ...
    async def credentials_many(
        self, tenant_id: str, wanted: dict[str, tuple[str, ...]]
    ) -> dict[str, dict[str, str]]: ...
    async def connectors_configured(self, tenant_id: str) -> dict[str, list[str]]: ...
    async def record_request(self, tenant_id: str, surface: str) -> None: ...
    async def usage_since(self, root_id: str, since) -> int: ...
    async def usage_buckets(
        self, root_id: str, since
    ) -> list[tuple[str, datetime, str, int]]: ...
    async def get_plan(self, tenant_id: str) -> tuple[str, int]: ...
    async def set_plan(self, tenant_id: str, name: str) -> None: ...
    async def key_owner(self, key_id: str) -> str | None: ...
    async def add_signup(
        self, email: str, password_hash: str, token_hash: str, expires_at: datetime
    ) -> None: ...
    async def signup_for(self, token_hash: str) -> tuple[str, str] | None: ...
    async def complete_signup(self, token_hash: str) -> Tenant | None: ...
    async def account_exists(self, email: str) -> bool: ...
    async def account_login(self, email: str) -> tuple[str, str] | None: ...
    async def add_account(self, tenant_id: str, email: str, password_hash: str) -> None: ...
    async def reset_password(self, email: str, password_hash: str) -> str: ...
    async def list_tenants(self) -> list[Tenant]: ...
    async def delete_tenant(self, tenant_id: str) -> list[str]: ...
    async def save_billing(
        self, tenant_id: str, *, customer_id: str, subscription_id: str, status: str,
        cancel_at_period_end: bool, current_period_end: datetime | None,
    ) -> None: ...
    async def billing_for(self, tenant_id: str) -> dict | None: ...
    async def open_checkout(self, tenant_id: str, session_id: str, *, policy_version: str,
                            at: datetime, ip: str) -> None: ...
    async def checkout_paid(self, tenant_id: str, session_id: str, *, at: datetime, subscription_id: str,
                            terms_accepted_at: datetime | None) -> bool: ...
    async def checkouts_for(self, tenant_id: str) -> list[dict]: ...
    async def tenant_for_billing_customer(self, customer_id: str) -> str | None: ...
    async def create_session(
        self, tenant_id: str, token_hash: str, expires_at: datetime
    ) -> None: ...
    async def tenant_for_session(self, token_hash: str) -> Tenant | None: ...
    async def delete_session(self, token_hash: str) -> None: ...
    async def add_run(
        self, tenant_id: str, *, kind: str, name: str, surface: str, status: str,
        started_at: datetime, finished_at: datetime, summary: dict | None,
        rows: list | None, error: str | None,
    ) -> RunRecord: ...
    async def list_runs(
        self, tenant_id: str, name: str | None = None, limit: int = 20
    ) -> list[RunRecord]: ...
    async def get_run(self, tenant_id: str, run_id: str) -> RunRecord | None: ...
    async def latest_runs(
        self, tenant_ids: list[str], kind: str, name: str
    ) -> dict[str, RunRecord]: ...
    async def keys_for(self, tenant_id: str) -> list[ApiKey]: ...
    async def get_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> SupportConversation | None: ...
    async def save_conversation(
        self, tenant_id: str, conversation_id: str | None, messages: list, turns: int
    ) -> str: ...
    async def add_ticket(
        self, tenant_id: str, *, conversation_id: str | None, severity: str,
        category: str, summary: str, notified: bool, ticket_id: str | None = None,
        brief: dict | None = None,
    ) -> SupportTicket: ...
    async def list_tickets(self, limit: int = 50, status: str | None = None) -> list[SupportTicket]: ...
    async def account_email(self, tenant_id: str) -> str | None: ...
    async def get_ticket(self, ticket_id: str) -> SupportTicket | None: ...
    async def set_ticket_status(self, tenant_id: str, ticket_id: str, status: str) -> None: ...
    async def add_reply(
        self, tenant_id: str, ticket_id: str, operator: str, body: str, sent: bool
    ) -> dict: ...
    async def replies_for(self, tenant_id: str, ticket_id: str) -> list[dict]: ...
    async def put_operator_factor(self, email: str, secret: str) -> None: ...
    async def operator_factor(self, email: str) -> str | None: ...
    async def set_session_operator(self, token_hash: str, until: datetime | None) -> None: ...
    async def session_operator_until(self, token_hash: str) -> datetime | None: ...
    async def log_operator(
        self, operator: str, action: str, target: str | None, detail: dict | None
    ) -> None: ...
    async def operator_log(self, limit: int = 100) -> list[dict]: ...
    async def add_import(
        self, tenant_id: str, *, kind: str, filename: str, format: str, layout: str,
        mapping: dict, sha256: str, rows: list[tuple[int, date, dict]], skipped: list[int],
        card_columns: list[str], first_date: date | None, last_date: date | None,
        now: datetime | None = None,
    ) -> ImportFile: ...
    async def list_imports(
        self, tenant_id: str, kind: str | None = None, now: datetime | None = None
    ) -> list[ImportFile]: ...
    async def get_import(
        self, tenant_id: str, file_id: str, now: datetime | None = None
    ) -> ImportFile | None: ...
    async def delete_import(self, tenant_id: str, file_id: str) -> bool: ...
    async def import_rows(
        self, tenant_id: str, kind: str, *, since: date | None = None,
        until: date | None = None, after: tuple[date, str, int] | None = None,
        limit: int = 500, now: datetime | None = None, direction: str | None = None,
    ) -> list[dict]: ...
    async def import_summary(
        self, tenant_id: str, now: datetime | None = None
    ) -> dict[str, dict]: ...
    async def save_layout(self, tenant_id: str, name: str, mapping: dict) -> dict: ...
    async def list_layouts(self, tenant_id: str) -> list[dict]: ...
    async def get_layout(self, tenant_id: str, name: str) -> dict | None: ...
    async def delete_layout(self, tenant_id: str, name: str) -> bool: ...
    async def sweep_imports(self, now: datetime | None = None) -> int: ...
    async def record_access(
        self, tenant_id: str, *, credential: str, ip: str, network: str, surface: str,
        target: str = "", at: datetime | None = None,
    ) -> Seen: ...
    async def access_entries(self, root_id: str, since: datetime, limit: int = 200) -> list[dict]: ...
    async def get_notify(self, tenant_id: str) -> dict: ...
    async def set_notify(
        self, tenant_id: str, *, daily_checks: bool | None = None, finding_emails: bool | None = None,
    ) -> dict: ...
    async def notify_token(self, tenant_id: str) -> str: ...
    async def notify_mailed(self, tenant_id: str, at: datetime | None) -> None: ...
    async def daily_checks(self, tenant_ids: list[str]) -> dict[str, DailyCheck]: ...
    async def claim_daily_check(self, tenant_id: str, at: datetime) -> None: ...
    async def settle_daily_check(
        self, tenant_id: str, *, told: list[str], news: list[dict],
    ) -> None: ...
    async def add_access_alert(
        self, tenant_id: str, *, kind: str, credential: str, detail: dict, at: datetime | None = None,
    ) -> dict: ...
    async def set_alert_mailed(self, tenant_id: str, alert_id: str) -> None: ...
    async def access_alerts(self, root_id: str, since: datetime) -> list[dict]: ...
    async def volume_spikes(
        self, now: datetime | None = None, *, floor: int = access_rules.VOLUME_FLOOR,
        factor: int = access_rules.VOLUME_FACTOR,
    ) -> list[dict]: ...
    async def sweep_access(self, now: datetime | None = None) -> int: ...


class MemoryStore:
    """Reference implementation. Not durable."""

    def __init__(self, master: MasterKeyProvider) -> None:
        self._master = master
        self._tenants: dict[str, Tenant] = {}
        self._keys: dict[str, ApiKey] = {}
        self._creds: dict[tuple[str, str, str], SealedCredential] = {}
        self._usage: dict[tuple, int] = {}
        self._plans: dict[str, str] = {}
        # token_hash → (email, password_hash, expires_at)
        self._signups: dict[str, tuple[str, str, datetime]] = {}
        # email → (tenant_id, password_hash)
        self._accounts: dict[str, tuple[str, str]] = {}
        # token_hash → (tenant_id, expires_at)
        self._sessions: dict[str, tuple[str, datetime]] = {}
        #: When each session was last used, for the idle limit.
        self._session_seen: dict[str, datetime] = {}
        self._billing: dict[str, dict] = {}
        self._checkouts: dict[str, dict] = {}
        self._runs: dict[str, RunRecord] = {}
        self._conversations: dict[str, SupportConversation] = {}
        self._tickets: dict[str, SupportTicket] = {}
        self._replies: list[dict] = []
        self._factors: dict[str, SealedCredential] = {}
        self._operator_until: dict[str, datetime] = {}
        self._operator_log: list[dict] = []
        self._imports: dict[str, ImportFile] = {}
        #: file id → [(row number, row date, mapped fields)]
        self._import_rows: dict[str, list[tuple[int, date, dict]]] = {}
        #: (tenant id, name) → saved mapping
        self._layouts: dict[tuple[str, str], dict] = {}
        #: Access rows, oldest first, and the alerts they raised, by id.
        self._access: list[dict] = []
        self._alerts: dict[str, dict] = {}
        self._notify: dict[str, dict] = {}
        self._daily: dict[str, DailyCheck] = {}

    async def create_tenant(self, name: str, parent_id: str | None = None) -> Tenant:
        if parent_id is not None and parent_id not in self._tenants:
            raise KeyError(f"unknown parent tenant {parent_id!r}")
        tenant = Tenant(
            id=f"ten_{uuid.uuid4().hex[:16]}", name=name, parent_id=parent_id
        )
        self._tenants[tenant.id] = tenant
        return tenant

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    async def children_of(self, tenant_id: str) -> list[Tenant]:
        return [t for t in self._tenants.values() if t.parent_id == tenant_id]

    async def list_tenants(self) -> list[Tenant]:
        return list(self._tenants.values())

    async def delete_tenant(self, tenant_id: str) -> list[str]:
        """Delete a tenant, every tenant under it and everything they hold.
        The ids deleted; empty when there was no such tenant."""
        if tenant_id not in self._tenants:
            return []
        gone = self._subtree(tenant_id)
        emails = {e for e, (t, _) in self._accounts.items() if t in gone}
        self._tenants = {k: v for k, v in self._tenants.items() if k not in gone}
        self._keys = {k: v for k, v in self._keys.items() if v.tenant_id not in gone}
        self._creds = {k: v for k, v in self._creds.items() if k[0] not in gone}
        self._usage = {k: v for k, v in self._usage.items() if k[0] not in gone}
        self._plans = {k: v for k, v in self._plans.items() if k not in gone}
        self._signups = {k: v for k, v in self._signups.items() if v[0] not in emails}
        self._accounts = {k: v for k, v in self._accounts.items() if v[0] not in gone}
        self._sessions = {k: v for k, v in self._sessions.items() if v[0] not in gone}
        self._runs = {k: v for k, v in self._runs.items() if v.tenant_id not in gone}
        self._conversations = {k: v for k, v in self._conversations.items() if v.tenant_id not in gone}
        self._tickets = {k: v for k, v in self._tickets.items() if v.tenant_id not in gone}
        self._replies = [r for r in self._replies if r["tenant_id"] not in gone]
        self._billing = {k: v for k, v in self._billing.items() if k not in gone}
        self._checkouts = {k: v for k, v in self._checkouts.items() if v["tenant_id"] not in gone}
        for file_id in [k for k, v in self._imports.items() if v.tenant_id in gone]:
            del self._imports[file_id]
            self._import_rows.pop(file_id, None)
        self._layouts = {k: v for k, v in self._layouts.items() if k[0] not in gone}
        self._access = [r for r in self._access if r["tenant_id"] not in gone]
        self._alerts = {k: v for k, v in self._alerts.items() if v["tenant_id"] not in gone}
        self._notify = {k: v for k, v in self._notify.items() if k not in gone}
        self._daily = {k: v for k, v in self._daily.items() if k not in gone}
        return sorted(gone)

    async def save_billing(
        self, tenant_id: str, *, customer_id: str, subscription_id: str, status: str,
        cancel_at_period_end: bool, current_period_end: datetime | None,
    ) -> None:
        """Record or update a paying account's Stripe link; an empty id keeps
        the one already known."""
        if tenant_id not in self._tenants:
            raise ValueError(f"no such tenant: {tenant_id}")
        old = self._billing.get(tenant_id, {})
        self._billing[tenant_id] = {
            "tenant_id": tenant_id, "customer_id": customer_id or old.get("customer_id") or None,
            "subscription_id": subscription_id or old.get("subscription_id") or None,
            "status": status, "cancel_at_period_end": cancel_at_period_end,
            "current_period_end": current_period_end or old.get("current_period_end"),
            "updated_at": datetime.now(UTC),
        }

    async def billing_for(self, tenant_id: str) -> dict | None:
        found = self._billing.get(tenant_id)
        return dict(found) if found else None

    async def open_checkout(self, tenant_id: str, session_id: str, *, policy_version: str,
                            at: datetime, ip: str) -> None:
        if tenant_id not in self._tenants:
            raise ValueError(f"no such tenant: {tenant_id}")
        self._checkouts = {k: v for k, v in self._checkouts.items() if not (
            v["tenant_id"] == tenant_id and v["paid_at"] is None and v["opened_at"] < at - UNPAID_CHECKOUT_KEPT)}
        self._checkouts.setdefault(session_id, {
            "session_id": session_id, "tenant_id": tenant_id, "policy_version": policy_version,
            "opened_at": at, "ip": ip, "paid_at": None, "subscription_id": None, "terms_accepted_at": None})

    async def checkout_paid(self, tenant_id: str, session_id: str, *, at: datetime, subscription_id: str,
                            terms_accepted_at: datetime | None) -> bool:
        row = self._checkouts.get(session_id)
        if row is None or row["tenant_id"] != tenant_id:
            return False
        if row["paid_at"] is None:
            self._checkouts[session_id] = {**row, "paid_at": at, "subscription_id": subscription_id,
                                           "terms_accepted_at": terms_accepted_at}
        return True

    async def checkouts_for(self, tenant_id: str) -> list[dict]:
        rows = [dict(v) for v in self._checkouts.values() if v["tenant_id"] == tenant_id]
        rows.sort(key=lambda r: r["session_id"])
        rows.sort(key=lambda r: r["opened_at"], reverse=True)
        return rows[:CHECKOUTS_LISTED]

    async def tenant_for_billing_customer(self, customer_id: str) -> str | None:
        return next((t for t, b in self._billing.items() if b["customer_id"] == customer_id), None)

    async def issue_key(self, tenant_id: str, *, scope: str) -> tuple[str, ApiKey]:
        if scope not in KEY_SCOPES:
            raise ValueError(f"a key's scope is one of {KEY_SCOPES}, not {scope!r}")
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        full, prefix, hash_hex = apikey.generate()
        record = ApiKey(
            id=f"key_{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            prefix=prefix,
            hash_hex=hash_hex,
            scope=scope,
        )
        self._keys[record.id] = record
        return full, record

    async def revoke_key(self, key_id: str) -> None:
        from datetime import datetime

        record = self._keys.get(key_id)
        if record is None:
            raise KeyError(f"unknown key {key_id!r}")
        self._keys[key_id] = replace(record, revoked_at=datetime.now(UTC))

    async def tenant_for_key(self, full_key: str) -> Tenant | None:
        found = await self.access_for_key(full_key)
        return found[0] if found else None

    async def access_for_key(self, full_key: str) -> tuple[Tenant, str] | None:
        """The key's tenant and what the key may do, or None."""
        for record in self._keys.values():
            if record.is_active and apikey.matches(full_key, record.hash_hex):
                tenant = self._tenants.get(record.tenant_id)
                return (tenant, record.scope) if tenant else None
        return None

    async def put_credential(
        self, tenant_id: str, connector: str, field_name: str, value: str
    ) -> None:
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        data_key = new_key()
        record = SealedCredential(
            tenant_id=tenant_id,
            connector=connector,
            field_name=field_name,
            ciphertext=b"",
            wrapped_key=self._master.wrap(data_key),
            key_id=self._master.key_id,
        )
        sealed = seal(data_key, value.encode(), aad=record.aad)
        self._creds[(tenant_id, connector, field_name)] = SealedCredential(
            tenant_id=tenant_id,
            connector=connector,
            field_name=field_name,
            ciphertext=sealed,
            wrapped_key=record.wrapped_key,
            key_id=record.key_id,
        )

    async def credentials_for(
        self, tenant_id: str, connector: str, fields: tuple[str, ...]
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in fields:
            record = self._creds.get((tenant_id, connector, name))
            if record is None:
                out[name] = ""
                continue
            data_key = self._master.unwrap(record.wrapped_key)
            out[name] = open_sealed(
                data_key, record.ciphertext, aad=record.aad
            ).decode()
        return out

    async def credentials_many(
        self, tenant_id: str, wanted: dict[str, tuple[str, ...]]
    ) -> dict[str, dict[str, str]]:
        return {c: await self.credentials_for(tenant_id, c, f) for c, f in wanted.items()}

    async def record_request(self, tenant_id: str, surface: str) -> None:
        key = (tenant_id, hour_bucket(), surface)
        self._usage[key] = self._usage.get(key, 0) + 1

    def _subtree(self, root_id: str) -> set[str]:
        subtree = {root_id}
        changed = True
        while changed:
            changed = False
            for t in self._tenants.values():
                if t.parent_id in subtree and t.id not in subtree:
                    subtree.add(t.id)
                    changed = True
        return subtree

    async def usage_since(self, root_id: str, since) -> int:
        subtree = self._subtree(root_id)
        return sum(
            n for (tid, hour, _), n in self._usage.items()
            if tid in subtree and hour >= since
        )

    async def usage_buckets(
        self, root_id: str, since
    ) -> list[tuple[str, datetime, str, int]]:
        subtree = self._subtree(root_id)
        return sorted(
            (tid, hour, surface, n)
            for (tid, hour, surface), n in self._usage.items()
            if tid in subtree and hour >= since
        )

    async def get_plan(self, tenant_id: str) -> tuple[str, int]:
        name = self._plans.get(tenant_id, DEFAULT_PLAN)
        return name, PLANS[name]

    # ── reconciliation runs ─────────────────────────────────────────

    async def add_run(
        self, tenant_id: str, *, kind: str, name: str, surface: str, status: str,
        started_at: datetime, finished_at: datetime, summary: dict | None,
        rows: list | None, error: str | None,
    ) -> RunRecord:
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        rec = RunRecord(
            id=f"run_{uuid.uuid4().hex[:16]}", tenant_id=tenant_id, kind=kind, name=name,
            surface=surface, status=status, started_at=started_at, finished_at=finished_at,
            summary=summary, rows=rows, error=error,
        )
        self._runs[rec.id] = rec
        # Prune with the insert: the newest RUNS_KEPT per (tenant, kind, name).
        same = sorted(
            (r for r in self._runs.values()
             if r.tenant_id == tenant_id and r.kind == kind and r.name == name),
            key=lambda r: (r.finished_at, r.id), reverse=True,
        )
        for old in same[RUNS_KEPT:]:
            del self._runs[old.id]
        return rec

    async def list_runs(
        self, tenant_id: str, name: str | None = None, limit: int = 20
    ) -> list[RunRecord]:
        found = [
            r for r in self._runs.values()
            if r.tenant_id == tenant_id and (name is None or r.name == name)
        ]
        found.sort(key=lambda r: (r.finished_at, r.id), reverse=True)
        # A listing never carries rows; get_run does.
        return [replace(r, rows=None) for r in found[:limit]]

    async def get_run(self, tenant_id: str, run_id: str) -> RunRecord | None:
        r = self._runs.get(run_id)
        return r if r is not None and r.tenant_id == tenant_id else None

    async def latest_runs(
        self, tenant_ids: list[str], kind: str, name: str
    ) -> dict[str, RunRecord]:
        wanted = set(tenant_ids)
        out: dict[str, RunRecord] = {}
        for r in self._runs.values():
            if r.tenant_id not in wanted or r.kind != kind or r.name != name:
                continue
            cur = out.get(r.tenant_id)
            if cur is None or (r.finished_at, r.id) > (cur.finished_at, cur.id):
                out[r.tenant_id] = r
        return out

    async def set_plan(self, tenant_id: str, name: str) -> None:
        if name not in PLANS:
            raise PlanRefused(f"unknown plan {name!r}; known: {', '.join(PLANS)}")
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise PlanRefused(f"no such tenant: {tenant_id}")
        if not tenant.is_root:
            raise PlanRefused(f"{tenant_id} is not a root tenant; {NOT_ROOT_PLAN}")
        self._plans[tenant_id] = name

    async def connectors_configured(self, tenant_id: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for (tid, connector, field_name) in self._creds:
            if tid == tenant_id:
                out.setdefault(connector, []).append(field_name)
        return out

    async def key_owner(self, key_id: str) -> str | None:
        record = self._keys.get(key_id)
        return None if record is None else record.tenant_id

    # ── accounts ────────────────────────────────────────────────────

    async def add_signup(
        self, email: str, password_hash: str, token_hash: str, expires_at: datetime
    ) -> None:
        self._signups[token_hash] = (email, password_hash, expires_at)

    async def signup_for(self, token_hash: str) -> tuple[str, str] | None:
        found = self._signups.get(token_hash)
        if found is None or found[2] <= datetime.now(UTC):
            return None
        return found[0], found[1]

    async def complete_signup(self, token_hash: str) -> Tenant | None:
        found = await self.signup_for(token_hash)
        self._signups.pop(token_hash, None)
        if found is None:
            return None
        email, password_hash = found
        if email in self._accounts:
            return None
        tenant = await self.create_tenant(email)
        self._accounts[email] = (tenant.id, password_hash)
        for other in [h for h, s in self._signups.items() if s[0] == email]:
            del self._signups[other]
        return tenant

    async def account_exists(self, email: str) -> bool:
        return email in self._accounts

    async def account_login(self, email: str) -> tuple[str, str] | None:
        return self._accounts.get(email)

    async def add_account(self, tenant_id: str, email: str, password_hash: str) -> None:
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise AccountRefused(f"no such tenant: {tenant_id}")
        if not tenant.is_root:
            raise AccountRefused(f"{tenant_id} is not a root tenant")
        if any(t == tenant_id for t, _ in self._accounts.values()):
            raise AccountRefused(f"{tenant_id} already has an account")
        if email in self._accounts:
            raise AccountRefused(f"{email} is already taken")
        self._accounts[email] = (tenant_id, password_hash)

    async def reset_password(self, email: str, password_hash: str) -> str:
        found = self._accounts.get(email)
        if found is None:
            raise AccountRefused(f"no account for {email}")
        tenant_id = found[0]
        self._accounts[email] = (tenant_id, password_hash)
        for token_hash in [h for h, (t, _) in self._sessions.items() if t == tenant_id]:
            del self._sessions[token_hash]
        return tenant_id

    async def create_session(
        self, tenant_id: str, token_hash: str, expires_at: datetime
    ) -> None:
        self._sessions[token_hash] = (tenant_id, expires_at)
        self._session_seen[token_hash] = datetime.now(UTC)

    async def tenant_for_session(self, token_hash: str) -> Tenant | None:
        """Live before its absolute expiry and while used within SESSION_IDLE;
        using it moves the idle clock (at most once per SESSION_TOUCH)."""
        found = self._sessions.get(token_hash)
        now = datetime.now(UTC)
        seen = self._session_seen.get(token_hash, now)
        if found is None or found[1] <= now or seen <= now - accounts.SESSION_IDLE:
            return None
        if seen < now - accounts.SESSION_TOUCH:
            self._session_seen[token_hash] = now
        return self._tenants.get(found[0])

    async def delete_session(self, token_hash: str) -> None:
        self._sessions.pop(token_hash, None)
        self._session_seen.pop(token_hash, None)


    # ── support ─────────────────────────────────────────────────────

    async def keys_for(self, tenant_id: str) -> list[ApiKey]:
        """A tenant's keys, newest first. The hash stays in the record but no
        caller of this returns it."""
        found = [k for k in self._keys.values() if k.tenant_id == tenant_id]
        return sorted(found, key=lambda k: k.created_at, reverse=True)

    async def get_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> SupportConversation | None:
        c = self._conversations.get(conversation_id)
        return c if c is not None and c.tenant_id == tenant_id else None

    async def save_conversation(
        self, tenant_id: str, conversation_id: str | None, messages: list, turns: int
    ) -> str:
        """Insert or replace one conversation, then drop the tenant's
        conversations untouched for SUPPORT_KEPT_DAYS."""
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        now = datetime.now(UTC)
        old = self._conversations.get(conversation_id) if conversation_id else None
        if old is not None and old.tenant_id != tenant_id:
            raise KeyError(f"unknown conversation {conversation_id!r}")
        cid = conversation_id or f"sup_{uuid.uuid4().hex[:16]}"
        self._conversations[cid] = SupportConversation(
            id=cid, tenant_id=tenant_id, messages=list(messages), turns=turns,
            created_at=old.created_at if old else now, updated_at=now,
        )
        cutoff = now - timedelta(days=SUPPORT_KEPT_DAYS)
        for key, c in list(self._conversations.items()):
            if c.tenant_id == tenant_id and c.updated_at < cutoff:
                del self._conversations[key]
        return cid

    async def add_ticket(
        self, tenant_id: str, *, conversation_id: str | None, severity: str,
        category: str, summary: str, notified: bool, ticket_id: str | None = None,
        brief: dict | None = None,
    ) -> SupportTicket:
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        t = SupportTicket(
            id=ticket_id or f"tkt_{uuid.uuid4().hex[:16]}", tenant_id=tenant_id,
            conversation_id=conversation_id, severity=severity, category=category,
            summary=summary, notified=notified, brief=dict(brief) if brief else None,
        )
        self._tickets[t.id] = t
        return t

    async def list_tickets(self, limit: int = 50, status: str | None = None) -> list[SupportTicket]:
        """Every tenant's tickets, newest first: for the operator only."""
        found = [t for t in self._tickets.values() if status is None or t.status == status]
        return sorted(found, key=lambda t: t.created_at, reverse=True)[:limit]

    async def get_ticket(self, ticket_id: str) -> SupportTicket | None:
        """One ticket from any tenant: for the operator only."""
        return self._tickets.get(ticket_id)

    async def set_ticket_status(self, tenant_id: str, ticket_id: str, status: str) -> None:
        t = self._tickets.get(ticket_id)
        if t is None or t.tenant_id != tenant_id:
            raise KeyError(f"unknown ticket {ticket_id!r}")
        resolved = datetime.now(UTC) if status == "resolved" else None
        self._tickets[ticket_id] = replace(t, status=status, resolved_at=resolved)

    async def add_reply(
        self, tenant_id: str, ticket_id: str, operator: str, body: str, sent: bool
    ) -> dict:
        reply = {"id": f"rpl_{uuid.uuid4().hex[:16]}", "tenant_id": tenant_id,
                 "ticket_id": ticket_id, "operator": operator, "body": body, "sent": sent,
                 "created_at": datetime.now(UTC)}
        self._replies.append(reply)
        return dict(reply)

    async def replies_for(self, tenant_id: str, ticket_id: str) -> list[dict]:
        return [dict(r) for r in self._replies
                if r["tenant_id"] == tenant_id and r["ticket_id"] == ticket_id]

    async def put_operator_factor(self, email: str, secret: str) -> None:
        data_key = new_key()
        record = SealedCredential(tenant_id="operator", connector="totp", field_name=email,
                                  ciphertext=b"", wrapped_key=b"", key_id=self._master.key_id)
        self._factors[email] = SealedCredential(
            tenant_id="operator", connector="totp", field_name=email,
            ciphertext=seal(data_key, secret.encode(), aad=record.aad),
            wrapped_key=self._master.wrap(data_key), key_id=self._master.key_id,
        )

    async def operator_factor(self, email: str) -> str | None:
        record = self._factors.get(email)
        if record is None:
            return None
        data_key = self._master.unwrap(record.wrapped_key)
        return open_sealed(data_key, record.ciphertext, aad=record.aad).decode()

    async def set_session_operator(self, token_hash: str, until: datetime | None) -> None:
        if until is None:
            self._operator_until.pop(token_hash, None)
        elif token_hash in self._sessions:
            self._operator_until[token_hash] = until

    async def session_operator_until(self, token_hash: str) -> datetime | None:
        if token_hash not in self._sessions:
            return None
        return self._operator_until.get(token_hash)

    async def log_operator(
        self, operator: str, action: str, target: str | None, detail: dict | None
    ) -> None:
        self._operator_log.append({"at": datetime.now(UTC), "operator": operator,
                                   "action": action, "target": target, "detail": detail})

    async def operator_log(self, limit: int = 100) -> list[dict]:
        return [dict(e) for e in reversed(self._operator_log)][:limit]

    async def account_email(self, tenant_id: str) -> str | None:
        """The sign-in address of the tenant's login, or of the nearest
        ancestor's: a merchant has no login, its account does."""
        logins = {tid: email for email, (tid, _) in self._accounts.items()}
        t = self._tenants.get(tenant_id)
        while t is not None:
            if t.id in logins:
                return logins[t.id]
            t = self._tenants.get(t.parent_id) if t.parent_id else None
        return None


    # ── imported files ──────────────────────────────────────────────

    async def add_import(
        self, tenant_id: str, *, kind: str, filename: str, format: str, layout: str,
        mapping: dict, sha256: str, rows: list[tuple[int, date, dict]], skipped: list[int],
        card_columns: list[str], first_date: date | None, last_date: date | None,
        now: datetime | None = None,
    ) -> ImportFile:
        """Store a mapped file's rows. The tenant's expired files go first,
        so an upload never sits next to data it has outlived."""
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        now = now or datetime.now(UTC)
        for old in [f for f in self._imports.values()
                    if f.tenant_id == tenant_id and f.expires_at <= now]:
            del self._imports[old.id]
            self._import_rows.pop(old.id, None)
        same = next((f for f in self._imports.values() if f.tenant_id == tenant_id
                     and f.kind == kind and f.sha256 == sha256), None)
        if same is not None:
            raise DuplicateImport(same.id)
        rec = ImportFile(
            id=f"imp_{uuid.uuid4().hex[:16]}", tenant_id=tenant_id, kind=kind,
            filename=filename, format=format, layout=layout, mapping=dict(mapping),
            sha256=sha256, row_count=len(rows), skipped=len(skipped),
            skipped_rows=list(skipped[:SKIPPED_ROWS_KEPT]), card_columns=list(card_columns),
            first_date=first_date, last_date=last_date, created_at=now,
            expires_at=add_months(now, IMPORT_KEPT_MONTHS),
        )
        self._imports[rec.id] = rec
        self._import_rows[rec.id] = [(n, d, dict(data)) for n, d, data in rows]
        return rec

    def _live_imports(self, tenant_id: str, now: datetime | None) -> list[ImportFile]:
        now = now or datetime.now(UTC)
        return [f for f in self._imports.values() if f.tenant_id == tenant_id and f.expires_at > now]

    async def list_imports(
        self, tenant_id: str, kind: str | None = None, now: datetime | None = None
    ) -> list[ImportFile]:
        found = [f for f in self._live_imports(tenant_id, now) if kind is None or f.kind == kind]
        return sorted(found, key=lambda f: (f.created_at, f.id), reverse=True)

    async def get_import(
        self, tenant_id: str, file_id: str, now: datetime | None = None
    ) -> ImportFile | None:
        return next((f for f in self._live_imports(tenant_id, now) if f.id == file_id), None)

    async def delete_import(self, tenant_id: str, file_id: str) -> bool:
        f = self._imports.get(file_id)
        if f is None or f.tenant_id != tenant_id:
            return False
        del self._imports[file_id]
        self._import_rows.pop(file_id, None)
        return True

    async def import_rows(
        self, tenant_id: str, kind: str, *, since: date | None = None,
        until: date | None = None, after: tuple[date, str, int] | None = None,
        limit: int = 500, now: datetime | None = None, direction: str | None = None,
    ) -> list[dict]:
        """Mapped rows of one kind, oldest first, from files not expired.
        `after` is the (row date, file id, row) of the last row already read;
        `direction` "in" or "out" keeps rows whose amount is above or below 0."""
        out = []
        for f in self._live_imports(tenant_id, now):
            if f.kind != kind:
                continue
            for n, d, data in self._import_rows.get(f.id, []):
                key = (d, f.id, n)
                if (since and d < since) or (until and d > until) or (after and key <= after):
                    continue
                amount = data.get("amount_minor") or 0
                if (direction == "in" and amount <= 0) or (direction == "out" and amount >= 0):
                    continue
                out.append((key, {**data, "file_id": f.id, "row": n, "layout": f.layout}))
        out.sort(key=lambda kv: kv[0])
        return [row for _, row in out[:limit]]

    async def import_summary(
        self, tenant_id: str, now: datetime | None = None
    ) -> dict[str, dict]:
        """Per kind, what the tenant's live files cover: how many files and
        rows, and the first and last day of them."""
        out: dict[str, dict] = {}
        for f in self._live_imports(tenant_id, now):
            k = out.setdefault(f.kind, {"files": 0, "rows": 0, "first_date": None, "last_date": None})
            k["files"] += 1
            k["rows"] += f.row_count
            if f.first_date and (k["first_date"] is None or f.first_date < k["first_date"]):
                k["first_date"] = f.first_date
            if f.last_date and (k["last_date"] is None or f.last_date > k["last_date"]):
                k["last_date"] = f.last_date
        return out

    async def save_layout(self, tenant_id: str, name: str, mapping: dict) -> dict:
        """Create or replace a saved mapping by name."""
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        others = [k for k in self._layouts if k[0] == tenant_id and k[1] != name]
        if len(others) >= LAYOUTS_KEPT:
            raise ValueError(f"at most {LAYOUTS_KEPT} saved mappings; delete one first")
        now = datetime.now(UTC)
        old = self._layouts.get((tenant_id, name))
        rec = {"name": name, "kind": mapping["kind"], "mapping": dict(mapping),
               "created_at": old["created_at"] if old else now, "updated_at": now}
        self._layouts[(tenant_id, name)] = rec
        return dict(rec)

    async def list_layouts(self, tenant_id: str) -> list[dict]:
        return [dict(v) for k, v in sorted(self._layouts.items()) if k[0] == tenant_id]

    async def get_layout(self, tenant_id: str, name: str) -> dict | None:
        found = self._layouts.get((tenant_id, name))
        return dict(found) if found else None

    async def delete_layout(self, tenant_id: str, name: str) -> bool:
        return self._layouts.pop((tenant_id, name), None) is not None

    async def sweep_imports(self, now: datetime | None = None) -> int:
        """Delete every tenant's expired files and their rows."""
        now = now or datetime.now(UTC)
        gone = [k for k, f in self._imports.items() if f.expires_at <= now]
        for file_id in gone:
            del self._imports[file_id]
            self._import_rows.pop(file_id, None)
        return len(gone)

    # ── access log ──────────────────────────────────────────────────

    async def record_access(
        self, tenant_id: str, *, credential: str, ip: str, network: str, surface: str,
        target: str = "", at: datetime | None = None,
    ) -> Seen:
        at = at or datetime.now(UTC)
        mine = [r for r in self._access if r["tenant_id"] == tenant_id and r["credential"] == credential]
        seen = Seen(
            known_network=any(r["network"] == network and r["at"] > at - access_rules.NETWORK_MEMORY
                              for r in mine),
            first_seen=min((r["at"] for r in mine), default=None),
        )
        self._access.append({"tenant_id": tenant_id, "at": at, "credential": credential, "ip": ip,
                             "network": network, "surface": surface, "target": target})
        return seen

    async def access_entries(self, root_id: str, since: datetime, limit: int = 200) -> list[dict]:
        subtree = self._subtree(root_id)
        rows = [dict(r) for r in self._access if r["tenant_id"] in subtree and r["at"] >= since]
        return sorted(rows, key=lambda r: r["at"], reverse=True)[:limit]

    async def add_access_alert(
        self, tenant_id: str, *, kind: str, credential: str, detail: dict, at: datetime | None = None,
    ) -> dict:
        alert = {"id": f"alr_{uuid.uuid4().hex[:16]}", "tenant_id": tenant_id,
                 "at": at or datetime.now(UTC), "kind": kind, "credential": credential,
                 "detail": dict(detail), "mailed": False}
        self._alerts[alert["id"]] = alert
        return dict(alert)

    async def set_alert_mailed(self, tenant_id: str, alert_id: str) -> None:
        alert = self._alerts.get(alert_id)
        if alert is not None and alert["tenant_id"] == tenant_id:
            alert["mailed"] = True

    async def access_alerts(self, root_id: str, since: datetime) -> list[dict]:
        subtree = self._subtree(root_id)
        rows = [dict(a) for a in self._alerts.values() if a["tenant_id"] in subtree and a["at"] >= since]
        return sorted(rows, key=lambda a: a["at"], reverse=True)

    async def volume_spikes(
        self, now: datetime | None = None, *, floor: int = access_rules.VOLUME_FLOOR,
        factor: int = access_rules.VOLUME_FACTOR,
    ) -> list[dict]:
        now = now or datetime.now(UTC)
        hour = timedelta(hours=1)
        recent: dict[tuple[str, str], int] = {}
        hours: dict[tuple[str, str, datetime], int] = {}
        for r in self._access:
            if not access_rules.is_key(r["credential"]):
                continue
            pair = (r["tenant_id"], r["credential"])
            if now - hour < r["at"] <= now:
                recent[pair] = recent.get(pair, 0) + 1
            elif now - hour - access_rules.VOLUME_WEEK < r["at"] <= now - hour:
                bucket = (*pair, r["at"].replace(minute=0, second=0, microsecond=0))
                hours[bucket] = hours.get(bucket, 0) + 1
        quiet = {(a["tenant_id"], a["credential"]) for a in self._alerts.values()
                 if a["kind"] == "volume" and a["at"] > now - access_rules.VOLUME_QUIET}
        out = []
        for (tenant_id, credential), n in sorted(recent.items()):
            busiest = max((c for (t, k, _), c in hours.items() if (t, k) == (tenant_id, credential)),
                          default=0)
            if n >= floor and n > factor * busiest and (tenant_id, credential) not in quiet:
                out.append({"tenant_id": tenant_id, "credential": credential,
                            "requests": n, "busiest": busiest})
        return out

    # ── the daily check's settings ────────────────────────────────────

    async def get_notify(self, tenant_id: str) -> dict:
        return dict(self._notify.get(tenant_id, NOTIFY_DEFAULTS))

    async def set_notify(
        self, tenant_id: str, *, daily_checks: bool | None = None, finding_emails: bool | None = None,
    ) -> dict:
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        row = dict(self._notify.get(tenant_id, NOTIFY_DEFAULTS))
        if daily_checks is not None:
            row["daily_checks"] = daily_checks
        if finding_emails is not None:
            row["finding_emails"] = finding_emails
        self._notify[tenant_id] = row
        return dict(row)

    async def notify_token(self, tenant_id: str) -> str:
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        row = dict(self._notify.get(tenant_id, NOTIFY_DEFAULTS))
        row["unsubscribe_token"] = row["unsubscribe_token"] or new_unsubscribe_token(tenant_id)
        self._notify[tenant_id] = row
        return row["unsubscribe_token"]

    async def notify_mailed(self, tenant_id: str, at: datetime | None) -> None:
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        self._notify[tenant_id] = {**self._notify.get(tenant_id, NOTIFY_DEFAULTS), "mailed_at": at}

    # ── the daily check's own record ──────────────────────────────────

    async def daily_checks(self, tenant_ids: list[str]) -> dict[str, DailyCheck]:
        return {t: self._daily[t] for t in tenant_ids if t in self._daily}

    async def claim_daily_check(self, tenant_id: str, at: datetime) -> None:
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        held = self._daily.get(tenant_id)
        self._daily[tenant_id] = (
            replace(held, checked_at=at) if held else DailyCheck(tenant_id=tenant_id, checked_at=at))

    async def settle_daily_check(
        self, tenant_id: str, *, told: list[str], news: list[dict],
    ) -> None:
        held = self._daily.get(tenant_id)
        if held is not None:
            self._daily[tenant_id] = replace(held, told=tuple(told), news=tuple(dict(n) for n in news))

    async def sweep_access(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        before = len(self._access)
        self._access = [r for r in self._access if r["at"] >= now - access_rules.RETENTION]
        self._alerts = {k: v for k, v in self._alerts.items()
                        if v["at"] >= now - access_rules.ALERT_RETENTION}
        return before - len(self._access)


async def resolver_for(store: Store, tenant_id: str, connectors=None):
    """Load a tenant's credentials, then return a synchronous resolver.

    The SDK calls a CredentialResolver synchronously deep inside
    fetch_rows, and reachability checks cannot await at all. So the
    credentials are fetched once here and the returned closure reads from
    a plain dict — which is the seam fetch_rows, the query session and the
    reconciliation runner already expect, unchanged.

    Loading up front also bounds the exposure: plaintext exists for the
    life of one request rather than being fetchable at arbitrary depth.
    """
    from okwan_core import all_connectors

    wanted = {c.name: c.auth.required_fields for c in connectors or all_connectors()}
    loaded: dict[tuple[str, str], str] = {
        (connector, field): value
        for connector, creds in (await store.credentials_many(tenant_id, wanted)).items()
        for field, value in creds.items()
    }

    def resolve(connector_name: str, fields: tuple[str, ...]) -> dict[str, str]:
        return {f: loaded.get((connector_name, f), "") for f in fields}

    # Who the resolver answers for, so a connector over Okwan's own store
    # (`files`) reads this tenant's data and no other (Connector.context).
    resolve.tenant_id = tenant_id  # type: ignore[attr-defined]
    return resolve
