"""Where a connector may connect when the caller chooses the host.

Most connectors dial a fixed host (api.stripe.com). A few take the host
from a credential — Postgres from a DSN — and since signup is open, a
credential is anything any verified account typed. Unchecked, that turns
the API into a way to open TCP connections to Render-internal services,
loopback, or the cloud metadata endpoint.

The rule: resolve first, refuse unless every address is publicly routable,
then connect to the address that was checked. Checking a name and then
handing the name to a client that resolves it again leaves a window in
which a second DNS answer can point somewhere else (DNS rebinding);
`PinnedLoop` closes it by dialing the checked address while TLS still
presents the original name for SNI and verification.

`OKWAN_EGRESS_ALLOW` names deliberate exceptions, comma-separated: exact
hostnames (`localhost`, `db.internal`) or CIDR blocks (`10.20.0.0/16`).
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Any

from .errors import OkwanError

RESOLVE_TIMEOUT = 10.0

_Address = ipaddress.IPv4Address | ipaddress.IPv6Address


class EgressRefused(OkwanError):
    """The destination is not one a caller-chosen connection may reach."""


def allowlist() -> tuple[frozenset[str], tuple[ipaddress._BaseNetwork, ...]]:
    """Read per call: cheap, and a changed env needs no restart in tests."""
    hosts: set[str] = set()
    nets: list[ipaddress._BaseNetwork] = []
    for entry in os.environ.get("OKWAN_EGRESS_ALLOW", "").split(","):
        entry = entry.strip().lower()
        if not entry:
            continue
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            hosts.add(entry.rstrip("."))
    return frozenset(hosts), tuple(nets)


def is_public(ip: _Address) -> bool:
    """Globally routable unicast, judged on what the packet will reach.

    `is_global` already excludes private, loopback, link-local (and so
    169.254.169.254, the metadata service), CGNAT 100.64/10 and reserved
    ranges. An IPv4-mapped IPv6 address is judged as the IPv4 address it
    carries, since that is where the connection lands.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve_public(host: str, port: int) -> str:
    """Resolve `host` and return one address that may be dialed, or raise.

    Every address in the answer must pass, not just the one returned: a
    name answering with one public and one private address is refused
    rather than raced.
    """
    allowed_hosts, allowed_nets = allowlist()
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), RESOLVE_TIMEOUT
        )
    except (OSError, TimeoutError) as exc:
        raise EgressRefused(f"cannot resolve {host}: {exc}") from None
    addresses = list(dict.fromkeys(ipaddress.ip_address(i[4][0].split("%")[0])
                                   for i in infos))
    if not addresses:
        raise EgressRefused(f"{host} has no addresses")
    if host.lower().rstrip(".") not in allowed_hosts:
        for ip in addresses:
            if not is_public(ip) and not any(ip in n for n in allowed_nets):
                raise EgressRefused(
                    f"{host} resolves to {ip}, which is not a public address; "
                    "add it to OKWAN_EGRESS_ALLOW if this is deliberate"
                )
    return str(addresses[0])


class PinnedLoop:
    """An event loop that may dial exactly one checked address.

    Handed to a client library in place of the running loop. Every
    attribute passes through except outbound connections: a connection to
    `host` goes to `ip` instead, TLS keeps `host` as its server name, and
    any other destination — another host, a unix socket — is refused.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, host: str, ip: str) -> None:
        self._loop, self._host, self._ip = loop, host, ip

    def __getattr__(self, name: str) -> Any:
        return getattr(self._loop, name)

    async def create_connection(self, factory, host=None, port=None, **kw):
        if host != self._host:
            raise EgressRefused(f"connection to {host} was not checked")
        if kw.get("ssl") and kw.get("server_hostname") is None:
            kw["server_hostname"] = self._host
        return await self._loop.create_connection(factory, self._ip, port, **kw)

    async def create_unix_connection(self, *args, **kw):
        raise EgressRefused("unix sockets are not reachable from a connector")
