"""Okwan core SDK — the one-definition rule lives here.

A Connector is defined once. From that single definition the platform
auto-generates: (a) REST endpoints, (b) SQL-queryable tables (v2),
(c) MCP tool definitions. Nothing may bypass this contract.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from .auth import AuthAdapter
from .client import OkwanClient, RateLimitProfile


class OpType(StrEnum):
    LIST = "list"
    GET = "get"
    SEARCH = "search"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


#: Operations that never mutate remote state. MCP marks these as
#: read-only tools; everything else requires explicit write consent.
READ_ONLY: frozenset[OpType] = frozenset({OpType.LIST, OpType.GET, OpType.SEARCH})


@dataclass(slots=True)
class ConnectorContext:
    """Injected into every operation handler at call time."""

    client: OkwanClient
    credentials: dict[str, str]


Handler = Callable[[ConnectorContext, BaseModel], Awaitable[BaseModel]]


@dataclass(slots=True)
class Operation:
    name: str
    op_type: OpType
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Handler
    description: str

    @property
    def is_read_only(self) -> bool:
        return self.op_type in READ_ONLY


@dataclass(slots=True)
class Resource:
    """An entity exposed by a connector (e.g. `messages`, `templates`).

    `schema` is the canonical Pydantic model of one record — the same
    model later backs the SQL table projection in the query layer.
    """

    name: str
    schema: type[BaseModel]
    description: str
    operations: dict[str, Operation] = field(default_factory=dict)

    def operation(
        self,
        op_type: OpType,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        name: str | None = None,
        description: str = "",
    ) -> Callable[[Handler], Handler]:
        """Decorator: register an operation on this resource."""

        def register(fn: Handler) -> Handler:
            op_name = name or op_type.value
            if op_name in self.operations:
                raise ValueError(
                    f"duplicate operation '{op_name}' on resource '{self.name}'"
                )
            self.operations[op_name] = Operation(
                name=op_name,
                op_type=op_type,
                input_model=input_model,
                output_model=output_model,
                handler=fn,
                description=description or (fn.__doc__ or "").strip(),
            )
            return fn

        return register


@dataclass(slots=True)
class Connector:
    """A single external system, defined once."""

    name: str
    version: str
    description: str
    base_url: str
    auth: AuthAdapter
    rate_limit: RateLimitProfile
    docs_url: str = ""
    resources: dict[str, Resource] = field(default_factory=dict)

    def resource(
        self, name: str, schema: type[BaseModel], description: str = ""
    ) -> Resource:
        if name in self.resources:
            raise ValueError(f"duplicate resource '{name}' on connector '{self.name}'")
        res = Resource(name=name, schema=schema, description=description)
        self.resources[name] = res
        return res

    def context(self, credentials: dict[str, str]) -> ConnectorContext:
        """Build a ready-to-call context with auth + rate limiting applied."""
        client = OkwanClient(
            base_url=self.base_url,
            auth=self.auth.bind(credentials),
            rate_limit=self.rate_limit,
        )
        return ConnectorContext(client=client, credentials=credentials)

    def iter_operations(self) -> list[tuple[Resource, Operation]]:
        return [
            (res, op)
            for res in self.resources.values()
            for op in res.operations.values()
        ]
