"""Vault administration.

Deliberately a CLI rather than HTTP routes. Onboarding an ISV is a thing
you do, not a thing the internet does, and an admin API that can mint
tenants is an attack surface with no current user. Add it when self-serve
signup exists; until then there is nothing exposed to protect.

    python -m okwan_vault keygen
    python -m okwan_vault tenant create "Acme ISV"
    python -m okwan_vault key issue ten_abc123
    python -m okwan_vault cred set ten_abc123 stripe secret_key
    python -m okwan_vault tenant show ten_abc123
"""
from __future__ import annotations

import asyncio
import base64
import getpass
import os
import sys

from .crypto import new_key
from .keys import from_env
from .postgres import PostgresStore


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
