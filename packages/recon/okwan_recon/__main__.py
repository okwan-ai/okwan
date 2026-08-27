"""Serve every registered reconciliation as one MCP server over stdio.

Usage: python -m okwan_recon
"""
import asyncio

import okwan_paystack.connector  # noqa: F401  (registers the connector)
import okwan_postgres.connector  # noqa: F401
import okwan_stripe.connector  # noqa: F401

from .emitters.mcp import run_stdio

if __name__ == "__main__":
    asyncio.run(run_stdio())
