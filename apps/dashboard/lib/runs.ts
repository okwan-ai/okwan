import "server-only";
import { cache } from "react";
import { api, session } from "./api";
import {
  type AcrossPage, ATTENTION, type AttentionRow, atStake, digestOf, failedDigest, type Finding, missingFor, type RunDigest, toFinding,
  truncated,
} from "./finding";
import { formatMinor } from "./money";
import { merchantsWithRails, type MerchantRails } from "./merchants";

/**
 * Overview and Findings need results, and results are not persisted. A
 * render runs the `rails` fold for each merchant with every rail the fold
 * reads connected, in parallel, through the same session route the Run button uses
 * (POST /v1/tenants/{id}/reconciliations/across/rails). Every successful run
 * is metered to its merchant, so a successful result is reused for FRESH_MS
 * from this server's memory: loading Overview then Findings, or reloading
 * either, costs nothing inside that window. The key includes the merchant's
 * ready rails, so connecting or removing a rail runs again. Failed runs are
 * not metered and not kept; they retry on the next load. The merchant
 * page's Run button always runs fresh.
 *
 * A failed run never fails the page: that merchant reads "Couldn't run"
 * with the API's own status and detail.
 */
export type MerchantRun =
  | { merchant: MerchantRails; state: "skipped" }
  | { merchant: MerchantRails; state: "ok"; finding: Finding; at: number }
  | { merchant: MerchantRails; state: "failed"; status: number; detail: string; at: number };

export const FOLD = "rails";

/** Ready to check: every rail the fold reads is connected (FOLD_READS). */
export function eligible(m: MerchantRails): boolean {
  return m.known && missingFor(m).length === 0;
}

/** How long a successful run is reused before a page load runs it again. */
export const FRESH_MS = 10 * 60 * 1000;

/** Keyed by merchant, fold and ready rails. Read only after the merchant
 * list, which is fetched per request under the caller's session, has
 * shown the caller can see that merchant. */
const recent = new Map<string, { at: number; finding: Finding }>();

/** Runs already on their way, so two loads at once share one metered run. */
const inflight = new Map<string, Promise<{ ok: true; finding: Finding; at: number } | { ok: false; status: number; detail: string }>>();

function keyOf(m: { tenant: { id: string }; ready: string[] }): string {
  return `${m.tenant.id}:${FOLD}:${[...m.ready].sort().join(",")}`;
}

/**
 * A result still fresh in this server's memory, without running anything.
 * Callers must have shown the caller may see `id` first (the merchant
 * layout does: its credentials read passed the API's subtree guard).
 */
export function cachedRun(id: string, ready: string[]): { finding: Finding; at: number } | null {
  const hit = recent.get(keyOf({ tenant: { id }, ready }));
  return hit && Date.now() - hit.at < FRESH_MS ? hit : null;
}

function remember(key: string, finding: Finding, at: number) {
  for (const [k, v] of recent) if (at - v.at >= FRESH_MS) recent.delete(k);
  recent.set(key, { at, finding });
}

export const runAll = cache(async (): Promise<MerchantRun[] | null> => {
  const [merchants, token] = await Promise.all([merchantsWithRails(), session()]);
  if (!merchants) return null;
  return Promise.all(
    merchants.map(async (merchant): Promise<MerchantRun> => {
      if (!eligible(merchant)) return { merchant, state: "skipped" };
      const key = keyOf(merchant);
      const hit = recent.get(key);
      if (hit && Date.now() - hit.at < FRESH_MS) return { merchant, state: "ok", ...hit };
      let pending = inflight.get(key);
      if (!pending) {
        pending = api<AcrossPage>(
          `/v1/tenants/${encodeURIComponent(merchant.tenant.id)}/reconciliations/across/${FOLD}?limit=1000`,
          { method: "POST", session: token },
        ).then((r) => {
          if (!r.ok) return { ok: false as const, status: r.status, detail: r.detail };
          const finding = toFinding(r.data);
          const at = Date.now();
          remember(key, finding, at);
          return { ok: true as const, finding, at };
        }).finally(() => inflight.delete(key));
        inflight.set(key, pending);
      }
      const r = await pending;
      return r.ok
        ? { merchant, state: "ok", finding: r.finding, at: r.at }
        : { merchant, state: "failed", status: r.status, detail: r.detail, at: Date.now() };
    }),
  );
});

/** The oldest result a page is showing, for "checked N min ago". */
export function oldestAt(runs: MerchantRun[]): number | null {
  const ats = runs.flatMap((r) => (r.state === "ok" ? [r.at] : []));
  return ats.length ? Math.min(...ats) : null;
}

export { checkedAgo } from "./finding";
export type { AttentionRow } from "./finding";

/** Every money finding across merchants, worst first, largest first. */
export function attentionRows(runs: MerchantRun[]): AttentionRow[] {
  return runs
    .flatMap((r) =>
      r.state === "ok"
        ? r.finding.rows
          .filter((row) => ATTENTION.includes(row.outcome))
          .map((row) => ({
            ...row,
            merchantId: r.merchant.tenant.id,
            merchantName: r.merchant.tenant.name,
            at: r.at,
            partial: r.finding.partial || truncated(r.finding),
          }))
        : [],
    )
    // Worst outcome first; amounts compare only within one currency.
    .sort((a, b) => ATTENTION.indexOf(a.outcome) - ATTENTION.indexOf(b.outcome)
      || (a.currency === b.currency ? (b.total_minor ?? 0) - (a.total_minor ?? 0) : 0)
      || a.merchantName.localeCompare(b.merchantName));
}

/** Collected-twice totals, per currency. A merchant whose double
 * collections span currencies has no single total and counts as mixed. */
export function twiceTotals(runs: MerchantRun[]) {
  const byCurrency = new Map<string, number>();
  /** overcollected_minor: what was taken beyond the order totals. */
  const owedByCurrency = new Map<string, number>();
  let orders = 0;
  let merchants = 0;
  let mixed = false;
  for (const r of runs) {
    if (r.state !== "ok" || r.finding.summary.collected_twice === 0) continue;
    const s = r.finding.summary;
    orders += s.collected_twice;
    merchants += 1;
    const cur = r.finding.twice_currency?.toUpperCase() ?? null;
    if (cur === null) {
      mixed = true;
      continue;
    }
    byCurrency.set(cur, (byCurrency.get(cur) ?? 0) + s.collected_twice_minor);
    owedByCurrency.set(cur, (owedByCurrency.get(cur) ?? 0) + s.overcollected_minor);
  }
  return { byCurrency, owedByCurrency, orders, merchants, mixed };
}

/** Why a clean-looking total might not be the whole story. */
export function caveats(runs: MerchantRun[]): string[] {
  const out: string[] = [];
  const failed = runs.filter((r) => r.state === "failed").length;
  if (failed) out.push(`${failed} merchant${failed === 1 ? "" : "s"} couldn't run`);
  const unverifiable = runs.reduce((n, r) => n + (r.state === "ok" ? r.finding.summary.unverifiable : 0), 0);
  if (unverifiable) out.push(`${unverifiable.toLocaleString("en-US")} order${unverifiable === 1 ? "" : "s"} couldn't be verified`);
  const cut = runs.filter((r) => r.state === "ok" && (r.finding.partial || truncated(r.finding))).length;
  if (cut) out.push(`${cut} merchant${cut === 1 ? "'s" : "s'"} results were cut short`);
  return out;
}


export function digest(r: MerchantRun): RunDigest | null {
  const id = r.merchant.tenant.id;
  if (r.state === "skipped") return null;
  if (r.state === "failed") return failedDigest(id, r.detail, r.at);
  return digestOf(id, r.finding, r.at);
}

/** Findings per merchant in the shape the palette's order search needs.
 *  Only merchants that ran: a merchant left out keeps what the tab had. */
export function seenFindings(runs: MerchantRun[]) {
  return Object.fromEntries(
    runs.flatMap((r) => (r.state === "ok"
      ? [[r.merchant.tenant.id, r.finding.rows.filter((x) => ATTENTION.includes(x.outcome)).map((x) => {
        const s = atStake(x);
        return {
          merchantId: r.merchant.tenant.id,
          merchantName: r.merchant.tenant.name,
          order: x.order,
          outcome: x.outcome,
          stake: s ? `${formatMinor(s.minor, x.currency)} ${s.label}` : "",
        };
      })]]
      : [])),
  );
}
