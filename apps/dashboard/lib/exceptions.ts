import { asWritten, dayOf, UNSEEN_CHAR } from "./file-checks";
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
 *
 * A person decides what an item means (C58): gives it to someone, explains
 * it, resolves it or opens it again, with a reason. Each line says what was
 * last decided and where a decision about it is sent; a name or a note is
 * that person's own text and is drawn like a reference.
 */

/** A decision as the API answers it. */
export type ApiDecision = {
  id: string;
  seq: number;
  status: "open" | "assigned" | "explained" | "resolved";
  prior_status: string | null;
  owner: string | null;
  note: string | null;
  by: { tenant_id: string; name: string; credential: string };
  at: string;
  run_id: string;
  /** What the item put at stake when it was decided. */
  on: { amount_minor: number | null; currency: string | null; dated_on: string | null } | null;
  /** For one that closed the item: the item has other terms now, or a twin, so it is open again. */
  changed?: boolean;
  changed_why?: "alike" | "terms";
};

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
  decision?: ApiDecision | null;
  /** Alike in every reference and day to another finding of the same result: it cannot be decided on its own. */
  alike?: boolean;
  /** What it puts at stake with its sign, which a decision sends back as what the person saw. */
  terms?: string;
  signed_minor?: number | null;
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
  /** How many of its findings a person has explained or resolved. */
  closed?: number;
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
  /** Where a decision about this item is sent, and its history read. */
  decide: string;
  /** What a person last decided about it, when anything was. */
  decision: DecisionShown | null;
  /** Set when the item cannot be decided on its own, and why. */
  undecidable: string | null;
  /** The item's terms as shown: sent with a decision, which the API refuses if they have changed. */
  terms: string | null;
};

/** A decision, every word chosen: who decided what, when and why. */
export type DecisionShown = {
  status: ApiDecision["status"];
  /** "Given to", "Explained", "Resolved", "Opened again". */
  said: string;
  owner: string | null;
  note: string | null;
  by: string;
  how: string;
  on: string;
  /** Set when the decision closed the item on other terms than it has now. */
  changed: string | null;
};

export type Notice = { merchantId: string; merchant: string; text: string; href?: string; link?: string };

export type Shown =
  | { state: "unavailable" }
  | { state: "ok"; lines: Line[]; total: number; left: number; notices: Notice[]; checks: number; asOf: string;
      /** Set when the list is one merchant's. */
      merchant: { id: string; name: string } | null;
      /** The account's list comes 50 merchants a page: the next page, if any. */
      nextHref: string | null;
      /** Open items, or one merchant's closed ones. */
      view: View;
      /** One merchant's list: how many of its items are closed, and the
       *  other view's address. */
      closed: number;
      otherHref: string | null };

export type View = "open" | "closed";

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

/** What each status says, in a person's words. */
export const SAID: Record<ApiDecision["status"], string> = {
  assigned: "Given to",
  explained: "Explained",
  resolved: "Resolved",
  open: "Opened again",
};

/** A person's text (a name, a note) as written, every character that
 *  cannot be seen spelled out. The API refuses them; this is the second
 *  wall. Not cut: the API holds a note to 1,000 characters. */
export function asNote(text: string): string {
  return Array.from(text)
    .map((c) => (c !== "\n" && UNSEEN_CHAR.test(c) ? `[U+${c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, "0")}]` : c))
    .join("");
}

/** How a decision was made: in the dashboard, or with an API key by its prefix. */
export function howMade(credential: string): string {
  return credential === "session" ? "in the dashboard" : `with the key ${asWritten(credential)}`;
}

export function decisionShown(d: ApiDecision, item?: Pick<ApiItem, "signed_minor">): DecisionShown {
  return {
    status: d.status,
    said: SAID[d.status] ?? "Decided",
    owner: d.owner ? asNote(d.owner) : null,
    note: d.note ? asNote(d.note) : null,
    by: asWritten(d.by.name),
    how: howMade(d.by.credential),
    on: dayOf(d.at),
    changed: d.changed ? changedSince(d, item?.signed_minor ?? null) : null,
  };
}

/** Why a closing decision no longer closes its item, in words that match
 *  what the line shows: another item is now alike to it; the same amount
 *  now runs the other way (over, where it was short); or it was made on
 *  another amount or day. */
function changedSince(d: ApiDecision, now: number | null): string {
  const said = SAID[d.status];
  if (d.changed_why === "alike") {
    return `${said} when it was the only item like it. Another item of its check is now alike to it in every `
      + "reference and day, so it is open again, and neither can be decided on its own.";
  }
  const was = d.on;
  const signed = was?.amount_minor ?? null;
  const amount = signed !== null ? formatMinor(Math.abs(signed), was?.currency ?? null) : "";
  if (signed !== null && now !== null && Math.abs(signed) === Math.abs(now) && Math.sign(signed) !== Math.sign(now)) {
    return `${said} when the same ${amount} ran the other way. It has turned around since, so it is open again.`;
  }
  const day = was?.dated_on ? dayOf(was.dated_on) : "";
  const terms = [amount, day && `dated ${day}`].filter(Boolean).join(", ");
  return `${said} when it was ${terms || "something else"}. It has changed since, so it is open again.`;
}

/** The dashboard's own route for one item's decisions. Every part is the
 *  API's own value, each encoded. */
export function decidePath(item: Pick<ApiItem, "tenant_id" | "kind" | "check" | "key">): string {
  return [
    "/api/merchants", encodeURIComponent(item.tenant_id), "exceptions", encodeURIComponent(item.kind),
    encodeURIComponent(item.check), encodeURIComponent(item.key), "decisions",
  ].join("/");
}

export function openFor(days: number | null): string {
  if (days === null) return "";
  if (days === 0) return "Today";
  return days === 1 ? "1 day" : `${days.toLocaleString("en-US")} days`;
}

/** Lines drawn at most; the rest are counted. */
export const LINES_SHOWN = 200;

export function shownOf(
  answer: ApiAnswer | null, merchantId: string | null = null, names: Record<string, string> = {}, view: View = "open",
): Shown {
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
    decide: decidePath(item),
    decision: item.decision ? decisionShown(item.decision, item) : null,
    terms: item.terms ?? null,
    undecidable: item.alike
      ? "Alike in every reference and day to another item of the same check, so neither can be decided on its own. Settle it where it started."
      : null,
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
    if (!merchantId && view === "open" && unlisted > 0) more.set(c.tenant_id, (more.get(c.tenant_id) ?? 0) + unlisted);
    if (c.last_run && c.last_run.status !== "ok") {
      notices.push({
        merchantId: c.tenant_id, merchant,
        text: c.findings_from
          ? `${c.title}: the newest run did not finish. These items are from the run before it.`
          : `${c.title}: the newest run did not finish, and no earlier one did.`,
      });
    }
    if (c.cut) {
      notices.push({
        merchantId: c.tenant_id, merchant,
        text: `${c.title}: the run found more than it kept. Only the first items are here, and the count may include items someone already closed.`,
      });
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
    view,
    closed: answer.checks.reduce((n, c) => n + (c.closed ?? 0), 0),
    otherHref: merchantId
      ? `/exceptions?merchant=${encodeURIComponent(merchantId)}${view === "open" ? "&state=closed" : ""}`
      : null,
  };
}
