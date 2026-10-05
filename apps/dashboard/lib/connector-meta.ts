/**
 * What the dashboard knows about a connector beyond its declaration.
 *
 * A dashboard mirror, like FOLD_READS (lib/finding.ts): the API does not yet
 * state a connector's role or where it sits in a merchant's picture, so the
 * order is kept here. It strains the define-once rule and goes when the API
 * exposes it (OKWAN_PROJECT.md §10 item 12). Anything derivable from the
 * declaration itself (routes, tables, tools) is derived, never kept here.
 */

/** The order connectors are shown in: the order ledger, the payment rails,
 *  then the rest. A connector not listed sorts after these, by name. */
export const ORDER = ["shopify", "paypal", "stripe", "paystack", "postgres", "whatsapp"] as const;

/** Compare two connector names in ORDER, unknown names last and alphabetical. */
export function byOrder(a: string, b: string): number {
  const rank = (n: string) => {
    const i = (ORDER as readonly string[]).indexOf(n);
    return i === -1 ? ORDER.length : i;
  };
  return rank(a) - rank(b) || a.localeCompare(b);
}
