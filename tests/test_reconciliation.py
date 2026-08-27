from __future__ import annotations

import pytest

from okwan_core.currency import to_minor
from okwan_recon import ExactRef, Fuzzy, MSISDN, Reconciliation, ResourceRef, match
from okwan_recon.emitters.mcp import tool_metadata

SPEC = Reconciliation(
    name="paystack_settlement",
    description="Match Paystack transactions against merchant order records.",
    left=ResourceRef(connector="paystack", resource="transactions", operation="list"),
    right=ResourceRef(connector="postgres", resource="sql", operation="query"),
    keys=[
        ExactRef(left="reference", right="order_ref"),
        Fuzzy(amount="amount", currency="currency", window="48h"),
    ],
    identity=MSISDN(left="customer.phone", right="phone", default_country_code="233"),
)

LEFT = [
    {"id": 1, "reference": "ORD-1", "amount": 250000, "currency": "NGN",
     "created_at": "2026-08-01T10:00:00Z", "customer": {"phone": "+233 24 111 2222"}},
    {"id": 2, "reference": "PSK-XYZ", "amount": 5000, "currency": "XOF",
     "created_at": "2026-08-01T11:00:00Z", "customer": {"phone": "0244445555"}},
    {"id": 3, "reference": "PSK-ORPHAN", "amount": 9999, "currency": "NGN",
     "created_at": "2026-08-01T12:00:00Z", "customer": {"phone": None}},
]

RIGHT = [
    {"order_ref": "ORD-1", "amount": 250000, "currency": "NGN",
     "created_at": "2026-08-01T09:58:00Z", "phone": "233241112222"},
    {"order_ref": "ORD-2", "amount": 5000, "currency": "XOF",
     "created_at": "2026-08-01T11:20:00Z", "phone": "233244445555"},
    {"order_ref": "ORD-9", "amount": 750, "currency": "NGN",
     "created_at": "2026-08-01T12:05:00Z", "phone": "233200000000"},
]


def test_exact_rule_wins_before_fuzzy_sees_the_record():
    result = match(SPEC, LEFT, RIGHT)
    by_rule = {p.rule: p for p in result.matched}
    assert by_rule["exact_ref"].left["reference"] == "ORD-1"
    assert by_rule["exact_ref"].confidence == 1.0


def test_fuzzy_fallback_pairs_xof_without_scaling():
    result = match(SPEC, LEFT, RIGHT)
    pair = next(p for p in result.matched if p.rule == "fuzzy")
    assert pair.left["reference"] == "PSK-XYZ"
    assert pair.right["order_ref"] == "ORD-2"


def test_unmatched_reported_on_both_sides():
    result = match(SPEC, LEFT, RIGHT)
    assert result.summary["matched"] == 2
    assert result.summary["unmatched_left"] == 1
    assert result.summary["unmatched_right"] == 1
    assert result.unmatched_left[0]["reference"] == "PSK-ORPHAN"


def test_zero_decimal_currency_not_scaled():
    assert to_minor(5000, "XOF") == 5000
    assert to_minor(2500, "NGN") == 250000


def test_identity_guard_blocks_mismatched_phones():
    left = [{"reference": "A", "amount": 100, "currency": "XOF",
             "created_at": "2026-08-01T10:00:00Z", "customer": {"phone": "233240000001"}}]
    right = [{"order_ref": "B", "amount": 100, "currency": "XOF",
              "created_at": "2026-08-01T10:01:00Z", "phone": "233249999999"}]
    result = match(SPEC, left, right)
    assert result.summary["matched"] == 0


def test_tool_metadata_derived_from_declaration():
    meta = tool_metadata(SPEC)
    assert meta["tool_name"] == "reconcile_paystack_settlement"
    assert meta["view"] == "recon.paystack_settlement"
    assert meta["read_only"] is True
    assert meta["rules"] == ["exact_ref", "fuzzy"]


def test_valid_declaration_resolves_against_the_registry():
    import okwan_paystack.connector  # noqa: F401
    import okwan_postgres.connector  # noqa: F401

    SPEC.validate_against_registry()


def test_unknown_resource_is_rejected():
    import okwan_paystack.connector  # noqa: F401

    bad = SPEC.model_copy(
        update={"left": ResourceRef(connector="paystack", resource="nope")}
    )
    with pytest.raises(ValueError, match="no resource"):
        bad.validate_against_registry()


def test_write_operation_is_structurally_rejected():
    """A reconciliation may not be declared over a mutating operation."""
    import okwan_whatsapp.connector  # noqa: F401

    bad = SPEC.model_copy(
        update={
            "right": ResourceRef(
                connector="whatsapp", resource="messages", operation="send_text"
            )
        }
    )
    with pytest.raises(ValueError, match="write operation"):
        bad.validate_against_registry()


def test_duckdb_view_materializes_from_same_declaration():
    duckdb = pytest.importorskip("duckdb")
    from okwan_recon.emitters.duckdb_view import materialize_view

    result = match(SPEC, LEFT, RIGHT)
    con = duckdb.connect(":memory:")
    assert materialize_view(con, SPEC, result) == "recon.paystack_settlement"
    counts = dict(
        con.execute(
            'SELECT status, count(*) FROM "recon"."paystack_settlement" GROUP BY 1'
        ).fetchall()
    )
    assert counts == {"matched": 2, "unmatched_left": 1, "unmatched_right": 1}


def test_metadata_separates_registry_key_from_tool_name():
    """REST clients navigate by `name`; MCP registers by `tool_name`."""
    meta = tool_metadata(SPEC)
    assert meta["name"] == "paystack_settlement"
    assert meta["tool_name"] == "reconcile_paystack_settlement"
    assert meta["path"] == "/v1/reconciliations/paystack_settlement"


def test_listed_names_are_routable():
    import okwan_recon.declarations  # noqa: F401
    from okwan_recon import all_reconciliations, get

    for spec in all_reconciliations():
        assert get(tool_metadata(spec)["name"]) is spec
