"""The public API reference at /docs, and the OpenAPI document it reads.

FastAPI still generates the document from the mounted routes, so every
connector route, its summary and its section text come from that
connector's one declaration. This module only adds what a reader needs
around them: an introduction, sections in groups with readable names, the
key as a real security scheme (instead of two raw header fields on every
operation), the write operations marked, and a branded Scalar page (with
Scalar's own Ask AI, MCP and toolbar features) in place of FastAPI's
default Swagger UI.

Nothing here changes what a route does. The page loads one pinned Scalar
build from jsDelivr with a Subresource Integrity hash, the same kind of CDN
load the default Swagger UI made.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, RedirectResponse
from okwan_core import all_connectors
from okwan_recon.across import OUTCOMES
from okwan_recon.paging import MAX_ROWS
from okwan_recon.registry import all_across
from okwan_recon.registry import get as get_reconciliation

from .mail import dashboard_url

SCALAR_VERSION = "1.72.4"
SCALAR_SRC = f"https://cdn.jsdelivr.net/npm/@scalar/api-reference@{SCALAR_VERSION}/dist/browser/standalone.js"
#: sha384 of that exact file (npm tarball and jsDelivr serve the same bytes).
SCALAR_SRI = "sha384-omTRdD9MbjA1vm12DqRUVvqJlr3VzSixvAdF1Jruu9AJOiJKyTKraIB6DyX+m10M"

#: The header fields the auth dependencies read. They are documented once,
#: as security schemes, rather than as parameters on every operation.
AUTH_HEADERS = {"authorization", "x-okwan-key"}

#: Sections that are not connectors: (tag, display name, description).
STATIC_TAGS: list[tuple[str, str, str]] = [
    ("reconciliations", "The check",
     "Run the check over a merchant's orders and payments. Each run is one request and is saved as "
     "verdicts and amounts, never raw payment records."),
    ("query", "SQL",
     "Read-only SQL over the tables the connectors generate. One request per statement."),
    ("tenants", "Merchants & keys",
     "Your merchants, their keys, connections, usage and saved checks. Takes a key or a dashboard "
     "session. Reading is free."),
    ("accounts", "Accounts",
     "Sign-up, email verification and dashboard sessions."),
    ("catalog", "Catalog",
     "Every connector and what its one declaration generates. Public."),
    ("system", "System", "Health of this API and its vault. Public."),
]


def label(description: str, name: str) -> str:
    """A connector's display name: the words before the colon its
    declaration opens with ("PayPal: read the transaction ledger…")."""
    head = description.split(":", 1)[0].strip()
    return head if head and head != description.strip() else name


def check_connectors() -> list[str]:
    """The connectors the across-rails checks read, ledger first, in their
    declaration order (from each fold's member reconciliations)."""
    seen: list[str] = []
    for fold in all_across():
        for rail in fold.rails:
            spec = get_reconciliation(rail.reconciliation)
            for ref in (spec.left, spec.right):
                if ref.connector not in seen:
                    seen.append(ref.connector)
    return seen


def intro() -> str:
    outcomes = ", ".join(f"`{o}`" for o in OUTCOMES)
    writes = sorted(f"`{c.name}.{r.name}.{op.name}`" for c in all_connectors()
                    for r, op in c.iter_operations() if not op.is_read_only)
    write_line = (f"The only operations that write are {', '.join(writes)}; they are marked **Writes** below."
                  if writes else "No operation writes.")
    return f"""Okwan reads the order ledgers and payment rails of the merchants you serve and tells you, order by
order, what was **collected twice**, what **doesn't add up** and what was **never paid**. Every route is
read-only.

## Quickstart

1. **Issue a key.** In the dashboard, open **Agents** or **Settings → API keys**, choose a merchant and issue a
   key. It starts with `okw_` and is shown once.
2. **Run the check.** `OKWAN_URL` is this API's address.

   ```bash
   curl "$OKWAN_URL/v1/reconciliations/across/rails?outcome=collected_twice" \\
     -H "Authorization: Bearer $OKWAN_KEY"
   ```

3. **Read the answer.** `summary` holds the counts and amounts; `data` is a page of orders, worst first.

## Authentication

Send the key as `Authorization: Bearer okw_…` (or `X-Okwan-Key: okw_…`). A key belongs to one merchant, or to
your workspace, and reads only what that tenant has connected. Routes under **Merchants & keys** also take a
dashboard session. Okwan stores a key's hash only; revoke a key by its id.

## One declaration, three surfaces

Each connector is declared once. Its REST routes (below), its SQL tables (`POST /v1/query`) and its MCP tools
(the hosted server at `/mcp/`, with the same key) all come from that declaration, so every surface reads the
same rails the same way.

## Read-only by design

No route can refund, charge or move money. {write_line}

## The check

`GET /v1/reconciliations/across/rails` reads the merchant's Shopify orders, then PayPal and Stripe, and gives one
verdict per order: {outcomes}. Filter with `outcome`; page with `limit` (up to {MAX_ROWS:,}) and the `cursor`
from the previous page. Every run is saved, and reading saved runs under `/v1/tenants/{{id}}/runs` is free.

## Requests and plans

A successful call that reads a provider (a connector route, a SQL statement, a check) counts as one request
against your plan. Reading the catalog, saved runs and usage is free. Past the monthly limit, calls answer `402`.

## Errors

Errors are JSON: `{{"detail": …}}`. `401` the key is missing or invalid, or the provider rejected the stored
credentials · `402` the plan's limit is reached · `404` unknown, or not yours (never `403`) · `409` a cursor from a
different result · `422` the request is invalid (your input is never echoed back) · `502` a provider failed. A
provider's own error keeps its status code.
"""


def tags() -> list[dict[str, Any]]:
    connectors = sorted(all_connectors(), key=lambda c: c.name)
    first = check_connectors()
    connectors.sort(key=lambda c: (first.index(c.name) if c.name in first else len(first), c.name))
    out = [{"name": t, "x-displayName": d, "description": text} for t, d, text in STATIC_TAGS[:1]]
    out += [{"name": c.name, "x-displayName": label(c.description, c.name), "description": c.description}
            for c in connectors]
    out += [{"name": t, "x-displayName": d, "description": text} for t, d, text in STATIC_TAGS[1:]]
    return out


def tag_groups(tag_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    connector_tags = [t["name"] for t in tag_list if t["name"] not in {s[0] for s in STATIC_TAGS}]
    return [
        {"name": "Start here", "tags": ["reconciliations"]},
        {"name": "Connectors", "tags": connector_tags},
        {"name": "SQL", "tags": ["query"]},
        {"name": "Your workspace", "tags": ["tenants", "accounts"]},
        {"name": "Reference", "tags": ["catalog", "system"]},
    ]


#: Short titles for the routes that are not connector operations. Connector
#: operations get theirs from their declaration (`operation_title`).
TITLES: dict[tuple[str, str], str] = {
    ("get", "/v1/connectors"): "List connectors",
    ("get", "/v1/reconciliations"): "List checks",
    ("get", "/v1/reconciliations/across/{name}"): "Run the check",
    ("get", "/v1/reconciliations/{name}"): "Run a two-sided reconciliation",
    ("get", "/v1/query/tables"): "List SQL tables",
    ("post", "/v1/query"): "Run a SQL query",
    ("get", "/v1/tenants"): "List merchants",
    ("post", "/v1/tenants"): "Add a merchant",
    ("post", "/v1/tenants/{tenant_id}/keys"): "Issue a key",
    ("delete", "/v1/tenants/keys/{key_id}"): "Revoke a key",
    ("put", "/v1/tenants/{tenant_id}/credentials"): "Save credentials",
    ("get", "/v1/tenants/{tenant_id}/credentials"): "List stored credential fields",
    ("get", "/v1/tenants/{tenant_id}/usage"): "Read usage",
    ("post", "/v1/tenants/{tenant_id}/connectors/{connector_name}/test"): "Test a connection",
    ("get", "/v1/tenants/{tenant_id}/runs/latest"): "Latest saved checks",
    ("get", "/v1/tenants/{tenant_id}/runs"): "List saved checks",
    ("get", "/v1/tenants/{tenant_id}/runs/{run_id}"): "Read a saved check",
    ("post", "/v1/tenants/{tenant_id}/reconciliations/across/{name}"): "Run the check for a merchant",
    ("post", "/v1/tenants/{tenant_id}/reconciliations/{name}"): "Run a reconciliation for a merchant",
    ("post", "/v1/signup"): "Sign up",
    ("post", "/v1/signup/verify"): "Verify an email address",
    ("post", "/v1/sessions"): "Sign in",
    ("delete", "/v1/sessions/current"): "Sign out",
    ("get", "/healthz"): "Health",
}


def operation_title(resource: str, op: str) -> str:
    """"List transactions", "Get customer", "Send text message", from the
    declared resource and operation names."""
    def words(name: str) -> str:
        return "SQL" if name == "sql" else name.replace("_", " ")

    def one(name: str) -> str:
        return name[:-1] if name.endswith("s") and not name.endswith("ss") else name

    verb, *rest = op.split("_")
    if verb == "get" and rest:
        return f"Get {words(one(resource))} {' '.join(rest)}"
    if verb == "send" and rest:
        return f"Send {' '.join(rest)} {words(one(resource))}"
    if verb == "get":
        return f"Get {words(one(resource))}"
    return f"{verb.capitalize()} {words(resource)}"


def connector_titles() -> dict[str, str]:
    return {f"{c.name}_{r.name}_{op.name}": operation_title(r.name, op.name)
            for c in all_connectors() for r, op in c.iter_operations()}


def write_operations() -> set[str]:
    """operation_ids of connector operations that are not read-only."""
    return {f"{c.name}_{r.name}_{op.name}" for c in all_connectors()
            for r, op in c.iter_operations() if not op.is_read_only}


def build_openapi(app: FastAPI) -> dict[str, Any]:
    spec = get_openapi(title=app.title, version=app.version, description=intro(), routes=app.routes)
    spec["info"]["x-logo"] = {"altText": "Okwan"}
    tag_list = tags()
    spec["tags"] = tag_list
    spec["x-tagGroups"] = tag_groups(tag_list)
    spec.setdefault("components", {})["securitySchemes"] = {
        "OkwanKey": {
            "type": "http", "scheme": "bearer", "bearerFormat": "okw_…",
            "description": "An Okwan API key, issued in the dashboard (Agents or Settings → API keys).",
        },
        "OkwanKeyHeader": {
            "type": "apiKey", "in": "header", "name": "X-Okwan-Key",
            "description": "The same key in its own header, for clients that can't set Authorization.",
        },
    }
    writes = write_operations()
    titles = connector_titles()
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            # A connector route's summary is its declared description, which
            # reads as a paragraph: it becomes the description, and the title
            # is derived from the declared names.
            if op.get("operationId") in titles:
                op["description"] = op.get("description") or op.get("summary", "")
                op["summary"] = titles[op["operationId"]]
            elif (method, path) in TITLES:
                op["summary"] = TITLES[(method, path)]
            params = op.get("parameters", [])
            kept = [p for p in params if not (p.get("in") == "header" and p.get("name", "").lower() in AUTH_HEADERS)]
            if len(kept) != len(params):
                op["security"] = [{"OkwanKey": []}, {"OkwanKeyHeader": []}]
                if kept:
                    op["parameters"] = kept
                else:
                    op.pop("parameters", None)
            if op.get("operationId") in writes:
                op["x-okwan-writes"] = True
                op["description"] = ("**Writes.** This operation sends data to the provider.\n\n"
                                     + op.get("description", "")).strip()
    return spec


#: Okwan's tokens (OKWAN_PROJECT.md §2) as Scalar theme variables: neutral
#: white and grey, the same as the dashboard. Volt is the one accent, the
#: active section in the sidebar; links and buttons stay ink for contrast.
THEME_CSS = """
:root {
  --scalar-font: 'Poppins', system-ui, sans-serif;
  --scalar-font-code: 'JetBrains Mono', ui-monospace, monospace;
  --scalar-radius: 8px; --scalar-radius-lg: 12px; --scalar-radius-xl: 12px;
  --scalar-custom-header-height: 56px;
}
.light-mode {
  --scalar-color-1: #111111; --scalar-color-2: #4b4b4b; --scalar-color-3: #6b6b6b;
  --scalar-color-accent: #111111;
  --scalar-background-1: #ffffff; --scalar-background-2: #f5f5f5; --scalar-background-3: #ebebeb;
  --scalar-background-accent: #fff6c2;
  --scalar-border-color: #e5e5e5;
  --scalar-sidebar-background-1: #fafafa; --scalar-sidebar-color-1: #111111; --scalar-sidebar-color-2: #4b4b4b;
  --scalar-sidebar-border-color: #e5e5e5;
  --scalar-sidebar-item-hover-background: rgba(17, 17, 17, 0.05);
  --scalar-sidebar-item-active-background: #ffd400; --scalar-sidebar-color-active: #111111;
  --scalar-sidebar-search-background: #ffffff; --scalar-sidebar-search-border-color: #858585;
  --scalar-sidebar-search-color: #4b4b4b;
}
.dark-mode {
  --scalar-color-1: #f5f5f5; --scalar-color-2: #c8ccd4; --scalar-color-3: #98a1b0;
  --scalar-color-accent: #ffd400;
  --scalar-background-1: #0d1b2e; --scalar-background-2: #112339; --scalar-background-3: #183050;
  --scalar-background-accent: rgba(255, 212, 0, 0.12);
  --scalar-border-color: #22385a;
  --scalar-sidebar-background-1: #0a1626; --scalar-sidebar-color-1: #f5f5f5; --scalar-sidebar-color-2: #c8ccd4;
  --scalar-sidebar-border-color: #22385a;
  --scalar-sidebar-item-hover-background: rgba(245, 245, 245, 0.06);
  --scalar-sidebar-item-active-background: #ffd400; --scalar-sidebar-color-active: #111111;
  --scalar-sidebar-search-background: #112339; --scalar-sidebar-search-border-color: #3a5378;
  --scalar-sidebar-search-color: #c8ccd4;
}
.scalar-app h1, .scalar-app .section-header, .scalar-app .markdown h2 {
  font-family: 'Fraunces', Georgia, serif; font-weight: 500; letter-spacing: -0.01em;
}
"""

BRAND_BAR_CSS = """
.okwan-bar { position: sticky; top: 0; z-index: 1000; height: 56px; display: flex; align-items: center;
  gap: 12px; padding: 0 20px; background: #ffffff; border-bottom: 1px solid #e5e5e5;
  font: 14px/1.4 'Poppins', system-ui, sans-serif; color: #111111; }
.okwan-bar a { color: inherit; text-decoration: none; }
.okwan-bar .mark { width: 22px; height: 22px; border-radius: 6px; background: #ffd400; box-shadow: inset 0 0 0 1px #111111; }
.okwan-bar .name { font: 500 20px/1 'Fraunces', Georgia, serif; }
.okwan-bar .sep { color: #858585; }
.okwan-bar .links { margin-left: auto; display: flex; gap: 4px; }
.okwan-bar .links a { display: inline-flex; align-items: center; min-height: 44px; padding: 0 10px;
  border-radius: 8px; color: #4b4b4b; }
.okwan-bar .links a:hover { background: rgba(17, 17, 17, 0.05); color: #111111; }
.okwan-bar a:focus-visible { outline: 2px solid #111111; outline-offset: 2px; }
@media (max-width: 600px) { .okwan-bar .sep, .okwan-bar .title { display: none; } }
"""


def page() -> str:
    config = {
        "url": "/openapi.json",
        "theme": "none",
        "layout": "modern",
        "withDefaultFonts": False,
        "customCss": THEME_CSS,
        "defaultHttpClient": {"targetKey": "shell", "clientKey": "curl"},
        "authentication": {"preferredSecurityScheme": "OkwanKey"},
        "metaData": {"title": "Okwan API reference"},
        "hideModels": False,
        # Scalar's own features stay on (the owner's call): Ask AI, the MCP
        # generator, the client button and the developer toolbar, shown in
        # production too. Usage telemetry stays off: nothing a reader sees.
        "showDeveloperTools": "always",
        "telemetry": False,
        "persistAuth": False,
    }
    dash = dashboard_url()
    dashboard_link = f'<a href="{dash}/overview">Dashboard ↗</a>' if not dash.startswith("http://localhost") else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Okwan API reference</title>
<meta name="description" content="Read-only reconciliation across Shopify, PayPal and Stripe: the REST API, SQL and hosted MCP.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect x='3' y='3' width='26' height='26' rx='7' fill='%23FFD400' stroke='%23111' stroke-width='2'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500&family=JetBrains+Mono:wght@400;500&family=Poppins:wght@400;500;600&display=swap">
<style>body {{ margin: 0; background: #ffffff; }} {BRAND_BAR_CSS}</style>
</head>
<body>
<header class="okwan-bar">
  <a href="/docs" aria-label="Okwan API reference" style="display:flex;align-items:center;gap:10px">
    <span class="mark" aria-hidden="true"></span><span class="name">Okwan</span>
  </a>
  <span class="sep" aria-hidden="true">/</span><span class="title">API reference</span>
  <nav class="links" aria-label="Elsewhere">{dashboard_link}<a href="/openapi.json">OpenAPI JSON</a></nav>
</header>
<div id="app"></div>
<script src="{SCALAR_SRC}" integrity="{SCALAR_SRI}" crossorigin="anonymous"></script>
<script>Scalar.createApiReference('#app', {json.dumps(config)})</script>
</body>
</html>"""


def install(app: FastAPI) -> None:
    """Serve the reference at /docs and the enriched document at /openapi.json.
    Call after every router is mounted, so the document sees every route."""
    cached: dict[str, Any] = {}

    def openapi() -> dict[str, Any]:
        if "spec" not in cached:
            cached["spec"] = build_openapi(app)
        return cached["spec"]

    app.openapi = openapi  # type: ignore[method-assign]

    @app.get("/docs", include_in_schema=False)
    async def docs() -> HTMLResponse:
        return HTMLResponse(page())

    @app.get("/redoc", include_in_schema=False)
    async def redoc() -> RedirectResponse:
        return RedirectResponse("/docs", status_code=307)
