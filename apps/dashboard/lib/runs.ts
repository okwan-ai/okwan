import "server-only";
import { cache } from "react";
import { api, session } from "./api";
import { type AcrossPage, ATTENTION, digestOf, type Finding, type FindingRow, type RunDigest, toFinding } from "./finding";
import { merchantsWithRails, type MerchantRails } from "./merchants";

/**
 * Overview and Findings need results, and results are not persisted. Each
 * render runs the `rails` fold once per merchant with two or more rails
 * ready, in parallel, through the same session route the Run button uses
 * (POST /v1/tenants/{id}/reconciliations/across/rails). Every run is metered
 * to its merchant, so each page load costs one request per eligible
 * merchant. Cached for the render only; nothing is kept.
 *
 * A failed run never fails the page: that merchant reads "Couldn't run"
 * with the API's own status and detail.
 */
export type MerchantRun =
  | { merchant: MerchantRails; state: "skipped" }
  | { merchant: MerchantRails; state: "ok"; finding: Finding }
  | { merchant: MerchantRails; state: "failed"; status: number; detail: string };

export const FOLD = "rails";

export function eligible(m: MerchantRails): boolean {
  return m.known && m.ready.length >= 2;
}

export const runAll = cache(async (): Promise<MerchantRun[] | null> => {
  const [merchants, token] = await Promise.all([merchantsWithRails(), session()]);
  if (!merchants) return null;
  return Promise.all(
    merchants.map(async (merchant): Promise<MerchantRun> => {
      if (!eligible(merchant)) return { merchant, state: "skipped" };
      const r = await api<AcrossPage>(
        `/v1/tenants/${encodeURIComponent(merchant.tenant.id)}/reconciliations/across/${FOLD}?limit=1000`,
        { method: "POST", session: token },
      );
      return r.ok
        ? { merchant, state: "ok", finding: toFinding(r.data) }
        : { merchant, state: "failed", status: r.status, detail: r.detail };
    }),
  );
});

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
