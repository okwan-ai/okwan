"""Vault administration.

Operator tooling. Root tenants also come from self-serve signup now
(`okwan_api/signup.py`, gated on a verified email); this remains for
provisioning by hand and for inspecting a tenant.

    python -m okwan_vault keygen
    python -m okwan_vault tenant create "Acme ISV"
    python -m okwan_vault key issue ten_abc123
    python -m okwan_vault cred set ten_abc123 stripe secret_key
    python -m okwan_vault tenant show ten_abc123
    python -m okwan_vault tenant list
    python -m okwan_vault account add ten_abc123 founder@acme.com
    python -m okwan_vault account reset-password founder@acme.com
    python -m okwan_vault plan set ten_abc123 pro
    python -m okwan_vault plan show ten_abc123
    python -m okwan_vault support list

Setting a plan is operator-only on purpose: there is no API route for
it, so no key or session can raise its own tenant's limit, and there is
no billing yet to authorise a change (§9 2026-11-02).
"""
from __future__ import annotations

import asyncio
import base64
import getpass
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from . import accounts
from .crypto import new_key
from .keys import from_env
from .models import Tenant
from .postgres import PostgresStore
from .usage import NOT_ROOT_PLAN, PlanRefused, billing_root, month_start, quota


def _dsn() -> str:
    dsn = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
    if not dsn:
        sys.exit("OKWAN_VAULT_DATABASE_URL is not set")
    return dsn


async def _store() -> PostgresStore:
    return await PostgresStore(_dsn(), from_env()).connect()


async def tenant_create(name: str) -> None:
    store = await _store()
    try:
        tenant = await store.create_tenant(name)
        print(f"tenant  {tenant.id}")
        print(f"name    {tenant.name}")
    finally:
        await store.close()


async def tenant_show(tenant_id: str) -> None:
    store = await _store()
    try:
        tenant = await store.get_tenant(tenant_id)
        if tenant is None:
            sys.exit(f"no such tenant: {tenant_id}")
        print(f"tenant  {tenant.id}")
        print(f"name    {tenant.name}")
        configured = await store.connectors_configured(tenant_id)
        if not configured:
            print("connectors: none configured")
        else:
            print("connectors:")
            for connector, fields in sorted(configured.items()):
                print(f"  {connector:12} {', '.join(sorted(fields))}")
    finally:
        await store.close()


async def tenant_list() -> None:
    store = await _store()
    try:
        for t in await store.list_tenants():
            print(f"{t.id}  {'root' if t.is_root else 'child of ' + t.parent_id:<32}  {t.name}")
    finally:
        await store.close()


def _read_password() -> str:
    """From OKWAN_ACCOUNT_PASSWORD, else piped stdin. Not getpass: it opens
    /dev/tty directly and hangs in Codespaces (§11). A terminal on stdin is
    refused rather than read, because typing there echoes the password."""
    password = os.environ.get("OKWAN_ACCOUNT_PASSWORD")
    if password is None:
        if sys.stdin.isatty():
            sys.exit("set OKWAN_ACCOUNT_PASSWORD or pipe the password on stdin")
        password = sys.stdin.readline().rstrip("\r\n")
    return password


def _check_password(password: str) -> None:
    """The bounds signup holds every password to."""
    if not accounts.MIN_PASSWORD <= len(password) <= accounts.MAX_PASSWORD:
        raise accounts.AccountRefused(
            f"password must be {accounts.MIN_PASSWORD} to "
            f"{accounts.MAX_PASSWORD} characters"
        )


async def add_account(store, tenant_id: str, email: str, password: str) -> str:
    """The checks and the insert, apart from where the store and the
    password come from. Raises AccountRefused with the reason."""
    email = accounts.normalize_email(email)
    if len(email) > 254 or not re.match(accounts.EMAIL_PATTERN, email):
        raise accounts.AccountRefused(f"not an email address: {email!r}")
    _check_password(password)
    await store.add_account(tenant_id, email, accounts.hash_password(password))
    return email


async def reset_password(store, email: str, password: str) -> tuple[str, str]:
    """A new password for an existing login; returns (email, tenant_id).

    Every session the login holds ends with it, so a browser signed in
    with the old password is signed out. Raises AccountRefused with the
    reason: no such login, or a password outside signup's bounds.
    """
    email = accounts.normalize_email(email)
    _check_password(password)
    tenant_id = await store.reset_password(email, accounts.hash_password(password))
    return email, tenant_id


async def account_add(tenant_id: str, email: str) -> None:
    """A login for an existing root tenant, without email verification.

    For the operator's own tenant only: signup proves the address owns the
    inbox, and this does not (§11).
    """
    password = _read_password()
    store = await _store()
    try:
        email = await add_account(store, tenant_id, email, password)
    except accounts.AccountRefused as e:
        sys.exit(str(e))
    finally:
        await store.close()
    print(f"account {email}")
    print(f"tenant  {tenant_id}")


async def _root_tenant(store, tenant_id: str) -> Tenant:
    """The tenant, if it is a root; a child names the root that pays."""
    tenant = await store.get_tenant(tenant_id)
    if tenant is None:
        raise PlanRefused(f"no such tenant: {tenant_id}")
    if not tenant.is_root:
        root = await billing_root(store, tenant_id)
        raise PlanRefused(
            f"{tenant_id} is not a root tenant; {NOT_ROOT_PLAN}. Its usage is "
            f"billed to {root}"
        )
    return tenant


async def show_plan(store, tenant_id: str, now: datetime | None = None) -> list[str]:
    """A root tenant's plan, limit and month-to-date usage, as lines.

    Read through `usage.quota`, the same call the 402 gate makes, so what
    this prints is what the gate will decide on.
    """
    await _root_tenant(store, tenant_id)
    q = await quota(store, tenant_id, now)
    limit = "unmetered" if q.unmetered else f"{q.limit:,} requests a month"
    lines = [
        f"tenant     {tenant_id}",
        f"plan       {q.plan}",
        f"limit      {limit}",
        f"used       {q.used:,} since {month_start(now):%Y-%m-%d} UTC, merchants included",
    ]
    if not q.unmetered:
        lines.append(f"remaining  {q.remaining:,}")
    if q.exceeded:
        lines.append("gate       limit reached: metered calls answer 402")
    return lines


async def set_plan(store, tenant_id: str, name: str) -> list[str]:
    """Set a root tenant's plan and return what `plan show` now prints.

    Raises PlanRefused with the reason: an unknown plan, an unknown
    tenant, or a child tenant.
    """
    await _root_tenant(store, tenant_id)
    await store.set_plan(tenant_id, name)
    return await show_plan(store, tenant_id)


async def plan_set(tenant_id: str, name: str) -> None:
    store = await _store()
    try:
        lines = await set_plan(store, tenant_id, name)
    except PlanRefused as e:
        sys.exit(str(e))
    finally:
        await store.close()
    print("\n".join(lines))


async def plan_show(tenant_id: str) -> None:
    store = await _store()
    try:
        lines = await show_plan(store, tenant_id)
    except PlanRefused as e:
        sys.exit(str(e))
    finally:
        await store.close()
    print("\n".join(lines))


def ticket_lines(tickets) -> list[str]:
    """One line per escalation, newest first: what the operator reads."""
    if not tickets:
        return ["no support tickets"]
    return [f"{t.created_at:%Y-%m-%d %H:%M}  {t.id}  {t.severity}  {t.category:<13}  "
            f"{t.tenant_id}  {'mailed' if t.notified else 'NOT MAILED'}  {t.summary[:120]}"
            for t in tickets]


async def support_list() -> None:
    """Every escalation from the support assistant, mailed or not."""
    store = await _store()
    try:
        tickets = await store.list_tickets()
    finally:
        await store.close()
    print("\n".join(ticket_lines(tickets)))


def setup_text(email: str, secret: str) -> str:
    """What `operator enroll` writes for the operator's authenticator app."""
    from . import totp

    return (
        f"Okwan operator console: authenticator setup for {email}\n\n"
        "In your authenticator app (Google Authenticator, Microsoft Authenticator,\n"
        "1Password, Authy): Add account -> Enter a setup key.\n\n"
        "  Account:  Okwan\n"
        f"  Key:      {totp.grouped(secret)}\n"
        "  Type:     Time based\n\n"
        "Or, if your app opens otpauth links:\n"
        f"  {totp.setup_uri(secret, email)}\n\n"
        "Then delete this file. Anyone with the key can make your codes.\n"
    )


async def operator_enroll(email: str) -> None:
    """Give an operator login an authenticator secret, replacing any old one.

    The key is written to a file only the operator can read, never to the
    terminal, so it is not on screen for a screenshot or in scrollback.
    """
    from . import totp

    email = accounts.normalize_email(email)
    store = await _store()
    try:
        if await store.account_login(email) is None:
            sys.exit(f"no login for {email}: operators sign in to the dashboard first")
        secret = totp.new_secret()
        await store.put_operator_factor(email, secret)
        await store.log_operator(email, "enroll", email, {"by": "cli"})
    finally:
        await store.close()
    path = Path.home() / "okwan-operator-setup.txt"
    path.write_text(setup_text(email, secret))
    path.chmod(0o600)
    print(f"wrote {path}: open it, add the key to your authenticator app, then delete it.")
    print(f"The console also needs {email} in OKWAN_OPERATOR_EMAILS on the Render service.")


async def operator_log() -> None:
    store = await _store()
    try:
        entries = await store.operator_log(100)
    finally:
        await store.close()
    if not entries:
        print("no operator actions yet")
    for e in entries:
        target = e["target"] or ""
        print(f"{e['at']:%Y-%m-%d %H:%M:%S}  {e['operator']}  {e['action']:<14}  {target}")


async def account_reset_password(email: str) -> None:
    """A forgotten password, replaced by the operator.

    There is no reset by email yet, and no API route for this on
    purpose: only someone holding the vault database can run it. The
    password is read the same way as for `account add`.
    """
    password = _read_password()
    store = await _store()
    try:
        email, tenant_id = await reset_password(store, email, password)
    except accounts.AccountRefused as e:
        sys.exit(str(e))
    finally:
        await store.close()
    print(f"account {email}")
    print(f"tenant  {tenant_id}")
    print("password replaced; every session for this account has ended")


async def key_issue(tenant_id: str) -> None:
    store = await _store()
    try:
        if await store.get_tenant(tenant_id) is None:
            sys.exit(f"no such tenant: {tenant_id}")
        full, record = await store.issue_key(tenant_id)
        print(f"key id  {record.id}")
        print(f"prefix  {record.prefix}")
        print("\nSecret key, shown once and never stored:\n")
        print(f"  {full}\n")
    finally:
        await store.close()


async def key_revoke(key_id: str) -> None:
    store = await _store()
    try:
        await store.revoke_key(key_id)
        print(f"revoked {key_id}")
    finally:
        await store.close()


async def cred_set(tenant_id: str, connector: str, field_name: str) -> None:
    """Value is prompted, never passed as an argument.

    A secret on the command line lands in shell history and in the
    process table where any other user on the box can read it.
    """
    value = getpass.getpass(f"{connector}.{field_name}: ")
    if not value:
        sys.exit("empty value")
    store = await _store()
    try:
        if await store.get_tenant(tenant_id) is None:
            sys.exit(f"no such tenant: {tenant_id}")
        await store.put_credential(tenant_id, connector, field_name, value)
        print(f"stored {connector}.{field_name} for {tenant_id}")
    finally:
        await store.close()


async def cred_delete(tenant_id: str, connector: str, field_name: str) -> None:
    store = await _store()
    try:
        await store.delete_credential(tenant_id, connector, field_name)
        print(f"deleted {connector}.{field_name} for {tenant_id}")
    finally:
        await store.close()


USAGE = """usage:
  python -m okwan_vault keygen
  python -m okwan_vault tenant create <name>
  python -m okwan_vault tenant show <tenant_id>
  python -m okwan_vault tenant list
  python -m okwan_vault account add <tenant_id> <email>
  python -m okwan_vault account reset-password <email>
  python -m okwan_vault plan set <tenant_id> <plan>
  python -m okwan_vault plan show <tenant_id>
  python -m okwan_vault support list
  python -m okwan_vault operator enroll <email>
  python -m okwan_vault operator log
  python -m okwan_vault key issue <tenant_id>
  python -m okwan_vault key revoke <key_id>
  python -m okwan_vault cred set <tenant_id> <connector> <field>
  python -m okwan_vault cred delete <tenant_id> <connector> <field>"""


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(USAGE)

    verb, rest = argv[0], argv[1:]

    if verb == "keygen":
        print(base64.urlsafe_b64encode(new_key()).decode())
        return

    if not rest:
        sys.exit(USAGE)
    action, args = rest[0], rest[1:]

    routes = {
        ("tenant", "create"): (tenant_create, 1),
        ("tenant", "show"): (tenant_show, 1),
        ("tenant", "list"): (tenant_list, 0),
        ("account", "add"): (account_add, 2),
        ("account", "reset-password"): (account_reset_password, 1),
        ("plan", "set"): (plan_set, 2),
        ("plan", "show"): (plan_show, 1),
        ("support", "list"): (support_list, 0),
        ("operator", "enroll"): (operator_enroll, 1),
        ("operator", "log"): (operator_log, 0),
        ("key", "issue"): (key_issue, 1),
        ("key", "revoke"): (key_revoke, 1),
        ("cred", "set"): (cred_set, 3),
        ("cred", "delete"): (cred_delete, 3),
    }
    route = routes.get((verb, action))
    if route is None:
        sys.exit(USAGE)
    fn, argc = route
    if len(args) != argc:
        sys.exit(USAGE)
    asyncio.run(fn(*args))
