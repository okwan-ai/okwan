/**
 * The across-rails result, as the dashboard shows it. The API's row
 * carries every rail record it matched; the browser gets only what the
 * page renders, so customer fields on those records stay server-side.
 */

export const OUTCOME_LABEL: Record<string, string> = {
  collected_twice: "Collected twice",
  collected_inconsistent: "Doesn't add up",
  uncollected: "No payment found",
  unverifiable: "Couldn't verify",
  split_tender: "Split across rails",
  collected: "Paid once",
};

/** Display names for rails whose casing a capital letter can't recover. */
const RAIL_LABEL: Record<string, string> = { paypal: "PayPal", momo: "MoMo", whatsapp: "WhatsApp" };

export function railLabel(name: string): string {
  return RAIL_LABEL[name] ?? name.charAt(0).toUpperCase() + name.slice(1);
}

/**
 * What the `rails` fold reads: the Shopify ledger, then PayPal and Stripe.
 * A missing credential on any of them fails the whole run, so a merchant is
 * ready to check only when all three are connected.
 *
 * Mirrors packages/recon/okwan_recon/declarations.py (`rails`). A second
 * copy breaks the one-declaration rule, and is here only because the API
 * gives a dashboard session no fold metadata (GET /v1/reconciliations takes
 * an okw_ key). Keep the two in step until the API exposes it to sessions.
 */
export const FOLD_READS = ["shopify", "paypal", "stripe"] as const;

/** The payment rails the fold checks orders against (everything but the ledger). */
export const FOLD_RAILS = FOLD_READS.filter((c) => c !== "shopify");

export function missingFor(m: { ready: string[] }): string[] {
  return FOLD_READS.filter((c) => !m.ready.includes(c));
}

/** Non-clean outcomes, in the order the table leads with them. */
export const NEEDS_LOOK = ["collected_twice", "collected_inconsistent", "uncollected", "unverifiable"];

/** Outcomes that are a finding about money, not about what could be read:
 * what Overview and Findings list as needing attention. */
export const ATTENTION = ["collected_twice", "collected_inconsistent", "uncollected"];

/** Clean outcomes, after every non-clean one. */
const CLEAN = ["split_tender", "collected"];

/** Symbol + label, never colour alone. */
export const OUTCOME_MARK: Record<string, string> = {
  collected_twice: "×2",
  collected_inconsistent: "≠",
  uncollected: "∅",
  unverifiable: "?",
  split_tender: "+",
  collected: "✓",
};

export const OUTCOME_TONE: Record<string, "danger" | "warn" | "neutral" | "ok"> = {
  collected_twice: "danger",
  collected_inconsistent: "warn",
  uncollected: "warn",
  unverifiable: "neutral",
  split_tender: "ok",
  collected: "ok",
};

function rails(names: string[]): string {
  const l = names.map(railLabel);
  return l.length <= 1 ? (l[0] ?? "") : `${l.slice(0, -1).join(", ")} and ${l[l.length - 1]}`;
}

/** Every rail took the order's currency. The fold reports a mismatch as
 *  collected_inconsistent, and collected_minor then adds amounts in
 *  different currencies, so no difference may be computed from it. */
export function sameCurrency(r: FindingRow): boolean {
  const cur = (r.currency ?? "").toUpperCase();
  return r.paid.every((p) => !p.currency || p.currency.toUpperCase() === cur);
}

/**
 * The money a finding is about, per outcome, in integer minor units:
 * collected twice → what was taken beyond the order (owed back);
 * doesn't add up → the gap between what was taken and the order;
 * no payment → the order total (unpaid). Null when amounts are missing.
 */
export function atStake(r: FindingRow): { minor: number; label: string } | null {
  if (!sameCurrency(r)) return null;
  const total = r.total_minor;
  const taken = r.collected_minor;
  if (r.outcome === "collected_twice") {
    return total !== null && taken !== null ? { minor: taken - total, label: "owed back" } : null;
  }
  if (r.outcome === "collected_inconsistent") {
    if (total === null || taken === null) return null;
    return { minor: Math.abs(taken - total), label: taken < total ? "short" : "over" };
  }
  if (r.outcome === "uncollected") return total !== null ? { minor: total, label: "unpaid" } : null;
  return null;
}

/** What happened, without the order number, with each rail's take. */
export function what(r: FindingRow, fmt: (minor: number | null, currency: string | null) => string): string {
  const takes = r.paid.map((p) => `${railLabel(p.rail)} ${fmt(p.minor, p.currency ?? r.currency)}`);
  const list = takes.length <= 1 ? (takes[0] ?? "") : `${takes.slice(0, -1).join(", ")} and ${takes[takes.length - 1]}`;
  switch (r.outcome) {
    case "collected_twice":
      return `Paid in full twice: ${list}.`;
    case "collected_inconsistent":
      return `${list} against a ${fmt(r.total_minor, r.currency)} order.`;
    case "uncollected":
      return `No payment on ${rails([...FOLD_RAILS]).replace(" and ", " or ")}.`;
    case "unverifiable":
      return r.reason ?? "Couldn't be checked.";
    default:
      return list ? `Paid once: ${list}.` : "Paid once.";
  }
}

/** One plain-language line for a row, for lists that cannot show a table.
 *  `withOrder: false` drops the order number where a column already shows it. */
export function sentence(r: FindingRow, withOrder = true): string {
  const o = withOrder ? `Order ${r.order} ` : "";
  const cap = (t: string) => (withOrder ? t : t.charAt(0).toUpperCase() + t.slice(1));
  switch (r.outcome) {
    case "collected_twice":
      return cap(`${o}was paid in full on ${rails(r.collected_on) || "more than one rail"}.`);
    case "collected_inconsistent":
      return withOrder
        ? `Order ${r.order}: what the rails took doesn't add up to the order total.`
        : "What the rails took doesn't add up to the order total.";
    case "uncollected":
      return cap(`${o}has no payment on ${rails([...FOLD_RAILS]).replace(" and ", " or ")}.`);
    case "unverifiable":
      return withOrder
        ? `Order ${r.order} couldn't be checked on ${rails(r.unverified) || "a rail"}.`
        : `Couldn't be checked on ${rails(r.unverified) || "a rail"}.`;
    default:
      return cap(`${o}was paid once.`);
  }
}

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
  const order = [...NEEDS_LOOK, ...CLEAN];
  const rank = (o: string) => order.indexOf(o);
  const rows = page.data
    .filter((r) => rank(r.outcome) >= 0)
    // Worst outcome first, clean ones last; within one, the largest order first.
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
  const twiceRows = rows.filter((r) => r.outcome === "collected_twice");
  const twice = new Set(twiceRows.map((r) => r.currency));
  return {
    summary: page.summary,
    rows,
    partial: page.has_more,
    // A currency only when every collected-twice order is on this page and
    // they share one; otherwise the total can't be labelled with confidence.
    twice_currency: twice.size === 1 && twiceRows.length === page.summary.collected_twice ? [...twice][0] : null,
  };
}

/** Paid once, but another rail couldn't rule out a second payment. From the
 *  rows on the page, so a partial page undercounts; it never overcounts. */
export function unconfirmedRows(f: Finding): number {
  return f.rows.filter((r) => (r.outcome === "collected" || r.outcome === "split_tender") && r.unverified.length > 0).length;
}

/** A side was cut at its record cap, so later records weren't read. */
export function truncated(f: Finding): boolean {
  return Boolean(f.summary.ledger_coverage?.truncated)
    || Object.values(f.summary.rails).some((r) => r.coverage?.truncated);
}

export function checkedAgo(at: number | null, now = Date.now()): string {
  if (at === null) return "";
  const min = Math.floor((now - at) / 60000);
  return min < 1 ? "Checked just now" : `Checked ${min} min ago`;
}

/** A finding as the cross-merchant pages and the order drawer carry it. */
export type AttentionRow = FindingRow & {
  merchantId: string;
  merchantName: string;
  /** When the check that produced it ran (ms epoch). */
  at: number;
  /** The check's result was cut short (more than 1,000 orders, or a side
   *  hit its record cap), so this list may not be the whole of it. */
  partial: boolean;
};

/** What the browser's in-tab results store needs from a run. */
export type RunDigest = {
  id: string;
  /** The merchant's name, for notes that mention it. */
  name?: string;
  ok: boolean;
  /** When the result was produced (ms epoch, on the browser's clock once in
   *  the tab store); the store keeps the newest. */
  at: number;
  /** "server": a page-load check (Overview, Findings), `rawAt` on the
   *  server's clock. "tab": this tab's Run button, on the browser's clock. */
  origin?: "server" | "tab";
  rawAt?: number;
  /** Findings: collected twice, doesn't add up, no payment. */
  open: number;
  twice: number;
  twiceMinor: number;
  /** What was taken beyond the order totals: the amount owed back. */
  overMinor: number;
  unverifiable: number;
  /** Paid once with another rail unable to rule out a second payment. */
  unconfirmed: number;
  currency: string | null;
  detail?: string;
};

export function digestOf(id: string, f: Finding, at: number): RunDigest {
  const s = f.summary;
  return {
    id,
    ok: true,
    at,
    open: s.collected_twice + s.collected_inconsistent + s.uncollected,
    twice: s.collected_twice,
    twiceMinor: s.collected_twice_minor,
    overMinor: s.overcollected_minor,
    unverifiable: s.unverifiable,
    unconfirmed: unconfirmedRows(f),
    currency: f.twice_currency,
  };
}

export function failedDigest(id: string, detail: string, at: number): RunDigest {
  return { id, ok: false, at, open: 0, twice: 0, twiceMinor: 0, overMinor: 0, unverifiable: 0, unconfirmed: 0, currency: null, detail };
}

/** One short verdict for a digest, for places with room for a glyph and a
 *  few words (sidebar, palette). Same precedence as merchant status. */
export function verdictOf(d: RunDigest | undefined): { mark: string; label: string; tone: "danger" | "ink" | "soft" | "ok" } {
  if (!d) return { mark: "·", label: "not checked this session", tone: "soft" };
  if (!d.ok) return { mark: "✕", label: "couldn't run", tone: "danger" };
  if (d.twice) return { mark: "×2", label: `collected twice · ${d.open} finding${d.open === 1 ? "" : "s"}`, tone: "danger" };
  if (d.open) return { mark: String(d.open), label: `${d.open} finding${d.open === 1 ? "" : "s"}`, tone: "ink" };
  if (d.unverifiable) return { mark: "?", label: `${d.unverifiable} couldn't verify`, tone: "soft" };
  if (d.unconfirmed) return { mark: "✓?", label: `paid once · ${d.unconfirmed} not ruled out`, tone: "ok" };
  return { mark: "✓", label: "all paid once", tone: "ok" };
}
