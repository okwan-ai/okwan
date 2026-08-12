"""Okwan API gateway — REST interface auto-generated from connectors.

Route shape: POST /v1/{connector}/{resource}/{operation}
Credentials arrive per-request via X-Okwan-Credential-{field} headers
in v0; vault-backed credential storage replaces this in P1. No
connector may register a bespoke route — everything flows from the
SDK definition.
"""
from __future__ import annotations

import inspect
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

import okwan_whatsapp.connector  # noqa: F401  (registers the connector)
from okwan_core import (
    CredentialError,
    OkwanError,
    UpstreamError,
    all_connectors,
)
from okwan_core.connector import Connector, Operation, Resource

app = FastAPI(
    title="Okwan API",
    version="0.1.0",
    description="The data connectivity layer built for AI agents.",
)


class ConnectorInfo(BaseModel):
    name: str
    version: str
    description: str
    resources: dict[str, list[str]]


@app.get("/v1/connectors", response_model=list[ConnectorInfo])
async def list_connectors() -> list[ConnectorInfo]:
    return [
        ConnectorInfo(
            name=c.name,
            version=c.version,
            description=c.description,
            resources={r.name: sorted(r.operations) for r in c.resources.values()},
        )
        for c in all_connectors()
    ]


def _credentials(connector: Connector, request: Request) -> dict[str, str]:
    return {
        field: request.headers.get(
            f"X-Okwan-Credential-{field.replace('_', '-')}", ""
        )
        for field in connector.auth.required_fields
    }


def _mount(connector: Connector, resource: Resource, op: Operation) -> None:
    path = f"/v1/{connector.name}/{resource.name}/{op.name}"

    async def endpoint(request: Request, body: BaseModel) -> Any:
        credentials = _credentials(connector, request)
        try:
            connector.auth.validate(credentials)
            ctx = connector.context(credentials)
            try:
                return await op.handler(ctx, body)
            finally:
                await ctx.client.aclose()
        except CredentialError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.body) from exc
        except OkwanError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # FastAPI introspects the signature; give it the real input model.
    endpoint.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter(
                "request",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=Request,
            ),
            inspect.Parameter(
                "body",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=op.input_model,
            ),
        ]
    )
    endpoint.__name__ = f"{connector.name}_{resource.name}_{op.name}"

    app.post(
        path,
        response_model=op.output_model,
        summary=op.description,
        tags=[connector.name],
        operation_id=endpoint.__name__,
    )(endpoint)


for _connector in all_connectors():
    for _resource, _op in _connector.iter_operations():
        _mount(_connector, _resource, _op)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "connectors": str(len(all_connectors()))}
