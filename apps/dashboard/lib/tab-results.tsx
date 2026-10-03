"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import type { Finding, RunDigest } from "./finding";

/**
 * The last result per merchant that this browser tab has seen: Overview and
 * Findings report every run they made, a merchant's Run button reports its
 * own; Test and Save reports each credential test. Memory only, gone on
 * reload, never sent anywhere. It lets the sidebar badge, the Merchants
 * list and the rail tiles show a result without running anything: results
 * are not persisted server-side, and every run is metered.
 */
let results: Record<string, RunDigest> = {};
/** Findings this tab has seen, for order search in the palette. */
export type SeenFinding = { merchantId: string; merchantName: string; order: string; outcome: string; stake: string };
let findings: Record<string, SeenFinding[]> = {};
/** The last credential test per `${tenant}:${connector}`, same lifetime. */
let tests: Record<string, TestResult> = {};
const listeners = new Set<() => void>();
const EMPTY = {};

export type TestResult = {
  status: "rows" | "empty" | "failed" | "missing" | "untestable";
  operation?: string;
  rows?: number;
  detail: string;
};

function emit() {
  for (const l of listeners) l();
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function reportTest(tenant: string, connector: string, result: TestResult) {
  tests = { ...tests, [`${tenant}:${connector}`]: result };
  emit();
}

export function useTabTests(): Record<string, TestResult> {
  return useSyncExternalStore(subscribe, () => tests, () => EMPTY);
}

/** Keeps the newest result per merchant: a page re-rendering an older
 *  cached run must not overwrite a fresh run from the merchant's page. */
/**
 * Server timestamps on the browser's clock. Results made on the server
 * carry its Date.now(); results from the Run button carry the browser's.
 * Comparing them raw lets a skewed clock pick the older one, so every
 * server `at` is shifted by (browser now − server now) before comparing.
 */
export function rebase(at: number, serverNow: number): number {
  // The offset is fixed the first time a payload is seen: Back/Forward
  // restore a cached page with its old serverNow, and measuring then would
  // add the whole time since and make an old result look new.
  let off = offsets.get(serverNow);
  if (off === undefined) {
    off = Date.now() - serverNow;
    offsets.set(serverNow, off);
  }
  return at + off;
}

/** Clock offset per server payload (keyed by its serverNow), set on arrival. */
const offsets = new Map<number, number>();

/** The merchant page's last Run per merchant, same lifetime. */
let lastRuns: Record<string, { finding: Finding; at: number }> = {};

export function reportRun(id: string, finding: Finding, at: number) {
  lastRuns = { ...lastRuns, [id]: { finding, at } };
}

export function lastRun(id: string): { finding: Finding; at: number } | undefined {
  return lastRuns[id];
}

/** Is `d` at least as new as `prev`? Two server results compare on the
 *  server's own clock (re-basing the same instant on two page loads gives
 *  slightly different times); anything else on the browser's clock. */
function newer(d: RunDigest, prev: RunDigest | undefined): boolean {
  if (!prev) return true;
  if (d.origin === "server" && prev.origin === "server" && d.rawAt !== undefined && prev.rawAt !== undefined) {
    return d.rawAt >= prev.rawAt;
  }
  return d.at >= prev.at;
}

/** The newer of a tab-store digest and a server-cached one (read with the
 *  server's clock at `serverNow`). */
export function pickNewer(fromTab: RunDigest | undefined, cached: RunDigest | null, serverNow: number | null): RunDigest | null {
  if (!fromTab) return cached;
  if (!cached) return fromTab;
  if (fromTab.origin === "server" && fromTab.rawAt !== undefined) return fromTab.rawAt >= cached.at ? fromTab : cached;
  return fromTab.at >= (serverNow === null ? cached.at : rebase(cached.at, serverNow)) ? fromTab : cached;
}

export function report(digests: RunDigest[]) {
  const fresh = digests.filter((d) => newer(d, results[d.id]));
  if (!fresh.length) return;
  results = { ...results, ...Object.fromEntries(fresh.map((d) => [d.id, d])) };
  emit();
}

export function useTabResults(): Record<string, RunDigest> {
  return useSyncExternalStore(subscribe, () => results, () => EMPTY);
}

export function useTabFindings(): Record<string, SeenFinding[]> {
  return useSyncExternalStore(subscribe, () => findings, () => EMPTY);
}

/**
 * Rendered by a server page to hand its runs (and, optionally, their
 * findings, keyed by merchant) to the store. When this tab ran a merchant
 * again after the check the page shows (the server reuses a check for up
 * to 10 minutes), it says so rather than let two numbers disagree silently.
 */
export function ReportRuns({ digests, seen, serverNow }: {
  digests: RunDigest[];
  seen?: Record<string, SeenFinding[]>;
  serverNow: number;
}) {
  const [stale, setStale] = useState<string[]>([]);
  useEffect(() => {
    const local = digests.map((d) => ({ ...d, origin: "server" as const, rawAt: d.at, at: rebase(d.at, serverNow) }));
    // Stale only against this tab's own Run: another page-load check of the
    // same result is not "newer".
    const ranSince = (d: RunDigest) => results[d.id]?.origin === "tab" && results[d.id].at > d.at;
    setStale(local.filter(ranSince).map((d) => d.name ?? d.id));
    report(local);
    if (seen) {
      // Keep the findings of a merchant this tab ran more recently.
      const fresh = Object.fromEntries(Object.entries(seen).filter(([id]) => !local.some((d) => d.id === id && ranSince(d))));
      findings = { ...findings, ...fresh };
      emit();
    }
  }, [digests, seen, serverNow]);
  if (!stale.length) return null;
  return (
    <p role="status" className="mb-4 rounded-xl border border-line bg-surface px-4 py-2.5 text-sm">
      <span aria-hidden className="mr-1.5 font-mono">↻</span>
      {stale.join(", ")} {stale.length === 1 ? "was" : "were"} run again on {stale.length === 1 ? "its" : "their"} merchant page
      after the check shown here. This page reuses a check for up to 10 minutes; the newer result is on the merchant page.
    </p>
  );
}
