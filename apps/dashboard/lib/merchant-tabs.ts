/** The merchant page's tabs. Shared by the server page and the client tab bar. */
export const MERCHANT_TABS = [
  { id: "findings", label: "Findings" },
  { id: "connections", label: "Connections" },
  { id: "keys", label: "API keys" },
] as const;

export type MerchantTab = (typeof MERCHANT_TABS)[number]["id"];

export function tabOf(value: string | null | undefined): MerchantTab {
  return MERCHANT_TABS.find((t) => t.id === value)?.id ?? "findings";
}
