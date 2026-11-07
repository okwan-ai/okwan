"""The edge of the API: a request-size cap and protective response headers.

One pure ASGI middleware in front of every route, the hosted MCP mount
included.

**Size.** Almost no request Okwan serves needs more than a few kilobytes:
a SQL string, a credential, a sign-in. Anything over MAX_BODY_BYTES is
refused with 413 before a handler, a JSON parser or the vault sees it,
whether it declares its length or streams it in chunks. One uvicorn
worker serves everyone, so a single oversized upload must not be able to
hold it. The one exception is a file import (`imports.py`), which carries
a file of up to 8 MB as base64 and gets UPLOAD_MAX_BODY_BYTES, on that
route and its preview only.

**Headers.** Every response says: only over HTTPS (HSTS), never inside a
frame, never sniff the type, send no referrer, and do not cache, since
almost every response is one tenant's data. The JSON routes also carry a
content security policy that allows nothing at all, which a JSON body
never needs. The API reference pages (/docs) load the reference viewer
from a CDN, so they keep the frame and type rules but not that policy.
A header a route sets itself is never overwritten.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.exceptions import HTTPException

MAX_BODY_BYTES = 1024 * 1024
#: An 8 MB file as base64 is 10.7 MB; the rest is the mapping and JSON.
UPLOAD_MAX_BODY_BYTES = 12 * 1024 * 1024
_UPLOAD = re.compile(r"^/v1/tenants/[^/]+/imports(?:/preview)?$")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
)
#: For JSON: nothing may load, run, or frame it.
LOCKED_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
#: Pages that render HTML and load their viewer from a CDN.
PAGES = ("/docs", "/redoc")


class _TooLarge(HTTPException):
    """An HTTP error, so a framework reading the body passes it on as 413
    rather than folding it into a generic parse error."""

    def __init__(self, limit: int) -> None:
        super().__init__(413, f"request body too large: the limit is {limit} bytes")


class Edge:
    def __init__(self, app: ASGIApp, max_body: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_body = max_body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        page = path.startswith(PAGES)
        limit = (UPLOAD_MAX_BODY_BYTES if scope.get("method") == "POST" and _UPLOAD.match(path)
                 else self.max_body)
        started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                present = {k.lower() for k, _ in message.get("headers", [])}
                extra = [h for h in HEADERS if h[0] not in present]
                if not page and LOCKED_CSP[0] not in present:
                    extra.append(LOCKED_CSP)
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await self._refuse(send_with_headers, limit)
            return

        seen = 0

        async def counted_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _TooLarge(limit)
            return message

        try:
            await self.app(scope, counted_receive, send_with_headers)
        except _TooLarge:
            if started:
                raise
            await self._refuse(send_with_headers, limit)

    async def _refuse(self, send: Send, limit: int) -> None:
        body = b'{"detail":"request body too large: the limit is %d bytes"}' % limit
        await send({"type": "http.response.start", "status": 413, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"connection", b"close"),
        ]})
        await send({"type": "http.response.body", "body": body})


def _content_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers", []):
        if key.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
