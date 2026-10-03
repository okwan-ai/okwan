"""GET /v1/connectors states each connector's generated SQL tables.

The dashboard's catalog shows a connector's three surfaces (REST routes,
SQL tables, MCP tools) from this one listing, so the tables it names must
be the ones the query layer serves: derived from the same catalog, with
the same exclusions, not a second list.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from okwan_query.catalog import catalog


def _listed() -> dict[str, dict]:
    from okwan_api.main import app

    return {c["name"]: c for c in TestClient(app).get("/v1/connectors").json()}


def test_sql_tables_match_the_query_catalog():
    # The listing first: importing the app is what registers the connectors.
    listed = {name for c in _listed().values() for name in c["sql_tables"]}
    served = {f"{t.connector}.{t.resource}" for t in catalog() if t.connector != "rail"}
    assert listed == served


def test_every_table_is_one_of_the_connectors_resources():
    for name, c in _listed().items():
        for table in c["sql_tables"]:
            connector, resource = table.split(".", 1)
            assert connector == name
            assert resource in c["resources"]


def test_postgres_raw_sql_is_not_a_table():
    pg = _listed()["postgres"]
    assert "postgres.sql" not in pg["sql_tables"]
    assert "postgres.rows" not in pg["sql_tables"]


def test_stripe_charges_is_a_table():
    assert "stripe.charges" in _listed()["stripe"]["sql_tables"]
