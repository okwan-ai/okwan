"""Federated SQL over connector definitions."""
from __future__ import annotations

import pytest

import okwan_paystack.connector  # noqa: F401
import okwan_postgres.connector  # noqa: F401
import okwan_shopify.connector   # noqa: F401
import okwan_stripe.connector    # noqa: F401
from okwan_query import QuerySession, catalog, columns_for, find
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
