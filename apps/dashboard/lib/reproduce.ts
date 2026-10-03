/**
 * How to get one order's verdict again, on the surfaces that exist hosted.
 * Pure: strings only. The key is a placeholder ($OKWAN_KEY); a key is shown
 * once at issue and never held by the dashboard.
 */
import type { FindingRow } from "./finding";

/** A value inside a double-quoted jq string, inside a single-quoted shell word. */
function jqString(v: string): string {
  return v.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/'/g, "'\\''");
}

export function restCall(apiBase: string, r: FindingRow): string {
  return [
    `curl -s "${apiBase}/v1/reconciliations/across/rails?outcome=${r.outcome}&limit=1000" \\`,
    `  -H "Authorization: Bearer $OKWAN_KEY" \\`,
    `  | jq '.data[] | select((.order.name // .order.id | tostring) == "${jqString(r.order)}")'`,
  ].join("\n");
}

export function mcpCall(r: FindingRow): string {
  return `okwan_reconcile ${JSON.stringify({ name: "rails", status: r.outcome, limit: 1000 })}\n# then find order ${r.order} in rows[]`;
}
