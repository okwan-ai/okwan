"""The API's edge: oversized requests refused, protective headers on every response."""
from __future__ import annotations

import httpx
import pytest
from okwan_api.edge import LOCKED_CSP, MAX_BODY_BYTES, Edge
from okwan_vault import EnvMasterKey, MemoryStore, new_key


@pytest.fixture
async def client():
    from okwan_api.auth import set_store
    from okwan_api.main import app

    set_store(MemoryStore(EnvMasterKey(new_key())))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        yield c


async def test_a_declared_oversized_body_is_refused_before_any_handler(client):
    r = await client.post("/v1/sessions", content=b"x" * (MAX_BODY_BYTES + 1),
                          headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert str(MAX_BODY_BYTES) in r.json()["detail"]
    assert r.headers["x-frame-options"] == "DENY"


async def test_a_streamed_oversized_body_is_refused(client):
    async def chunks():
        for _ in range(MAX_BODY_BYTES // 65536 + 2):
            yield b"x" * 65536

    r = await client.post("/v1/query", content=chunks(), headers={"content-type": "application/json",
                                                                 "authorization": "Bearer okw_none"})
    assert r.status_code == 413


async def test_an_ordinary_body_passes(client):
    r = await client.post("/v1/sessions", json={"email": "nobody@example.com", "password": "x" * 12})
    assert r.status_code in (401, 429), r.text


@pytest.mark.parametrize("path", ["/healthz", "/v1/connectors", "/v1/reconciliations"])
async def test_json_responses_carry_every_protective_header(client, path):
    r = await client.get(path)
    h = r.headers
    assert h["strict-transport-security"].startswith("max-age=")
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert h["cache-control"] == "no-store"
    assert h["content-security-policy"] == LOCKED_CSP[1].decode()


async def test_the_reference_page_keeps_its_viewer(client):
    """/docs loads its viewer from a CDN: no locked policy, every other header."""
    r = await client.get("/docs")
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["strict-transport-security"]


async def test_a_header_a_route_sets_is_kept():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"cache-control", b"public, max-age=60")]})
        await send({"type": "http.response.body", "body": b"{}"})

    transport = httpx.ASGITransport(app=Edge(app))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/anything")
    assert r.headers["cache-control"] == "public, max-age=60"
    assert r.headers.get_list("cache-control") == ["public, max-age=60"]
    assert r.headers["x-frame-options"] == "DENY"


async def test_websockets_and_lifespan_pass_through_untouched():
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    edge = Edge(app)
    await edge({"type": "lifespan"}, None, None)
    await edge({"type": "websocket", "path": "/"}, None, None)
    assert seen == ["lifespan", "websocket"]
