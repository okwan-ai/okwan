"""Federated SQL over connector definitions."""
from __future__ import annotations

import okwan_paystack.connector  # noqa: F401
import okwan_postgres.connector  # noqa: F401
import okwan_shopify.connector  # noqa: F401
import okwan_stripe.connector  # noqa: F401
import pytest
from okwan_query import QuerySession, catalog, find
from okwan_query.types import column_type


def test_catalog_is_derived_not_declared():
    """Every table comes from a connector definition, not a table list."""
    names = {t.qualified for t in catalog()}
    assert "shopify.orders" in names
    assert "stripe.charges" in names
    assert "paystack.transactions" in names


def test_containers_are_excluded():
    """RowSet describes the envelope, not the record — not declarable."""
    names = {t.qualified for t in catalog()}
    assert "postgres.sql" not in names
    assert "postgres.rows" not in names


def test_computed_fields_become_columns():
    """Serialization mode: agent-facing fields are SQL columns for free."""
    cols = dict(find("shopify.orders").columns)
    assert cols["net_payment_minor"] == "BIGINT"
    assert cols["net_payment_major"] == "DOUBLE"
    assert cols["is_reconcilable"] == "BOOLEAN"


def test_optional_fields_keep_their_type():
    """`str | None` is a VARCHAR column, not JSON."""
    assert column_type({"anyOf": [{"type": "string"}, {"type": "null"}]}) == "VARCHAR"
    assert column_type({"anyOf": [{"type": "integer"}, {"type": "null"}]}) == "BIGINT"


def test_datetime_maps_to_timestamp():
    assert column_type({"type": "string", "format": "date-time"}) == "TIMESTAMP"


def test_nested_models_become_json_not_flattened():
    """Flattening would diverge from the REST payload."""
    cols = dict(find("paystack.transactions").columns)
    assert cols["customer"] == "JSON"
    assert cols["metadata"] == "JSON"


def test_only_referenced_tables_are_fetched():
    """A ten-connector deployment must not call ten APIs for one question."""
    s = QuerySession()
    try:
        refs = {t.qualified for t in s.referenced_tables(
            "SELECT * FROM shopify.orders WHERE currency = 'USD'"
        )}
        assert refs == {"shopify.orders"}
    finally:
        s.close()


def test_unknown_references_are_left_to_duckdb():
    s = QuerySession()
    try:
        assert s.referenced_tables("SELECT * FROM some.other") == []
    finally:
        s.close()


def test_ddl_quotes_identifiers():
    ddl = find("shopify.orders").ddl()
    assert ddl.startswith('CREATE OR REPLACE TABLE "shopify"."orders"')
    assert '"net_payment_minor" BIGINT' in ddl


# ── statement guard ─────────────────────────────────────────────────

def test_reads_are_allowed():
    from okwan_query import check

    assert check("SELECT * FROM shopify.orders").startswith("SELECT")
    assert check("WITH x AS (SELECT 1) SELECT * FROM x").startswith("WITH")
    assert check("  SELECT 1;  ") == "SELECT 1"


@pytest.mark.parametrize("sql", [
    "CREATE TABLE evil AS SELECT 1",
    "DROP TABLE shopify.orders",
    "INSERT INTO shopify.orders VALUES (1)",
    "UPDATE shopify.orders SET name = 'x'",
    "DELETE FROM shopify.orders",
])
def test_writes_are_rejected(sql):
    from okwan_query import UnsafeStatement, check

    with pytest.raises(UnsafeStatement):
        check(sql)


@pytest.mark.parametrize("sql", [
    "COPY (SELECT 1) TO '/tmp/out.csv'",
    "INSTALL httpfs",
    "ATTACH '/etc/shadow' AS s",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM glob('/**')",
])
def test_filesystem_escapes_are_rejected(sql):
    """readOnlyHint would be a false claim if these got through."""
    from okwan_query import UnsafeStatement, check

    with pytest.raises(UnsafeStatement):
        check(sql)


def test_statement_chaining_is_rejected():
    from okwan_query import UnsafeStatement, check

    with pytest.raises(UnsafeStatement, match="multiple statements"):
        check("SELECT 1; DROP TABLE x")


def test_comments_cannot_hide_a_second_statement():
    from okwan_query import UnsafeStatement, check

    with pytest.raises(UnsafeStatement):
        check("SELECT 1 -- ok\n; DROP TABLE x")


def test_empty_statement_is_rejected():
    from okwan_query import UnsafeStatement, check

    with pytest.raises(UnsafeStatement, match="empty"):
        check("   -- just a comment\n  ")


def test_query_tool_reports_guard_errors_as_data():
    """An agent gets a usable error, not an exception."""
    import asyncio

    from okwan_query.mcp import _query_tool

    result = asyncio.run(_query_tool(100)(sql="DROP TABLE x"))
    assert "error" in result
    assert result["row_count"] == 0


def test_catalog_payload_names_every_table():
    from okwan_query.mcp import catalog_payload

    payload = catalog_payload()
    names = {t["name"] for t in payload["tables"]}
    assert "shopify.orders" in names
    assert all(t["columns"] for t in payload["tables"])


def test_declared_tables_ship_with_the_server():
    """A named SQL query needs its shape stated; a resource does not."""
    import okwan_query.declarations  # noqa: F401

    t = find("rail.payments")
    assert t.connector == "rail"
    assert dict(t.columns)["amount"] == "BIGINT"
    assert "recon_payments" in t.params["sql"]


# ── reachability ────────────────────────────────────────────────────

def test_catalog_flags_unconfigured_tables():
    """An agent must be able to tell configured from unconfigured."""
    from okwan_query.mcp import catalog_payload

    payload = catalog_payload()
    assert "queryable_count" in payload
    assert all("queryable" in t for t in payload["tables"])
    assert all("missing_credentials" in t for t in payload["tables"])


def test_missing_credentials_names_the_field():
    from okwan_query.catalog import find, missing_credentials

    def nothing_configured(name, fields):
        return {f: "" for f in fields}

    missing = missing_credentials(find("stripe.charges"), nothing_configured)
    assert "secret_key" in missing


def test_configured_table_reports_no_missing():
    from okwan_query.catalog import find, missing_credentials

    def all_configured(name, fields):
        return {f: "x" for f in fields}

    assert missing_credentials(find("stripe.charges"), all_configured) == ()


def test_query_fails_before_calling_an_unconfigured_upstream():
    """Fail with a reason, not a timeout or an opaque 401."""
    import asyncio

    from okwan_core import CredentialError
    from okwan_query import QuerySession

    def nothing_configured(name, fields):
        return {f: "" for f in fields}

    s = QuerySession(resolver=nothing_configured)
    try:
        with pytest.raises(CredentialError, match="not configured"):
            asyncio.run(s.query("SELECT * FROM stripe.charges"))
    finally:
        s.close()
