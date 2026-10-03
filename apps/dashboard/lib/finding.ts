/**
 * The across-rails result, as the dashboard shows it. The API's row
 * carries every rail record it matched; the browser gets only what the
 * page renders, so customer fields on those records stay server-side.
 */

export const OUTCOME_LABEL: Record<string, string> = {
  collected_twice: "Collected twice",
  collected_inconsistent: "Amounts don't add up",
  uncollected: "No payment found",
  unverifiable: "Couldn't verify",
  split_tender: "Split across rails",
  collected: "Paid once",
};

/** Display names for rails whose casing a capital letter can't recover. */
const RAIL_LABEL: Record<string, string> = { paypal: "PayPal", momo: "MoMo" };

export function railLabel(name: string): string {
  return RAIL_LABEL[name] ?? name.charAt(0).toUpperCase() + name.slice(1);
}

/** Non-clean outcomes, in the order the table leads with them. */
export const NEEDS_LOOK = ["collected_twice", "collected_inconsistent", "uncollected", "unverifiable"];

export type Coverage = {
  source: string;
  records: number;
  cap: number;
  truncated: boolean;
  span_start: string | null;
  span_end: string | null;
};

export type Summary = {
  orders: number;
  collected_twice: number;
  split_tender: number;
  collected_inconsistent: number;
  collected: number;
  unverifiable: number;
  uncollected: number;
  collected_twice_minor: number;
  overcollected_minor: number;
  match_rate: number | null;
  ledger_coverage: Coverage | null;
  rails: Record<string, { coverage: Coverage | null; unmatched_right: number; unverifiable_right: number }>;
};

export type FindingRow = {
  outcome: string;
  order: string;
  currency: string | null;
  total_minor: number | null;
  collected_minor: number | null;
  collected_on: string[];
  unverified: string[];
  reason: string | null;
  /** What each matched rail took, so the page can format it. */
  paid: { rail: string; minor: number | null; currency: string | null }[];
};

export type Finding = {
  summary: Summary;
  rows: FindingRow[];
  /** The API had more rows than one page; the table is partial. */
  partial: boolean;
  /** The currency of the collected-twice orders, when they share one. */
  twice_currency: string | null;
};

type ApiRow = {
  outcome: string;
  order: Record<string, unknown>;
  order_total_minor: number | null;
  collected_minor: number | null;
  collected_on: string[];
  unverified_rails: string[];
  reason: string | null;
  rails: { rail: string; status: string; collected_minor: number | null; currency: string | null }[];
};

export type AcrossPage = { summary: Summary; data: ApiRow[]; has_more: boolean };

export function toFinding(page: AcrossPage): Finding {
  const rank = (o: string) => NEEDS_LOOK.indexOf(o);
  const rows = page.data
    .filter((r) => rank(r.outcome) >= 0)
    // Worst outcome first; within one, the largest order first.
    .sort((a, b) => rank(a.outcome) - rank(b.outcome)
      || (b.order_total_minor ?? 0) - (a.order_total_minor ?? 0))
    .map((r) => ({
      outcome: r.outcome,
      order: String(r.order.name ?? r.order.id ?? "—"),
      currency: typeof r.order.currency === "string" ? r.order.currency : null,
      total_minor: r.order_total_minor,
      collected_minor: r.collected_minor,
      collected_on: r.collected_on,
      unverified: r.unverified_rails,
      reason: r.reason,
      paid: (r.rails ?? [])
        .filter((v) => v.status === "matched")
        .map((v) => ({ rail: v.rail, minor: v.collected_minor, currency: v.currency })),
    }));
  const twice = new Set(rows.filter((r) => r.outcome === "collected_twice").map((r) => r.currency));
  return {
    summary: page.summary,
    rows,
    partial: page.has_more,
    twice_currency: twice.size === 1 ? [...twice][0] : null,
  };
}
