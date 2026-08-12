"""Stripe connector — Okwan connector #3, completing the P0 trio.

Read path over the Stripe API: customers, charges, subscriptions,
balance. First connector to use the SDK's standard cursor pagination
(CursorPage), which maps directly onto Stripe's starting_after model.
Write operations (create customer, refunds...) arrive in P1 behind
explicit WRITE annotations.
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
    Charge,
    ChargePage,
    Customer,
    CustomerPage,
    GetBalanceIn,
    GetCustomerIn,
    ListChargesIn,
    ListCustomersIn,
    ListSubscriptionsIn,
    Subscription,
    SubscriptionPage,
)

stripe = register(
    Connector(
        name="stripe",
        version="0.1.0",
        description=(
            "Stripe: read customers, charges, subscriptions, and account "
            "balance. Amounts are minor units (cents)."
        ),
        base_url="https://api.stripe.com/v1",
        auth=BearerTokenAuth(required_fields=("secret_key",)),
        rate_limit=RateLimitProfile(requests_per_second=20, burst=10),
        docs_url="https://docs.stripe.com/api",
    )
)

customers = stripe.resource("customers", schema=Customer, description="Stripe customers")
charges = stripe.resource("charges", schema=Charge, description="Payment charges")
subscriptions = stripe.resource(
    "subscriptions", schema=Subscription, description="Recurring subscriptions"
)
balance = stripe.resource("balance", schema=Balance, description="Account balance")


def _page_params(limit: int, cursor: str | None, **extra: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["starting_after"] = cursor
    params.update({k: v for k, v in extra.items() if v is not None})
    return params


def _page(data: dict[str, Any], model: type) -> tuple[list[Any], str | None, bool]:
    items = [model.model_validate(obj) for obj in data.get("data", [])]
    has_more = bool(data.get("has_more"))
    next_cursor = items[-1].id if (has_more and items) else None
    return items, next_cursor, has_more


@customers.operation(
    OpType.LIST,
    input_model=ListCustomersIn,
    output_model=CustomerPage,
    description="List customers, newest first, with cursor pagination.",
)
async def list_customers(ctx: ConnectorContext, params: ListCustomersIn) -> CustomerPage:
    data = await ctx.client.get(
        "/customers",
        params=_page_params(params.limit, params.cursor, email=params.email),
    )
    items, cursor, more = _page(data, Customer)
    return CustomerPage(items=items, next_cursor=cursor, has_more=more)


@customers.operation(
    OpType.GET,
    input_model=GetCustomerIn,
    output_model=Customer,
    description="Fetch one customer by ID.",
)
async def get_customer(ctx: ConnectorContext, params: GetCustomerIn) -> Customer:
    data = await ctx.client.get(f"/customers/{params.customer_id}")
    return Customer.model_validate(data)


@charges.operation(
    OpType.LIST,
    input_model=ListChargesIn,
    output_model=ChargePage,
    description=(
        "List charges, newest first. Filter by customer to compute "
        "per-customer revenue; sum `amount` where status=succeeded "
        "and refunded=false."
    ),
)
async def list_charges(ctx: ConnectorContext, params: ListChargesIn) -> ChargePage:
    data = await ctx.client.get(
        "/charges",
        params=_page_params(params.limit, params.cursor, customer=params.customer_id),
    )
    items, cursor, more = _page(data, Charge)
    return ChargePage(items=items, next_cursor=cursor, has_more=more)


@subscriptions.operation(
    OpType.LIST,
    input_model=ListSubscriptionsIn,
    output_model=SubscriptionPage,
    description="List subscriptions; status=all includes canceled.",
)
async def list_subscriptions(
    ctx: ConnectorContext, params: ListSubscriptionsIn
) -> SubscriptionPage:
    data = await ctx.client.get(
        "/subscriptions",
        params=_page_params(params.limit, params.cursor, status=params.status),
    )
    items, cursor, more = _page(data, Subscription)
    return SubscriptionPage(items=items, next_cursor=cursor, has_more=more)


@balance.operation(
    OpType.GET,
    input_model=GetBalanceIn,
    output_model=Balance,
    description="Current account balance: available and pending funds.",
)
async def get_balance(ctx: ConnectorContext, params: GetBalanceIn) -> Balance:
    data = await ctx.client.get("/balance")
    return Balance.model_validate(data)
