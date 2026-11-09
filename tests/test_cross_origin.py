"""No other website can act as a signed-in user, or read what Okwan returns.

Three walls, each checked here so a later change cannot quietly open one:

1. **No cross-origin access at all.** Neither the API nor the dashboard
   sends an Access-Control-Allow-Origin header, to any origin, ours
   included: the dashboard calls the API from its server, never from the
   browser. So a page on another site cannot read a response, and a
   preflight is never approved. Adding a CORS middleware fails this file.
2. **The API takes no cookie.** It authenticates only from the
   Authorization or X-Okwan-Key header, which a browser never attaches by
   itself. A session token sent as a cookie is refused.
3. **The dashboard changes nothing for another site.** Its session cookie
   is httpOnly, Secure in production and SameSite=Lax, and every route
   handler that changes data refuses a request whose Origin is not the
   dashboard's own (and one with no Origin at all).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from okwan_vault import EnvMasterKey, MemoryStore, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "apps" / "dashboard"
EVIL = "https://evil.example"


@pytest.fixture
async def api():
    from okwan_api.auth import set_store
    from okwan_api.main import app

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    shop = await store.create_tenant("Corner Café")
    key, _ = await store.issue_key(shop.id)
    session, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(shop.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        yield c, key, session


def _no_cors(r: httpx.Response) -> None:
    granted = sorted(h for h in r.headers if h.lower().startswith("access-control-allow"))
    assert not granted, f"{r.request.method} {r.request.url.path} grants cross-origin access: {granted}"


# ── 1. the API grants no origin anything ──────────────────────────────

@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/v1/connectors"), ("GET", "/v1/tenants"), ("POST", "/v1/query"), ("GET", "/healthz"),
])
async def test_the_api_answers_another_origin_without_any_cors_header(api, method, path):
    client, key, _ = api
    r = await client.request(method, path, headers={"Origin": EVIL, "Authorization": f"Bearer {key}"},
                             json={"sql": "SELECT 1"} if path == "/v1/query" else None)
    _no_cors(r)


@pytest.mark.parametrize("path", ["/v1/query", "/v1/tenants", "/v1/files/bank_deposits/list"])
async def test_a_preflight_from_another_origin_is_never_approved(api, path):
    client, *_ = api
    r = await client.options(path, headers={
        "Origin": EVIL, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code >= 400
    _no_cors(r)


async def test_the_hosted_mcp_grants_no_origin_anything():
    """The MCP server as main.py mounts it, with its own session manager
    running (the API's lifespan runs it in production)."""
    from okwan_api.main import _transport_security, build_mcp_server
    from starlette.applications import Starlette
    from starlette.routing import Mount

    server = build_mcp_server()
    app = Starlette(routes=[Mount("/mcp", server.streamable_http_app(
        streamable_http_path="/", transport_security=_transport_security()))])
    transport = httpx.ASGITransport(app=app)
    async with server.session_manager.run(), httpx.AsyncClient(transport=transport, base_url="http://okwan.test") as c:
        preflight = await c.options("/mcp/", headers={
            "Origin": EVIL, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type"})
        assert preflight.status_code >= 400
        _no_cors(preflight)
        post = await c.post("/mcp/", headers={"Origin": EVIL, "Accept": "application/json, text/event-stream"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        _no_cors(post)


def test_no_cors_middleware_is_mounted_anywhere_in_the_api():
    sources = [*ROOT.glob("apps/api/**/*.py"), *ROOT.glob("packages/**/*.py")]
    found = [str(p.relative_to(ROOT)) for p in sources if "CORSMiddleware" in p.read_text()]
    assert not found, f"a CORS middleware would let other sites read responses: {found}"


# ── 2. the API takes no cookie ─────────────────────────────────────────

async def test_a_session_sent_as_a_cookie_is_refused(api):
    client, _, session = api
    for name in ("okwan_session", "session", "Authorization"):
        r = await client.get("/v1/tenants", headers={"Cookie": f"{name}={session}", "Origin": EVIL})
        assert r.status_code == 401, f"the API accepted a cookie named {name}"


async def test_the_same_session_in_the_header_works(api):
    client, _, session = api
    assert (await client.get("/v1/tenants", headers={"Authorization": f"Bearer {session}"})).status_code == 200


# ── 3. the dashboard changes nothing for another site ─────────────────

_MUTATING = re.compile(r"export\s+async\s+function\s+(POST|PUT|PATCH|DELETE)\b")


def _route_handlers() -> list[Path]:
    return sorted(p for p in (DASHBOARD / "app").rglob("route.ts") if "node_modules" not in p.parts)


def test_every_dashboard_handler_that_changes_data_checks_the_origin():
    handlers = _route_handlers()
    assert len(handlers) >= 20, "found too few route handlers for the check to mean anything"
    unchecked = [str(p.relative_to(DASHBOARD)) for p in handlers
                 if _MUTATING.search(p.read_text()) and not re.search(r"\b(readBody|sameOrigin)\s*[<(]", p.read_text())]
    assert not unchecked, f"route handlers that change data without checking the Origin: {unchecked}"


def test_the_origin_check_refuses_a_request_with_no_origin():
    body = (DASHBOARD / "lib" / "api.ts").read_text()
    fn = body[body.index("export function sameOrigin"):]
    fn = fn[:fn.index("\n}\n")]
    assert 'if (!origin) return false;' in fn
    assert "new URL(origin).host === host" in fn


def test_the_session_cookie_is_httponly_lax_and_secure_in_production():
    body = (DASHBOARD / "lib" / "api.ts").read_text()
    fn = body[body.index("export async function setSession"):]
    fn = fn[:fn.index("\n}\n")]
    for setting in ("httpOnly: true", 'sameSite: "lax"', 'secure: process.env.NODE_ENV === "production"'):
        assert setting in fn, f"the session cookie lost `{setting}`"


def test_the_dashboard_sends_no_cors_header_either():
    config = (DASHBOARD / "next.config.ts").read_text()
    assert "Access-Control-Allow" not in config
    middleware = [p for p in DASHBOARD.glob("middleware.*")]
    assert all("Access-Control-Allow" not in p.read_text() for p in middleware)
