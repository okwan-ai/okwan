/**
 * The four tools the hosted MCP server exposes, one line each. The server
 * (packages/query/okwan_query/mcp_http.py) is the source; this list exists
 * because a dashboard session cannot call it. Keep the two in step.
 */
export const HOSTED_TOOLS: { name: string; what: string }[] = [
  { name: "okwan_list_reconciliations", what: "What can run for this key, and what credentials anything blocked still needs." },
  { name: "okwan_reconcile", what: "Runs a reconciliation or the rails fold by name; filter a fold with status, e.g. collected_twice." },
  { name: "okwan_describe_tables", what: "The SQL tables (connector.resource) this key can query." },
  { name: "okwan_query", what: "Read-only SQL across live connectors." },
];

export const HOSTED_TOOL_NAMES = HOSTED_TOOLS.map((t) => t.name);
