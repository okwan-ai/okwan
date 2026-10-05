import "server-only";
import { cache } from "react";
import {
  type AttentionRow, ATTENTION, atStake, digestOf, failedDigest, type Finding, missingFor, type RunDigest, toFinding,
  truncated,
} from "./finding";
import { formatMinor } from "./money";
import { merchantsWithRails, type MerchantRails } from "./merchants";
import { myLatestRuns, type StoredRun } from "./stored-runs";

/**
 * Overview and Findings read each merchant's newest stored run. Nothing
 * runs on a page load any more: a run happens when someone presses Run
 * (one merchant, or Run all), or when an agent or a REST caller runs the
 * fold with the merchant's key, and every one of those is stored by the
 * API. A page therefore shows what was last found and when, however the
 * run was made, and costs no request to open.
 */
export type MerchantRun =
  /** Not every rail the fold reads is connected. */
  | { merchant: MerchantRails; state: "skipped" }
  /** Ready, but never run. */
  | { merchant: MerchantRails; state: "none" }
  | { merchant: MerchantRails; state: "ok"; finding: Finding; at: number; runId: string; surface: string }
  | { merchant: MerchantRails; state: "failed"; status: number; detail: string; at: number; runId: string; surface: string };

export { FOLD } from "./stored-runs";

/** Ready to check: every rail the fold reads is connected (FOLD_READS). */
export function eligible(m: MerchantRails): boolean {
  return m.known && missingFor(m).length === 0;
}

/** A stored run as a page carries it. A merchant that is not ready is
 *  "skipped" even if an older run exists: the fold could not run today. */
export function fromStored(merchant: MerchantRails, run: StoredRun | undefined): MerchantRun {
  if (!eligible(merchant)) return { merchant, state: "skipped" };
  if (!run) return { merchant, state: "none" };
  const at = Date.parse(run.finished_at);
  if (run.status === "failed" || !run.summary) {
    return { merchant, state: "failed", status: 0, detail: run.error ?? "the run failed", at, runId: run.id, surface: run.surface };
  }
  return {
    merchant,
    state: "ok",
    finding: toFinding({ summary: run.summary, rows: run.rows ?? [], has_more: run.has_more ?? false, twice_currency: run.twice_currency }),
    at,
    runId: run.id,
    surface: run.surface,
  };
}

/** Every merchant with its newest stored run. Reads only; cached per render. */
export const storedRuns = cache(async (): Promise<MerchantRun[] | null> => {
  const [merchants, latest] = await Promise.all([merchantsWithRails(), myLatestRuns()]);
  if (!merchants || !latest) return null;
  return merchants.map((m) => fromStored(m, latest[m.tenant.id]));
});

/** The oldest result a page is showing, for "last run N ago". */
export function oldestAt(runs: MerchantRun[]): number | null {
  const ats = runs.flatMap((r) => (r.state === "ok" ? [r.at] : []));
  return ats.length ? Math.min(...ats) : null;
}

export { ranAgo } from "./finding";
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
    // A total order: worst outcome, then currency, then largest first within
    // that currency (amounts never compare across currencies), then merchant.
    .sort((a, b) => ATTENTION.indexOf(a.outcome) - ATTENTION.indexOf(b.outcome)
      || (a.currency ?? "").localeCompare(b.currency ?? "")
      || (b.total_minor ?? 0) - (a.total_minor ?? 0)
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
  const never = runs.filter((r) => r.state === "none").length;
  if (never) out.push(`${never} ready merchant${never === 1 ? "" : "s"} never run`);
  const unverifiable = runs.reduce((n, r) => n + (r.state === "ok" ? r.finding.summary.unverifiable : 0), 0);
  if (unverifiable) out.push(`${unverifiable.toLocaleString("en-US")} order${unverifiable === 1 ? "" : "s"} couldn't be verified`);
  const cut = runs.filter((r) => r.state === "ok" && (r.finding.partial || truncated(r.finding))).length;
  if (cut) out.push(`${cut} merchant${cut === 1 ? "'s" : "s'"} results were cut short`);
  return out;
}

export function digest(r: MerchantRun): RunDigest | null {
  const id = r.merchant.tenant.id;
  if (r.state === "skipped" || r.state === "none") return null;
  const d = r.state === "failed" ? failedDigest(id, r.detail, r.at) : digestOf(id, r.finding, r.at);
  return { ...d, name: r.merchant.tenant.name, surface: r.surface };
}

/** Findings per merchant in the shape the palette's order search needs. */
export function seenFindings(runs: MerchantRun[]) {
  return runs.flatMap((r) => (r.state === "ok"
    ? r.finding.rows.filter((x) => ATTENTION.includes(x.outcome)).map((x) => {
      const s = atStake(x);
      return {
        merchantId: r.merchant.tenant.id,
        merchantName: r.merchant.tenant.name,
        order: x.order,
        outcome: x.outcome,
        stake: s ? `${formatMinor(s.minor, x.currency)} ${s.label}` : "",
      };
    })
    : []));
}
