<p align="center"><img src="assets/brand/okwan-logo.svg" width="340" alt="Okwan — the data connectivity layer built for AI agents"></p>

# Okwan

**The data connectivity layer built for AI agents.**

Every connector is defined once in the Okwan SDK and automatically becomes:
- **REST endpoints** — `POST /v1/{connector}/{resource}/{operation}`
- **MCP server** — `python -m okwan_mcp {connector}` (tools auto-derived)
- **SQL tables** — query layer, v2

## Quickstart

```bash
pip install -e .
uvicorn okwan_api.main:app --reload      # REST gateway → http://localhost:8000/docs
python -m okwan_mcp whatsapp             # MCP server over stdio
```

Credentials: REST via `X-Okwan-Credential-Access-Token` header;
MCP via `OKWAN_WHATSAPP_ACCESS_TOKEN` env var.

## Repo layout
```
packages/core        # SDK: Connector / Resource / Operation / auth / client
packages/connectors  # one folder per connector (whatsapp is #1)
packages/mcp_gen     # MCP auto-generation
apps/api             # FastAPI gateway (routes auto-mounted)
```

© 2026 Global Tech Startup LLC.
