"""Run any registered connector as an MCP server over stdio.

Usage: python -m okwan_mcp whatsapp
"""
import asyncio
import sys

import okwan_paystack.connector  # noqa: F401
import okwan_postgres.connector  # noqa: F401
import okwan_shopify.connector  # noqa: F401
import okwan_stripe.connector  # noqa: F401
import okwan_whatsapp.connector  # noqa: F401
from okwan_core import get

from .generator import run_stdio

if __name__ == "__main__":
    connector_name = sys.argv[1] if len(sys.argv) > 1 else "whatsapp"
    asyncio.run(run_stdio(get(connector_name)))
