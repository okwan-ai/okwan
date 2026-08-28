"""Serve federated SQL as an MCP server over stdio.

Usage: python -m okwan_query
"""
import asyncio

import okwan_paystack.connector  # noqa: F401  (registers the connector)
import okwan_postgres.connector  # noqa: F401
import okwan_shopify.connector   # noqa: F401
import okwan_stripe.connector    # noqa: F401
import okwan_whatsapp.connector  # noqa: F401

from .mcp import run_stdio

if __name__ == "__main__":
    asyncio.run(run_stdio())
