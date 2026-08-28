"""Tables declared over a raw SQL statement.

A connector resource carries its own schema, so the catalog derives it.
A named query against a database does not — the shape is only knowable
if someone states it. That is the whole difference between the two, and
it is why Postgres appears here rather than in the derived catalog.

Importing this module registers them, the same pattern the reconciliation
declarations use.
"""
from __future__ import annotations

from .catalog import declare_sql_table

#: The payment rail's own record of what it collected, standing in for a
#: processor feed. Reconciling this against shopify.orders is the
#: cross-system case the platform exists for.
payments = declare_sql_table(
    "payments",
    "SELECT payment_id, reference, amount, currency, status, created_at "
    "FROM recon_payments WHERE status = 'success'",
    [
        ("payment_id", "VARCHAR"),
        ("reference", "VARCHAR"),
        ("amount", "BIGINT"),
        ("currency", "VARCHAR"),
        ("status", "VARCHAR"),
        ("created_at", "TIMESTAMP"),
    ],
)

__all__ = ["payments"]
