"""Shopify connector — Okwan connector #5, first order-ledger source.

Read path over the GraphQL Admin API: orders and products. Shopify is
the merchant's system of record, so it sits on the `right` side of a
reconciliation — the thing a payment rail is checked against.

Two things differ from the REST connectors:

* Transport. The Admin API is one POST endpoint per shop and the shop
  domain is part of the URL, so `base_url` cannot be static. A
  `context_factory` builds the client per credential set, the same seam
  Postgres uses for a non-HTTP transport.
* Auth. `X-Shopify-Access-Token`, not a bearer header.

Pagination needs no translation: Shopify's `pageInfo.endCursor` is
already the opaque cursor `CursorPage` expects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from okwan_core import (
    AuthAdapter,
    Connector,
    ConnectorContext,
    OkwanClient,
    OpType,
    RateLimitProfile,
    UpstreamError,
    register,
)

from .schemas import (
    GetOrderIn,
    ListOrdersIn,
    ListProductsIn,
    Order,
    OrderPage,
    Product,
    ProductPage,
    money_to_minor,
)

API_VERSION = "2026-07"


@dataclass(frozen=True, slots=True)
class ShopifyAuth(AuthAdapter):
    """Access token header plus the shop domain, which selects the host.

    The domain is a credential in the SDK sense — without it there is no
    endpoint to call — even though it is not itself a secret. Two fields
    where only one is the header value, so this cannot reuse ApiKeyAuth,
    which binds required_fields[0].
    """

    header: str = "X-Shopify-Access-Token"
    required_fields: tuple[str, ...] = ("access_token", "shop_domain")

    def bind(self, credentials: dict[str, str]) -> httpx.Auth:
        self.validate(credentials)
        return _ShopifyTokenAuth(self.header, credentials["access_token"])


class _ShopifyTokenAuth(httpx.Auth):
    def __init__(self, header: str, token: str) -> None:
        self._header, self._token = header, token

    def auth_flow(self, request: httpx.Request):
        request.headers[self._header] = self._token
        yield request


def _shopify_context(
    connector: Connector, credentials: dict[str, str]
) -> ConnectorContext:
    connector.auth.validate(credentials)
    domain = credentials["shop_domain"].strip().rstrip("/")
    domain = domain.removeprefix("https://").removeprefix("http://")
    client = OkwanClient(
        base_url=f"https://{domain}/admin/api/{API_VERSION}",
        auth=connector.auth.bind(credentials),
        rate_limit=connector.rate_limit,
    )
    return ConnectorContext(client=client, credentials=credentials)


shopify = register(
    Connector(
        name="shopify",
        version="0.1.0",
        description=(
            "Shopify: read orders and products from a merchant store via the "
            "GraphQL Admin API. Orders expose gross, refunded and net amounts "
            "separately in integer minor units — net payment is the figure to "
            "reconcile a payment rail against."
        ),
        base_url="",  # per-shop; built by the context factory
        auth=ShopifyAuth(),
        rate_limit=RateLimitProfile(requests_per_second=2, burst=4),
        docs_url="https://shopify.dev/docs/api/admin-graphql",
        context_factory=_shopify_context,
    )
)

orders = shopify.resource("orders", schema=Order, description="Merchant orders")
products = shopify.resource("products", schema=Product, description="Store products")


async def _graphql(
    ctx: ConnectorContext, document: str, **variables: Any
) -> dict[str, Any]:
    """Execute one GraphQL document and surface errors as UpstreamError.

    GraphQL returns HTTP 200 with an `errors` array, so a transport-level
    check alone would silently treat a permission failure as success.
    """
    payload = await ctx.client.post(
        "/graphql.json",
        json={
            "query": document,
            "variables": {k: v for k, v in variables.items() if v is not None},
        },
    )
    errors = payload.get("errors")
    if errors:
        messages = "; ".join(e.get("message", str(e)) for e in errors)
        code = (errors[0].get("extensions") or {}).get("code", "")
        status = 403 if code == "ACCESS_DENIED" else 400
        raise UpstreamError(status=status, body=f"shopify graphql: {messages}")
    return payload.get("data") or {}


def _money(node: dict[str, Any], key: str, currency: str) -> int:
    amount = ((node.get(key) or {}).get("shopMoney") or {}).get("amount")
    return money_to_minor(amount, currency)


def _order(node: dict[str, Any]) -> Order:
    currency = (
        ((node.get("currentTotalPriceSet") or {}).get("shopMoney") or {}).get(
            "currencyCode"
        )
        or "USD"
    )
    return Order(
        id=node["id"],
        name=node["name"],
        currency=currency,
        created_at=node.get("createdAt"),
        financial_status=node.get("displayFinancialStatus"),
        fulfillment_status=node.get("displayFulfillmentStatus"),
        total_price_minor=_money(node, "currentTotalPriceSet", currency),
        total_received_minor=_money(node, "totalReceivedSet", currency),
        total_refunded_minor=_money(node, "totalRefundedSet", currency),
    )


_ORDER_FIELDS = """
  id name createdAt
  displayFinancialStatus
  displayFulfillmentStatus
  currentTotalPriceSet { shopMoney { amount currencyCode } }
  totalReceivedSet     { shopMoney { amount } }
  totalRefundedSet     { shopMoney { amount } }
"""

_LIST_ORDERS = """
query ListOrders($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT) {
    edges { node { %s } }
    pageInfo { hasNextPage endCursor }
  }
}
""" % _ORDER_FIELDS

_GET_ORDER = """
query GetOrder($id: ID!) { order(id: $id) { %s } }
""" % _ORDER_FIELDS

_LIST_PRODUCTS = """
query ListProducts($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    edges { node { id title handle status totalInventory createdAt } }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _search_query(params: ListOrdersIn) -> str | None:
    """Build Shopify's search-syntax filter from typed inputs."""
    clauses: list[str] = []
    if params.financial_status:
        clauses.append(f"financial_status:{params.financial_status}")
    if params.created_at_min:
        clauses.append(f"created_at:>='{params.created_at_min.isoformat()}'")
    return " AND ".join(clauses) or None


def _page(connection: dict[str, Any], build) -> tuple[list[Any], str | None, bool]:
    edges = connection.get("edges") or []
    items = [build(e["node"]) for e in edges]
    info = connection.get("pageInfo") or {}
    has_more = bool(info.get("hasNextPage"))
    return items, (info.get("endCursor") if has_more else None), has_more


@orders.operation(
    OpType.LIST,
    input_model=ListOrdersIn,
    output_model=OrderPage,
    description=(
        "List orders oldest first. `name` (#1001) is the merchant-facing "
        "reference to match against a payment rail. Reconcile on "
        "`net_payment_minor`, not `total_received_minor`: a refunded order "
        "matched on gross looks like an over-collection."
    ),
)
async def list_orders(ctx: ConnectorContext, params: ListOrdersIn) -> OrderPage:
    data = await _graphql(
        ctx,
        _LIST_ORDERS,
        first=params.limit,
        after=params.cursor,
        query=_search_query(params),
    )
    items, cursor, more = _page(data.get("orders") or {}, _order)
    return OrderPage(items=items, next_cursor=cursor, has_more=more)


@orders.operation(
    OpType.GET,
    input_model=GetOrderIn,
    output_model=Order,
    description="Fetch one order by GID or numeric ID.",
)
async def get_order(ctx: ConnectorContext, params: GetOrderIn) -> Order:
    gid = params.order_id
    if not gid.startswith("gid://"):
        gid = f"gid://shopify/Order/{gid}"
    data = await _graphql(ctx, _GET_ORDER, id=gid)
    node = data.get("order")
    if not node:
        raise UpstreamError(status=404, body=f"order {params.order_id} not found")
    return _order(node)


@products.operation(
    OpType.LIST,
    input_model=ListProductsIn,
    output_model=ProductPage,
    description="List products in the store catalog.",
)
async def list_products(ctx: ConnectorContext, params: ListProductsIn) -> ProductPage:
    data = await _graphql(
        ctx, _LIST_PRODUCTS, first=params.limit, after=params.cursor
    )
    items, cursor, more = _page(
        data.get("products") or {},
        lambda n: Product(
            id=n["id"],
            title=n["title"],
            handle=n.get("handle"),
            status=n.get("status"),
            total_inventory=n.get("totalInventory"),
            created_at=n.get("createdAt"),
        ),
    )
    return ProductPage(items=items, next_cursor=cursor, has_more=more)
