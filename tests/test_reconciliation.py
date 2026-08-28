from __future__ import annotations

import pytest

from okwan_core.currency import to_minor
from okwan_recon import (
    AmountRef, ExactRef, Fuzzy, MSISDN, Reconciliation, ResourceRef, match,
)
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
    identity=MSISDN(left="customer.phone", right="phone", country_codes=("233",)),
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


def test_msisdn_expands_national_format_per_country():
    """A leading-zero number is ambiguous; keep every candidate."""
    spec = MSISDN(left="phone", country_codes=("233", "225"))
    assert {"233505060708", "225505060708"} <= spec.candidates("0505060708")
    assert spec.candidates("+233 24 111 2222") == {"233241112222"}
    assert spec.candidates(None) == frozenset()


def test_msisdn_agreement_is_three_valued():
    spec = MSISDN(left="phone", country_codes=("233", "225"))
    assert spec.agrees("0505060708", "225505060708") is True
    assert spec.agrees("233241112222", "233209999999") is False
    assert spec.agrees(None, "233241112222") is None


def test_absent_phone_does_not_block_a_fuzzy_match():
    spec = SPEC.model_copy(update={"keys": [Fuzzy(amount="amount", currency="currency")]})
    left = [{"amount": 100, "currency": "XOF", "created_at": "2026-08-01T10:00:00Z"}]
    right = [{"order_ref": "B", "amount": 100, "currency": "XOF",
              "phone": "233240000001", "created_at": "2026-08-01T10:01:00Z"}]
    assert match(spec, left, right).summary["matched"] == 1


def test_msisdn_international_dial_prefix_is_already_e164():
    """00225... is an E.164 number written with a dial-out prefix."""
    spec = MSISDN(left="phone", country_codes=("233", "225"))
    assert "225050607080" in spec.candidates("00225050607080")
    assert spec.agrees("0022505060708", "22505060708") is True


def test_msisdn_trunk_prefix_expands_not_truncates():
    spec = MSISDN(left="phone", country_codes=("225",))
    # 00-prefixed digits are ambiguous: both readings are kept.
    assert spec.candidates("005060708") >= {"5060708", "22505060708"}
    assert spec.agrees("005060708", "22505060708") is True


# ── connector amount parity ─────────────────────────────────────────

def test_stripe_and_paystack_expose_the_same_amount_shape():
    """Both rails must report major amounts, subunit-correct per currency."""
    from okwan_paystack.schemas import Transaction
    from okwan_stripe.schemas import Charge

    charge = Charge(id="ch_1", amount=250000, currency="NGN",
                    status="succeeded", created=0)
    txn = Transaction(id=1, reference="ORD-1", amount=250000,
                      currency="NGN", status="success")
    assert charge.amount_major == txn.amount_major == 2500.0


def test_stripe_zero_decimal_currency_not_scaled():
    from okwan_stripe.schemas import Charge

    charge = Charge(id="ch_2", amount=5000, currency="XOF",
                    status="succeeded", created=0)
    assert charge.amount_major == 5000.0
    assert charge.model_dump()["amount_major"] == 5000.0


def test_stripe_balance_entries_are_typed():
    from okwan_stripe.schemas import Balance

    b = Balance.model_validate({
        "available": [{"currency": "usd", "amount": 12345}],
        "pending": [{"currency": "xof", "amount": 5000}],
    })
    assert b.available[0].amount_major == 123.45
    assert b.pending[0].amount_major == 5000.0


# ── shopify money normalisation ─────────────────────────────────────

def test_shopify_money_uses_decimal_not_float():
    """299.10 * 100 is 29909.999... in binary float; money must not round down."""
    from okwan_shopify.schemas import money_to_minor

    assert money_to_minor("299.10", "USD") == 29910
    assert money_to_minor("0.07", "USD") == 7
    assert money_to_minor("1234.56", "USD") == 123456


def test_shopify_zero_decimal_currency_not_scaled():
    from okwan_shopify.schemas import money_to_minor

    assert money_to_minor("5000", "XOF") == 5000
    assert money_to_minor("5000", "USD") == 500000


def test_shopify_net_payment_excludes_refund():
    """A refunded order matched on gross looks like an over-collection."""
    from okwan_shopify.schemas import Order

    o = Order(
        id="gid://shopify/Order/1", name="#1001", currency="USD",
        total_price_minor=29900, total_received_minor=242300,
        total_refunded_minor=212400,
    )
    assert o.net_payment_minor == 29900
    assert o.net_payment_major == 299.00
    assert o.is_reconcilable is True


def test_shopify_uncollected_order_is_not_reconcilable():
    from okwan_shopify.schemas import Order

    o = Order(
        id="gid://shopify/Order/2", name="#1099", currency="USD",
        total_price_minor=5000, total_received_minor=0,
    )
    assert o.is_reconcilable is False


def test_shopify_amounts_are_comparable_with_paystack():
    """Both sides reduce to integer minor units of the same currency."""
    from okwan_paystack.schemas import Transaction
    from okwan_shopify.schemas import Order, money_to_minor

    order = Order(
        id="gid://shopify/Order/3", name="#1002", currency="USD",
        total_price_minor=money_to_minor("999.00", "USD"),
        total_received_minor=money_to_minor("999.00", "USD"),
    )
    txn = Transaction(id=9, reference="#1002", amount=99900,
                      currency="USD", status="success")
    assert order.net_payment_minor == txn.amount


def test_fuzzy_matches_across_differently_named_amount_fields():
    """Two systems rarely name the same concept identically."""
    spec = SPEC.model_copy(update={
        "keys": [Fuzzy(amount="amount", currency="currency",
                       amount_right="net_payment_minor")]
    })
    left = [{"payment_id": "P1", "amount": 45000, "currency": "USD",
             "created_at": "2026-08-01T10:00:00Z"}]
    right = [{"name": "#1004", "net_payment_minor": 45000, "currency": "USD",
              "created_at": "2026-08-01T10:30:00Z"}]
    assert match(spec, left, right).summary["matched"] == 1


def test_fuzzy_right_paths_default_to_left():
    rule = Fuzzy(amount="amount", currency="currency")
    assert rule.right_amount == "amount"
    assert rule.right_currency == "currency"


# ── discrepancy on matched pairs ────────────────────────────────────

def test_reference_matched_pair_with_wrong_money_is_flagged():
    """The rail holds a gross charge; the ledger nets out a refund."""
    spec = SPEC.model_copy(update={
        "keys": [ExactRef(left="reference", right="order_ref")],
        "amount": AmountRef(left="amount", right="net_minor"),
    })
    left = [{"reference": "#1001", "amount": 242300, "currency": "USD"}]
    right = [{"order_ref": "#1001", "net_minor": 29900, "currency": "USD"}]

    result = match(spec, left, right)
    pair = result.matched[0]
    assert pair.discrepancy_minor == 212400
    assert pair.agrees is False
    assert result.summary["matched"] == 1
    assert result.summary["matched_in_agreement"] == 0
    assert result.summary["matched_with_discrepancy"] == 1
    assert result.summary["net_discrepancy_minor"] == 212400


def test_clean_reference_match_agrees():
    spec = SPEC.model_copy(update={
        "keys": [ExactRef(left="reference", right="order_ref")],
        "amount": AmountRef(left="amount", right="net_minor"),
    })
    left = [{"reference": "#1002", "amount": 99900, "currency": "USD"}]
    right = [{"order_ref": "#1002", "net_minor": 99900, "currency": "USD"}]

    result = match(spec, left, right)
    assert result.matched[0].agrees is True
    assert result.summary["matched_with_discrepancy"] == 0


def test_cross_currency_pair_is_not_scored():
    """A difference between two currencies is not a number."""
    spec = SPEC.model_copy(update={
        "keys": [ExactRef(left="reference", right="order_ref")],
        "amount": AmountRef(left="amount", right="net_minor"),
    })
    left = [{"reference": "#1", "amount": 5000, "currency": "XOF"}]
    right = [{"order_ref": "#1", "net_minor": 5000, "currency": "USD"}]

    pair = match(spec, left, right).matched[0]
    assert pair.discrepancy_minor is None
    assert pair.agrees is None


def test_discrepancy_defaults_to_fuzzy_paths():
    """A declaration with no AmountRef borrows the first Fuzzy rule's paths."""
    spec = SPEC.model_copy(update={
        "keys": [Fuzzy(amount="amount", currency="currency",
                       amount_right="net_minor")],
        "amount": None,
    })
    ref = spec.resolved_amount
    assert ref is not None
    assert ref.left == "amount"
    assert ref.right_path == "net_minor"


# ── ambiguity ───────────────────────────────────────────────────────

def test_two_equal_candidates_are_unresolved_not_matched():
    """A match the system is not entitled to is worse than no match."""
    spec = SPEC.model_copy(update={
        "keys": [Fuzzy(amount="amount", currency="currency")],
        "amount": None,
    })
    left = [{"payment_id": "P1", "amount": 45000, "currency": "USD",
             "created_at": "2026-08-01T10:00:00Z"}]
    right = [
        {"order_ref": "A", "amount": 45000, "currency": "USD",
         "created_at": "2026-08-01T10:05:00Z"},
        {"order_ref": "B", "amount": 45000, "currency": "USD",
         "created_at": "2026-08-01T22:00:00Z"},
    ]

    result = match(spec, left, right)
    assert result.summary["matched"] == 0
    assert result.summary["ambiguous"] == 1
    assert {r["order_ref"] for r in result.ambiguous[0].candidates} == {"A", "B"}


def test_ambiguous_candidates_are_not_consumed():
    """Nothing claimed them, so they still appear as unmatched."""
    spec = SPEC.model_copy(update={
        "keys": [Fuzzy(amount="amount", currency="currency")],
        "amount": None,
    })
    left = [{"payment_id": "P1", "amount": 45000, "currency": "USD",
             "created_at": "2026-08-01T10:00:00Z"}]
    right = [
        {"order_ref": "A", "amount": 45000, "currency": "USD",
         "created_at": "2026-08-01T10:05:00Z"},
        {"order_ref": "B", "amount": 45000, "currency": "USD",
         "created_at": "2026-08-01T11:00:00Z"},
    ]

    result = match(spec, left, right)
    assert result.summary["unmatched_right"] == 2


def test_single_candidate_still_matches():
    spec = SPEC.model_copy(update={
        "keys": [Fuzzy(amount="amount", currency="currency")],
        "amount": None,
    })
    left = [{"payment_id": "P1", "amount": 45000, "currency": "USD",
             "created_at": "2026-08-01T10:00:00Z"}]
    right = [{"order_ref": "A", "amount": 45000, "currency": "USD",
              "created_at": "2026-08-01T10:05:00Z"}]

    result = match(spec, left, right)
    assert result.summary["matched"] == 1
    assert result.summary["ambiguous"] == 0


def test_exact_reference_resolves_what_amount_alone_cannot():
    """The reference is why duplicate amounts are still reconcilable."""
    spec = SPEC.model_copy(update={
        "keys": [ExactRef(left="reference", right="order_ref"),
                 Fuzzy(amount="amount", currency="currency")],
        "amount": None,
    })
    left = [
        {"payment_id": "P1", "reference": "A", "amount": 45000, "currency": "USD",
         "created_at": "2026-08-01T10:00:00Z"},
        {"payment_id": "P2", "reference": "B", "amount": 45000, "currency": "USD",
         "created_at": "2026-08-01T10:01:00Z"},
    ]
    right = [
        {"order_ref": "A", "amount": 45000, "currency": "USD",
         "created_at": "2026-08-01T10:05:00Z"},
        {"order_ref": "B", "amount": 45000, "currency": "USD",
         "created_at": "2026-08-01T10:06:00Z"},
    ]

    result = match(spec, left, right)
    assert result.summary["matched"] == 2
    assert result.summary["ambiguous"] == 0
