# Dependencies

Direct dependencies, as declared, and the versions the suite and builds
were verified on for this pack (2026-11-03). Transitive dependencies
come from these; see "Full inventory" for how to list them.

## API and packages (Python 3.12, `pyproject.toml`)

| Package | Declared | Verified on | Used for |
|---|---|---|---|
| fastapi | >=0.115 | 0.162.0 | HTTP API |
| starlette | (via fastapi) | 1.10.0 | ASGI, the edge middleware |
| pydantic | >=2.9 | 2.14.2 | Validation, schemas |
| uvicorn | >=0.30 | 0.54.0 | Server |
| httpx | >=0.27 | 0.28.1 | Calls to the rails, the monitor |
| tenacity | >=9.0 | 9.2.1 | Retries to the rails |
| mcp | >=2.0 | 2.3.0 | Hosted MCP server |
| asyncpg | >=0.29 | 0.32.0 | Vault database |
| duckdb | >=1.1 | 2.0.0 | SQL over connector rows, sandboxed |
| cryptography | >=43.0 | 50.0.2 | AES-GCM envelope encryption |
| anthropic | >=2.4,<3 | 2.4.0 | The support assistant's model calls |

Development only: pytest 9.2.0, pytest-asyncio 1.4.1.post1, ruff 0.17.3
(pinned `>=0.17,<0.18`).

The image is `python:3.12-slim` (`Dockerfile`). Dependencies are ranges
with no lockfile, so a rebuild can pick up a newer release: known gap G9.

## Dashboard (lockfile `apps/dashboard/package-lock.json`)

| Package | Declared | Locked |
|---|---|---|
| next | ^15.4.0 | 15.5.27 |
| react, react-dom | ^19.0.0 | 19.3.0 |
| server-only | ^0.0.1 | 0.0.1 |
| tailwindcss, @tailwindcss/postcss | ^4.1.0 | 4.3.3 |
| typescript | ^5 | 5.9.3 |

## Site (lockfile `apps/web/package-lock.json`)

| Package | Declared | Locked |
|---|---|---|
| next | ^15.4.0 | 15.5.23 |
| react, react-dom | ^19.0.0 | 19.2.8 |
| tailwindcss, @tailwindcss/postcss | ^4.1.0 | 4.3.3 |
| typescript | ^5 | 5.9.3 |

## CI actions (`.github/workflows`)

`actions/checkout@v4`, `actions/setup-python@v5`, `actions/setup-node@v4`,
`actions/github-script@v7`, and the `postgres:16` service image. Pinned by
tag, not commit: known gap G9.

## Services

Render (API, dashboard), Neon (vault database), Vercel (site, DNS for
okwan.ai), Resend (verification and escalation mail), Anthropic (the
support assistant's model), GitHub (code, CI, monitoring).
Which of these are subprocessors of customer data is an owner and counsel
question: [owner-and-legal.md](owner-and-legal.md).

## Full inventory

The resolved Python set, with known vulnerabilities, from a clean
environment built as the image builds it:

```bash
pip install -e . && pip install pip-audit && pip-audit
```

The JavaScript sets:

```bash
cd apps/dashboard && npm ci && npm audit
```

```bash
cd apps/web && npm ci && npm audit
```
