"""Where a caller-chosen credential can make the API connect.

Open signup made "any key holder" mean "anyone". The Postgres DSN and the
Shopify shop domain are the two credentials that pick a destination, and
these tests hold both to it: no private, loopback, link-local or
metadata address, no second host smuggled in an option, and the address
that was checked is the address that gets dialed.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket

import okwan_postgres.connector as pg
import pytest
from okwan_core import CredentialError, UpstreamError
from okwan_core.egress import EgressRefused, PinnedLoop, is_public, resolve_public
from okwan_shopify.connector import shop_host


def _answer(*ips):
    """A getaddrinfo result naming these addresses."""
    async def getaddrinfo(host, port, **_):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET,
                 socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    return getaddrinfo


@pytest.fixture
def dns(monkeypatch):
    def install(*ips):
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", _answer(*ips))
    return install


# ── address classification ──────────────────────────────────────────

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "::1",                       # loopback
    "10.0.0.5", "172.16.3.4", "192.168.1.1",  # private
    "169.254.169.254",                        # link-local: cloud metadata
    "fd00:ec2::254",                          # AWS IPv6 metadata (ULA)
    "100.100.100.200",                        # CGNAT range: Alibaba metadata
    "0.0.0.0", "224.0.0.1",                   # unspecified, multicast
    "::ffff:10.0.0.1", "::ffff:127.0.0.1",    # IPv4-mapped private
])
def test_non_public_addresses_are_refused(ip):
    assert not is_public(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ["3.218.140.55", "2600:1f18::1", "::ffff:3.218.140.55"])
def test_public_addresses_pass(ip):
    assert is_public(ipaddress.ip_address(ip))


# ── resolution ──────────────────────────────────────────────────────

async def test_a_name_resolving_private_is_refused(dns):
    dns("10.1.2.3")
    with pytest.raises(EgressRefused, match="not a public address"):
        await resolve_public("db.attacker.test", 5432)


async def test_one_private_answer_among_public_refuses_the_lot(dns):
    """A mixed answer is refused, not raced for the public address."""
    dns("3.218.140.55", "169.254.169.254")
    with pytest.raises(EgressRefused):
        await resolve_public("db.attacker.test", 5432)


async def test_public_answer_returns_the_address_to_dial(dns):
    dns("3.218.140.55")
    assert await resolve_public("db.example.test", 5432) == "3.218.140.55"


async def test_allowlisted_hostname_passes(dns, monkeypatch):
    dns("127.0.0.1")
    monkeypatch.setenv("OKWAN_EGRESS_ALLOW", "localhost, db.internal")
    assert await resolve_public("localhost", 5432) == "127.0.0.1"


async def test_allowlisted_cidr_passes_only_inside_it(dns, monkeypatch):
    monkeypatch.setenv("OKWAN_EGRESS_ALLOW", "10.20.0.0/16")
    dns("10.20.1.1")
    assert await resolve_public("db.internal", 5432) == "10.20.1.1"
    dns("10.30.1.1")
    with pytest.raises(EgressRefused):
        await resolve_public("db.internal", 5432)


# ── the pinned loop ─────────────────────────────────────────────────

class _RecordingLoop:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def create_connection(self, factory, host, port, **kw):
        self.calls.append((host, port, kw))
        return "transport", "protocol"

    def time(self) -> float:
        return 1.0


async def test_pinned_loop_dials_the_checked_address_not_the_name():
    """The rebinding guard: a second DNS answer is never consulted."""
    real = _RecordingLoop()
    loop = PinnedLoop(real, "db.example.test", "3.218.140.55")
    await loop.create_connection(object, "db.example.test", 5432)
    assert real.calls == [("3.218.140.55", 5432, {})]


async def test_pinned_loop_keeps_the_name_for_tls():
    """Connecting by IP must not drop SNI: Neon routes on it."""
    real = _RecordingLoop()
    loop = PinnedLoop(real, "db.example.test", "3.218.140.55")
    await loop.create_connection(object, "db.example.test", 5432, ssl=True)
    assert real.calls[0][2]["server_hostname"] == "db.example.test"


async def test_pinned_loop_refuses_any_other_destination():
    loop = PinnedLoop(_RecordingLoop(), "db.example.test", "3.218.140.55")
    with pytest.raises(EgressRefused):
        await loop.create_connection(object, "169.254.169.254", 80)
    with pytest.raises(EgressRefused):
        await loop.create_unix_connection(object, "/var/run/postgresql/.s.PGSQL.5432")


def test_pinned_loop_passes_everything_else_through():
    assert PinnedLoop(_RecordingLoop(), "h", "1.1.1.1").time() == 1.0


# ── Postgres DSN ────────────────────────────────────────────────────

@pytest.mark.parametrize("dsn", [
    "postgresql://u:p@db.example.test/db?passfile=/etc/passwd",
    "postgresql://u:p@db.example.test/db?sslrootcert=/app/secret.pem",
    "postgresql://u:p@db.example.test/db?service=prod",
    "postgresql://u:p@db.example.test/db?host=10.0.0.1",
    "postgresql://u:p@db.example.test/db?port=6543",
    "postgresql://u:p@a.example.test,b.example.test/db",
    "postgresql://u:p@/db",
    "postgresql://u:p@%2Fvar%2Frun%2Fpostgresql/db",
    "host=10.0.0.1 dbname=db",
    "mysql://u:p@db.example.test/db",
])
def test_dsns_that_name_another_destination_or_a_local_file_are_refused(dsn):
    with pytest.raises(EgressRefused):
        pg.parse_dsn(dsn)


def test_a_neon_shaped_dsn_parses():
    host, port, has_password = pg.parse_dsn(
        "postgresql://u:p@ep-x.c-5.us-east-2.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    assert (host, port, has_password) == ("ep-x.c-5.us-east-2.aws.neon.tech", 5432, True)


@pytest.fixture
def connect_spy(monkeypatch, dns):
    calls: list[dict] = []

    async def fake_connect(dsn, **kw):
        calls.append({"dsn": dsn, **kw})
        return object()

    monkeypatch.setattr(pg.asyncpg, "connect", fake_connect)
    return calls


async def test_transport_connects_through_a_loop_pinned_to_the_checked_address(
    dns, connect_spy
):
    dns("3.218.140.55")
    await pg.PgTransport("postgresql://u:p@db.example.test/db").conn()
    loop = connect_spy[0]["loop"]
    assert isinstance(loop, PinnedLoop)
    assert (loop._host, loop._ip) == ("db.example.test", "3.218.140.55")


async def test_a_private_dsn_never_reaches_the_driver(dns, connect_spy):
    dns("10.0.0.7")
    with pytest.raises(UpstreamError) as err:
        await pg.PgTransport("postgresql://u:p@internal.test/db").conn()
    assert err.value.status == 403
    assert connect_spy == []


async def test_no_password_in_the_dsn_means_none_is_sent(dns, connect_spy):
    """Otherwise asyncpg reads this server's PGPASSWORD or ~/.pgpass and
    sends whatever it finds to the caller's host."""
    dns("3.218.140.55")
    await pg.PgTransport("postgresql://u@db.example.test/db").conn()
    assert connect_spy[0]["password"] == ""


async def test_a_dsn_password_is_not_overridden(dns, connect_spy):
    dns("3.218.140.55")
    await pg.PgTransport("postgresql://u:p@db.example.test/db").conn()
    assert "password" not in connect_spy[0]


# ── Shopify shop domain ─────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "169.254.169.254",
    "localhost",
    "evil.example/x?",
    "shop.myshopify.com:8443",
    "user@shop.myshopify.com",
    "shop.myshopify.com.evil.example",
    "shop.myshopify.com/admin",
    "custom-store-domain.com",
])
def test_shop_domain_must_be_a_myshopify_name(raw):
    with pytest.raises(CredentialError):
        shop_host(raw)


def test_shop_domain_is_normalized():
    assert shop_host(" https://My-Store.myshopify.com/ ") == "my-store.myshopify.com"
