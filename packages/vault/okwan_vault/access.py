"""The access log's rules, shared by both stores and the API.

Every metered read of a tenant's data is one row: when, which credential
(an API key's public prefix, or "session" for the dashboard), the caller's
address and its network, the surface, and what was read. The row never
holds query text, parameters, rows or results.

Two things in it are alert-worthy, and the API decides which (okwan_api
.access): an API key read from a network it has not used for
NETWORK_MEMORY, once the key is ESTABLISHED; and an hour in which a key
read far more than its busiest hour of the week before.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta

#: How long an access row is kept. Long enough to look back over weeks of
#: quiet copying, the case the log exists for; short enough that an
#: address is not held for longer than the question it answers.
RETENTION = timedelta(days=90)

#: How long an alert is kept: a year of incidents, and the month after.
ALERT_RETENTION = timedelta(days=400)

#: A network a key has used within this window is a known one.
NETWORK_MEMORY = timedelta(days=30)

#: A key's first day sets up where it runs from, so a new deployment does
#: not alert on its own first requests. After that, a new network does.
ESTABLISHED = timedelta(hours=24)

#: An hour is unusual when a key reads at least FLOOR times in it and more
#: than FACTOR times its busiest hour over the WEEK before.
VOLUME_FLOOR = 100
VOLUME_FACTOR = 3
VOLUME_WEEK = timedelta(days=7)

#: At most one volume alert per key in this window.
VOLUME_QUIET = timedelta(hours=24)

#: What a key looks like in the log: its stored public prefix.
KEY_MARK = "okw_"

#: The credential a dashboard session is logged under.
SESSION = "session"


@dataclass(frozen=True, slots=True)
class Seen:
    """What the log knew about a credential before the row just written."""

    #: The credential read from this network within NETWORK_MEMORY.
    known_network: bool
    #: The credential's oldest row still kept, or None for its first.
    first_seen: datetime | None


def network_of(address: str) -> str:
    """The network an address belongs to, for telling a key's usual place
    from a new one: /24 for IPv4 and /48 for IPv6, the blocks providers
    hand to one site. An address that does not parse is its own network."""
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return address.strip() or "unknown"
    if ip.version == 4:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    if ip.ipv4_mapped:
        return str(ipaddress.ip_network(f"{ip.ipv4_mapped}/24", strict=False))
    return str(ipaddress.ip_network(f"{ip}/48", strict=False))


def is_key(credential: str) -> bool:
    return credential.startswith(KEY_MARK)
