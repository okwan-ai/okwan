"""Which API version answers a request, and the answer in that version.

Every request under /v1/ is answered in one version (okwan_core.versions):
the one in effect on the day its `Okwan-Version` header names, else the
one in effect for the account whose key or session made it (stamped when
the account was made), else, on a route that takes neither, the newest.
The answer says which, in the same header, always a version's own date.

A header that is not a day, or names a day before the first version, is
refused with 400 before any route runs, and so are two different values:
code that asks for a shape no version had is told so, rather than
answered in another. A day after this release's newest version is
answered in its newest: the client is ahead of what is deployed.

A route makes its answer in the newest shape. When the version answering
is older and a change since then alters that route, its successful JSON
answer is given back in the older shape here, by the changes' `back`
functions; any other answer passes through untouched, byte for byte.
Errors are never reshaped.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import Request
from okwan_core import versions
from okwan_vault.models import Tenant

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_NAME = versions.HEADER.lower().encode()
#: Where in the request's state the asked and the account's versions are kept.
_ASKED = "okwan_version_asked"
_ACCOUNT = "okwan_version_account"


class Unknown(ValueError):
    """The header names no version of this API, or two different ones."""


def asked(headers: list[tuple[bytes, bytes]]) -> str | None:
    """The version in effect on the day the request's header names, or None
    when it has no header."""
    # Only the spaces and tabs HTTP allows around a value are taken off.
    values = {v.decode("latin-1").strip(" \t") for k, v in headers if k.lower() == _NAME}
    if not values:
        return None
    if len(values) > 1:
        raise Unknown("two versions")
    (value,) = values
    found = versions.in_effect(value)
    if found is None:
        raise Unknown(value)
    return found


def remember(request: Request, tenant: Tenant) -> None:
    """Called wherever a key or session is resolved: the account's version
    answers when the request names none."""
    request.scope.setdefault("state", {})[_ACCOUNT] = tenant.api_version


def of_scope(scope: Scope) -> str:
    """The version answering: the one asked, else the one in effect for the
    account (a stamp this release does not know, made by a newer one, is
    answered in this release's newest), else the newest."""
    state = scope.get("state") or {}
    return state.get(_ASKED) or versions.in_effect(state.get(_ACCOUNT)) or versions.current()


def of(request: Request) -> str:
    """The version this request is answered in."""
    return of_scope(request.scope)


def _refusal() -> bytes:
    return json.dumps({"detail": (
        f"{versions.HEADER} names no version of this API: send a day, YYYY-MM-DD, on or after "
        f"{versions.FIRST} (the versions are {', '.join(versions.dates())}), or leave it out to be "
        f"answered in your account's version.")}).encode()


class Versions:
    """ASGI middleware for /v1/: the header read and checked, the version
    that answered said on every answer, and an answer reshaped for an older
    version when a change since alters its route."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/v1/"):
            await self.app(scope, receive, send)
            return
        try:
            wanted = asked(list(scope.get("headers") or []))
        except Unknown:
            body = _refusal()
            await send({"type": "http.response.start", "status": 400, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                (_NAME, versions.current().encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        state = scope.setdefault("state", {})
        state[_ASKED] = wanted
        state.pop(_ACCOUNT, None)
        held: Message | None = None
        parts: list[bytes] = []
        answered = versions.current()
        route: str | None = None

        async def versioned(message: Message) -> None:
            nonlocal held, answered, route
            if message["type"] == "http.response.start":
                answered = of_scope(scope)
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != _NAME]
                headers.append((_NAME, answered.encode()))
                message = {**message, "headers": headers}
                path = getattr(scope.get("route"), "path", None)
                route = None if path is None else f"{scope.get('method', '')} {path}"
                if (200 <= message["status"] < 300 and route is not None and _is_json(headers)
                        and versions.changed(answered, route=route)):
                    held = message
                    return
                await send(message)
                return
            if held is None or message["type"] != "http.response.body":
                await send(message)
                return
            parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            body = _back(b"".join(parts), answered, route)
            start = {**held, "headers": [(k, v) for k, v in held["headers"] if k.lower() != b"content-length"]
                     + [(b"content-length", str(len(body)).encode())]}
            held = None
            await send(start)
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, versioned)


def _is_json(headers: list[tuple[bytes, bytes]]) -> bool:
    for k, v in headers:
        if k.lower() == b"content-type":
            return v.split(b";")[0].strip().lower() == b"application/json"
    return False


def _back(body: bytes, to: str, route: str | None) -> bytes:
    """A route's answer in version `to`, serialised as FastAPI serialises. A
    body that is not JSON after all is passed on as it came."""
    try:
        made = json.loads(body)
    except ValueError:
        return body
    return json.dumps(versions.back(made, to=to, route=route), ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode()
