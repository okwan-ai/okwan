import { asWritten, dayOf } from "./file-checks";
import { formatMinor } from "./money";

/**
 * Open items: every finding of the workspace's merchants that still needs
 * a look, across all their checks, as `GET /v1/tenants/{id}/exceptions`
 * gives them (okwan_recon.exceptions). Each keeps the same key from one run
 * to the next and is aged from the day it is dated, or from when it was
 * first seen.
 *
 * The words are made here, on the server, and the list is drawn from them
 * in the browser (exceptions-panel.tsx), so nothing on the page is worked
 * out there. A reference is a merchant's own text: it is handed over as
 * written, with what cannot be seen spelled out, and drawn in an element of
 * its own, never inside a sentence the page writes.
 */

/** One item as the API answers it. */
export type ApiItem = {
  key: string;
  check: string;
  title: string;
  kind: "pair" | "across";
  finding: string;
  tenant_id: string;
  merchant: string;
  amount_minor: number | null;
  currency: string | null;
  dated_on: string | null;
  first_seen: string | null;
  open_days: number | null;
  left_ref?: string | null;
  right_ref?: string | null;
  order_ref?: string | null;
  run_id: string;
  run_at: string;
};

export type ApiCheck = {
  tenant_id: string;
  check: string;
  title: string;
  kind: "pair" | "across";
  last_run: { id: string; status: "ok" | "failed" | string; started_at: string; finished_at: string } | null;
  findings_from: string | null;
  /** How many findings the check's newest good run found. */
  open: number;
  /** Whether its stored rows kept fewer than it found. */
  cut: boolean;
};

export type ApiAnswer = { data: ApiItem[]; total: number; listed: number; checks: ApiCheck[]; next: string | null; as_of: string };

/** One line of the list, every word already chosen. */
export type Line = {
  key: string;
  /** The merchant's page, on the tab where this check's result is drawn. */
  href: string;
  merchantId: string;
  merchant: string;
  check: string;
  what: string;
  refs: { label: string; value: string }[];
  amount: string;
  dated: string;
  open: string;
  /** How long it has been open, for the colour of an old item. */
  days: number | null;
  firstSeen: string;
};

export type Notice = { merchantId: string; merchant: string; text: string; href?: string; link?: string };

export type Shown =
  | { state: "unavailable" }
  | { state: "ok"; lines: Line[]; total: number; left: number; notices: Notice[]; checks: number; asOf: string;
      /** Set when the list is one merchant's. */
      merchant: { id: string; name: string } | null;
      /** The account's list comes 50 merchants a page: the next page, if any. */
      nextHref: string | null };

/** What each side of a check is, in a person's words: [left, right]. */
const SIDES: Record<string, [string, string]> = {
  batches_deposits: ["batch", "deposit"],
  square_payments_payouts: ["payment", "payout line"],
  square_payouts_deposits: ["payout", "deposit"],
  clover_orders_payments: ["order", "payment"],
  clover_payments_settlements: ["card sale", "settled transaction"],
  delivery_payout_lines: ["payout", "order line"],
  delivery_payouts_deposits: ["payout", "deposit"],
  avatax_collected_filed: ["return", "recorded sale"],
  shopify_orders: ["payment", "order"],
  shopify_stripe: ["order", "Stripe charge"],
  shopify_paypal: ["order", "PayPal payment"],
};

const FOLD: Record<string, string> = {
  collected_twice: "Collected twice",
  collected_inconsistent: "Doesn't add up",
  uncollected: "No payment",
};

function cap(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** What the finding is, in words that name no reference. */
export function what(item: Pick<ApiItem, "check" | "kind" | "finding">): string {
  if (item.kind === "across") return FOLD[item.finding] ?? "Needs a look";
  const [left, right] = SIDES[item.check] ?? ["record", "match"];
  switch (item.finding) {
    case "unmatched_left": return `${cap(left)} with no ${right}`;
    case "unmatched_right": return `${cap(right)} with no ${left}`;
    case "matched_discrepant": return `${cap(left)} and ${right} differ`;
    case "ambiguous": return `More than one ${right} fits this ${left}`;
    default: return "Needs a look";
  }
}

/** The references an item is about, each labelled with what it is. */
function refs(item: ApiItem): { label: string; value: string }[] {
  if (item.kind === "across") {
    return item.order_ref ? [{ label: "Order", value: asWritten(item.order_ref) }] : [];
  }
  const [left, right] = SIDES[item.check] ?? ["record", "match"];
  const out: { label: string; value: string }[] = [];
  if (item.left_ref) out.push({ label: cap(left), value: asWritten(item.left_ref) });
  if (item.right_ref) out.push({ label: cap(right), value: asWritten(item.right_ref) });
  return out;
}

/** The checks whose result a merchant's Files tab draws. */
const ON_FILES_TAB = new Set(["batches_deposits"]);

function hrefOf(item: ApiItem): string {
  const base = `/merchants/${encodeURIComponent(item.tenant_id)}`;
  if (item.kind === "across") return `${base}?tab=findings`;
  return ON_FILES_TAB.has(item.check) ? `${base}?tab=files` : base;
}

export function openFor(days: number | null): string {
  if (days === null) return "";
  if (days === 0) return "Today";
  return days === 1 ? "1 day" : `${days.toLocaleString("en-US")} days`;
}

/** Lines drawn at most; the rest are counted. */
export const LINES_SHOWN = 200;

export function shownOf(answer: ApiAnswer | null, merchantId: string | null = null, names: Record<string, string> = {}): Shown {
  if (!answer) return { state: "unavailable" };
  const lines = answer.data.slice(0, LINES_SHOWN).map((item): Line => ({
    key: `${item.tenant_id}:${item.key}`,
    href: hrefOf(item),
    merchantId: item.tenant_id,
    merchant: asWritten(item.merchant),
    check: item.title,
    what: what(item),
    refs: refs(item),
    amount: item.amount_minor === null ? "" : formatMinor(item.amount_minor, item.currency),
    dated: dayOf(item.dated_on),
    open: openFor(item.open_days),
    days: item.open_days,
    firstSeen: item.first_seen ? dayOf(item.first_seen) : "",
  }));
  const known = new Map(Object.entries(names));
  for (const i of answer.data) known.set(i.tenant_id, i.merchant);
  const listed = new Map<string, number>();
  for (const i of answer.data) listed.set(`${i.tenant_id}:${i.check}`, (listed.get(`${i.tenant_id}:${i.check}`) ?? 0) + 1);
  const notices: Notice[] = [];
  const more = new Map<string, number>();
  for (const c of answer.checks) {
    const merchant = asWritten(known.get(c.tenant_id) ?? "");
    const unlisted = c.open - (listed.get(`${c.tenant_id}:${c.check}`) ?? 0);
    if (!merchantId && unlisted > 0) more.set(c.tenant_id, (more.get(c.tenant_id) ?? 0) + unlisted);
    if (c.last_run && c.last_run.status !== "ok") {
      notices.push({
        merchantId: c.tenant_id, merchant,
        text: c.findings_from
          ? `${c.title}: the newest run did not finish. These items are from the run before it.`
          : `${c.title}: the newest run did not finish, and no earlier one did.`,
      });
    }
    if (c.cut) {
      notices.push({ merchantId: c.tenant_id, merchant, text: `${c.title}: the run found more than it kept. Only the first items are here.` });
    }
  }
  for (const [tid, n] of more) {
    notices.push({
      merchantId: tid, merchant: asWritten(known.get(tid) ?? ""),
      text: `${n.toLocaleString("en-US")} more open item${n === 1 ? "" : "s"} not in this list.`,
      href: `/exceptions?merchant=${encodeURIComponent(tid)}`, link: "See all of them",
    });
  }
  return {
    state: "ok",
    lines,
    total: answer.total,
    left: Math.max(answer.total - lines.length, 0),
    notices,
    checks: answer.checks.length,
    asOf: answer.as_of,
    merchant: merchantId ? { id: merchantId, name: asWritten(known.get(merchantId) ?? "") } : null,
    nextHref: !merchantId && answer.next ? `/exceptions?after=${encodeURIComponent(answer.next)}` : null,
  };
}
