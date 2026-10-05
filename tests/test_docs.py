"""The API reference at /docs and the OpenAPI document it reads.

Documentation only: these hold that every route is in a named section, every
connector's section comes from its own declaration, the key is documented as
a security scheme rather than as raw header fields, the write operations are
marked, and the page loads one pinned, integrity-checked Scalar build.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from okwan_core import all_connectors

from okwan_api import docs
from okwan_api.main import app


@pytest.fixture(scope="module")
def spec() -> dict:
    return TestClient(app).get("/openapi.json").json()


def operations(spec: dict):
    for path, item in spec["paths"].items():
        for method, op in item.items():
            yield path, method, op


def test_the_page_is_okwans_own_and_pins_its_script():
    r = TestClient(app).get("/docs")
    assert r.status_code == 200
    assert "Okwan API reference" in r.text
    assert f"@scalar/api-reference@{docs.SCALAR_VERSION}/" in r.text
    assert f'integrity="{docs.SCALAR_SRI}"' in r.text
    assert "/openapi.json" in r.text
    # Scalar's hosted add-ons stay off.
    for off in ('"showDeveloperTools": "never"', '"agent": {"disabled": true}', '"mcp": {"disabled": true}',
                '"telemetry": false'):
        assert off in r.text


def test_redoc_points_at_the_one_reference():
    r = TestClient(app).get("/redoc", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"


def test_every_operation_sits_in_a_described_section_and_every_section_in_a_group(spec):
    named = {t["name"]: t for t in spec["tags"]}
    for path, method, op in operations(spec):
        assert op.get("tags"), f"{method} {path} has no section"
        for t in op["tags"]:
            assert t in named, f"{method} {path}: section {t} is not declared"
    assert all(t["description"] and t["x-displayName"] for t in spec["tags"])
    grouped = [t for g in spec["x-tagGroups"] for t in g["tags"]]
    assert sorted(grouped) == sorted(named)


def test_connector_sections_come_from_their_declarations(spec):
    named = {t["name"]: t for t in spec["tags"]}
    for c in all_connectors():
        assert named[c.name]["description"] == c.description
        assert named[c.name]["x-displayName"] == docs.label(c.description, c.name)
    assert named["paypal"]["x-displayName"] == "PayPal"
    # The check's own systems lead the connectors, ledger first.
    connectors = next(g for g in spec["x-tagGroups"] if g["name"] == "Connectors")["tags"]
    assert connectors[:3] == ["shopify", "paypal", "stripe"]


def test_the_key_is_a_security_scheme_not_a_header_field(spec):
    schemes = spec["components"]["securitySchemes"]
    assert schemes["OkwanKey"]["scheme"] == "bearer"
    assert schemes["OkwanKeyHeader"]["name"] == "X-Okwan-Key"
    secured = 0
    for path, method, op in operations(spec):
        for p in op.get("parameters", []):
            assert not (p["in"] == "header" and p["name"].lower() in docs.AUTH_HEADERS), f"{method} {path}"
        secured += bool(op.get("security"))
    assert secured >= 40
    public = {("get", "/healthz"), ("get", "/v1/connectors"), ("post", "/v1/signup"),
              ("post", "/v1/signup/verify"), ("post", "/v1/sessions")}
    for path, method, op in operations(spec):
        if (method, path) in public:
            assert "security" not in op, f"{method} {path} is public"


def test_writes_are_marked_and_only_writes(spec):
    writes = {f"{c.name}_{r.name}_{op.name}" for c in all_connectors()
              for r, op in c.iter_operations() if not op.is_read_only}
    marked = {op["operationId"] for _, _, op in operations(spec) if op.get("x-okwan-writes")}
    assert marked == writes and writes
    for _, _, op in operations(spec):
        if op.get("x-okwan-writes"):
            assert op["description"].startswith("**Writes.**")


def test_connector_operations_get_short_titles_and_keep_their_text(spec):
    for c in all_connectors():
        for r, op in c.iter_operations():
            o = spec["paths"][f"/v1/{c.name}/{r.name}/{op.name}"]["post"]
            assert o["summary"] == docs.operation_title(r.name, op.name)
            assert op.description in o["description"]
    assert docs.operation_title("transactions", "list") == "List transactions"
    assert docs.operation_title("customers", "get") == "Get customer"
    assert docs.operation_title("tables", "get_schema") == "Get table schema"
    assert docs.operation_title("messages", "send_text") == "Send text message"


def test_the_introduction_states_what_is_true_now(spec):
    text = spec["info"]["description"]
    for needed in ("Authorization: Bearer okw_", "/v1/reconciliations/across/rails", "402", "409", "/mcp/"):
        assert needed in text
    assert "Conduit" not in text
