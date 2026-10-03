import "server-only";
import { cache } from "react";
import { api, session } from "./api";
import { type AcrossPage, ATTENTION, digestOf, type Finding, type FindingRow, type RunDigest, toFinding } from "./finding";
import { merchantsWithRails, type MerchantRails } from "./merchants";

/**
 * Overview and Findings need results, and results are not persisted. A
 * render runs the `rails` fold for each merchant with two or more rails
 * ready, in parallel, through the same session route the Run button uses
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
  | { merchant: MerchantRails; state: "failed"; status: number; detail: string };

export const FOLD = "rails";

export function eligible(m: MerchantRails): boolean {
  return m.known && m.ready.length >= 2;
}

/** How long a successful run is reused before a page load runs it again. */
export const FRESH_MS = 10 * 60 * 1000;

/** Keyed by merchant, fold and ready rails. Read only after the merchant
 * list, which is fetched per request under the caller's session, has
 * shown the caller can see that merchant. */
const recent = new Map<string, { at: number; finding: Finding }>();

function keyOf(m: MerchantRails): string {
  return `${m.tenant.id}:${FOLD}:${[...m.ready].sort().join(",")}`;
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
      const r = await api<AcrossPage>(
        `/v1/tenants/${encodeURIComponent(merchant.tenant.id)}/reconciliations/across/${FOLD}?limit=1000`,
        { method: "POST", session: token },
      );
      if (!r.ok) return { merchant, state: "failed", status: r.status, detail: r.detail };
      const finding = toFinding(r.data);
      const at = Date.now();
      remember(key, finding, at);
      return { merchant, state: "ok", finding, at };
    }),
  );
});

/** The oldest result a page is showing, for "checked N min ago". */
export function oldestAt(runs: MerchantRun[]): number | null {
  const ats = runs.flatMap((r) => (r.state === "ok" ? [r.at] : []));
  return ats.length ? Math.min(...ats) : null;
}

export function checkedAgo(at: number | null): string {
  if (at === null) return "";
  const min = Math.floor((Date.now() - at) / 60000);
  return min < 1 ? "Checked just now" : `Checked ${min} min ago`;
}

export type AttentionRow = FindingRow & { merchantId: string; merchantName: string };

/** Every money finding across merchants, worst first, largest first. */
export function attentionRows(runs: MerchantRun[]): AttentionRow[] {
  return runs
    .flatMap((r) =>
      r.state === "ok"
        ? r.finding.rows
          .filter((row) => ATTENTION.includes(row.outcome))
          .map((row) => ({ ...row, merchantId: r.merchant.tenant.id, merchantName: r.merchant.tenant.name }))
        : [],
    )
    .sort((a, b) => ATTENTION.indexOf(a.outcome) - ATTENTION.indexOf(b.outcome)
      || (b.total_minor ?? 0) - (a.total_minor ?? 0));
}

/** Collected-twice totals, per currency. A merchant whose double
 * collections span currencies has no single total and counts as mixed. */
export function twiceTotals(runs: MerchantRun[]) {
  const byCurrency = new Map<string, number>();
  let orders = 0;
  let merchants = 0;
  let mixed = false;
  for (const r of runs) {
    if (r.state !== "ok" || r.finding.summary.collected_twice === 0) continue;
    const s = r.finding.summary;
    orders += s.collected_twice;
    merchants += 1;
    const cur = r.finding.twice_currency;
    if (cur === null) mixed = true;
    else byCurrency.set(cur.toUpperCase(), (byCurrency.get(cur.toUpperCase()) ?? 0) + s.collected_twice_minor);
  }
  return { byCurrency, orders, merchants, mixed };
}

export function digest(r: MerchantRun): RunDigest | null {
  const id = r.merchant.tenant.id;
  if (r.state === "skipped") return null;
  if (r.state === "failed") return { id, ok: false, open: 0, twice: 0, twiceMinor: 0, currency: null, detail: r.detail };
  return digestOf(id, r.finding);
}
