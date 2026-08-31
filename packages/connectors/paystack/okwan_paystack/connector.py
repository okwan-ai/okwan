"""Paystack connector — Okwan connector #4, first African payment rail.

Read path: transactions, settlements, settlement transactions,
customers, balance. Paystack pages by page number rather than by
cursor, so `_page` encodes the next page as an opaque cursor string.
Callers, MCP tools and REST routes all keep the SDK-standard
CursorPage contract and never learn that this rail pages differently.
"""
from __future__ import annotations

from typing import Any

from okwan_core import (
    BearerTokenAuth,
    Connector,
    ConnectorContext,
    OpType,
    RateLimitProfile,
    register,
)

from .schemas import (
    Balance,
    BalanceEntry,
    Customer,
    CustomerPage,
    GetBalanceIn,
    GetCustomerIn,
    GetTransactionIn,
    ListCustomersIn,
    ListSettlementsIn,
    ListSettlementTransactionsIn,
    ListTransactionsIn,
    Settlement,
    SettlementPage,
    Transaction,
    TransactionPage,
)

paystack = register(
    Connector(
        name="paystack",
        version="0.1.0",
        description=(
            "Paystack: read transactions, settlements, settlement "
            "transactions, customers and balances across NGN, GHS, ZAR, "
            "KES, USD and XOF. Amounts are minor units; each amount also "
            "exposes an `*_major` field that is subunit-correct for "
            "zero-decimal currencies such as XOF."
        ),
        base_url="https://api.paystack.co",
        auth=BearerTokenAuth(required_fields=("secret_key",)),
        rate_limit=RateLimitProfile(requests_per_second=10, burst=5),
        docs_url="https://paystack.com/docs/api",
    )
)

transactions = paystack.resource(
    "transactions", schema=Transaction, description="Payment transactions"
)
settlements = paystack.resource(
    "settlements", schema=Settlement, description="Payouts to the merchant bank account"
)
settlement_transactions = paystack.resource(
    "settlement_transactions",
    schema=Transaction,
    description="Transactions comprising one settlement",
)
customers = paystack.resource(
    "customers", schema=Customer, description="Paystack customers"
)
balance = paystack.resource(
    "balance", schema=Balance, description="Available balance per currency"
)


def _page_params(limit: int, cursor: str | None, **extra: Any) -> dict[str, Any]:
    """Translate the SDK's cursor contract into Paystack page numbers."""
    page = 1
    if cursor:
        try:
            page = max(1, int(cursor))
        except ValueError:
            page = 1
    params: dict[str, Any] = {"perPage": limit, "page": page}
    params.update({k: v for k, v in extra.items() if v is not None})
    return params


def _unwrap(payload: dict[str, Any]) -> Any:
    """Every Paystack response nests the payload under `data`."""
    return payload.get("data")


def _page(payload: dict[str, Any], model: type) -> tuple[list[Any], str | None, bool]:
    rows = _unwrap(payload) or []
    items = [model.model_validate(obj) for obj in rows]
    meta = payload.get("meta") or {}
    try:
        current = int(meta.get("page", 1) or 1)
        total_pages = int(meta.get("pageCount", 1) or 1)
    except (TypeError, ValueError):
        current, total_pages = 1, 1
    has_more = current < total_pages
    next_cursor = str(current + 1) if has_more else None
    return items, next_cursor, has_more


@transactions.operation(
    OpType.LIST,
    input_model=ListTransactionsIn,
    output_model=TransactionPage,
    description=(
        "List transactions, newest first. Sum `amount` where "
        "status=success for gross volume; `reference` is the key to "
        "match against merchant order records."
    ),
)
async def list_transactions(
    ctx: ConnectorContext, params: ListTransactionsIn
) -> TransactionPage:
    data = await ctx.client.get(
        "/transaction",
        params=_page_params(
            params.limit,
            params.cursor,
            status=params.status,
            customer=params.customer_id,
        ),
    )
    items, cursor, more = _page(data, Transaction)
    return TransactionPage(items=items, next_cursor=cursor, has_more=more)


@transactions.operation(
    OpType.GET,
    input_model=GetTransactionIn,
    output_model=Transaction,
    description="Fetch one transaction by its Paystack numeric ID.",
)
async def get_transaction(
    ctx: ConnectorContext, params: GetTransactionIn
) -> Transaction:
    data = await ctx.client.get(f"/transaction/{params.transaction_id}")
    return Transaction.model_validate(_unwrap(data))


@settlements.operation(
    OpType.LIST,
    input_model=ListSettlementsIn,
    output_model=SettlementPage,
    description=(
        "List settlements (payouts to the merchant bank account). "
        "`effective_amount` is net of fees; compare against the sum of "
        "the settlement's transactions to detect shortfalls."
    ),
)
async def list_settlements(
    ctx: ConnectorContext, params: ListSettlementsIn
) -> SettlementPage:
    data = await ctx.client.get(
        "/settlement",
        params=_page_params(params.limit, params.cursor, status=params.status),
    )
    items, cursor, more = _page(data, Settlement)
    return SettlementPage(items=items, next_cursor=cursor, has_more=more)


@settlement_transactions.operation(
    OpType.LIST,
    input_model=ListSettlementTransactionsIn,
    output_model=TransactionPage,
    description=(
        "List the transactions that make up one settlement — the "
        "payout-to-transaction breakdown used for reconciliation."
    ),
)
async def list_settlement_transactions(
    ctx: ConnectorContext, params: ListSettlementTransactionsIn
) -> TransactionPage:
    data = await ctx.client.get(
        f"/settlement/{params.settlement_id}/transactions",
        params=_page_params(params.limit, params.cursor),
    )
    items, cursor, more = _page(data, Transaction)
    return TransactionPage(items=items, next_cursor=cursor, has_more=more)


@customers.operation(
    OpType.LIST,
    input_model=ListCustomersIn,
    output_model=CustomerPage,
    description="List customers, newest first.",
)
async def list_customers(
    ctx: ConnectorContext, params: ListCustomersIn
) -> CustomerPage:
    data = await ctx.client.get(
        "/customer", params=_page_params(params.limit, params.cursor)
    )
    items, cursor, more = _page(data, Customer)
    return CustomerPage(items=items, next_cursor=cursor, has_more=more)


@customers.operation(
    OpType.GET,
    input_model=GetCustomerIn,
    output_model=Customer,
    description="Fetch one customer by customer code or email address.",
)
async def get_customer(ctx: ConnectorContext, params: GetCustomerIn) -> Customer:
    data = await ctx.client.get(f"/customer/{params.customer_code}")
    return Customer.model_validate(_unwrap(data))


@balance.operation(
    OpType.GET,
    input_model=GetBalanceIn,
    output_model=Balance,
    description="Current available balance, one entry per currency.",
)
async def get_balance(ctx: ConnectorContext, params: GetBalanceIn) -> Balance:
    data = await ctx.client.get("/balance")
    rows = _unwrap(data) or []
    return Balance(balances=[BalanceEntry.model_validate(r) for r in rows])
