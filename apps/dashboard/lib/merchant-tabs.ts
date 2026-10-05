/** The merchant page's two tabs. Shared by the server page and the client
 *  tab bar. Keys moved to Agents and Settings, usage to Settings › Plan
 *  (§9 2026-10-05); their old ?tab= values redirect in the page. */
export const MERCHANT_TABS = [
  { id: "findings", label: "Findings" },
  { id: "connections", label: "Connections" },
] as const;

export type MerchantTab = (typeof MERCHANT_TABS)[number]["id"];

/** Where a bare /merchants/[id] opens: Connections until the check can run
 *  (every system it reads is connected) or a saved check exists. */
export function defaultTab(ready: boolean, hasRun: boolean): MerchantTab {
  return ready || hasRun ? "findings" : "connections";
}

/** A valid ?tab= value wins; a link to one order or one run (`deepLink`)
 *  always means Findings; otherwise the merchant's default. */
export function tabOf(value: string | null | undefined, fallback: MerchantTab = "findings", deepLink = false): MerchantTab {
  return MERCHANT_TABS.find((t) => t.id === value)?.id ?? (deepLink ? "findings" : fallback);
}
