"use client";

import { useEffect, useSyncExternalStore } from "react";
import type { RunDigest } from "./finding";

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
export function report(digests: RunDigest[]) {
  const newer = digests.filter((d) => !results[d.id] || d.at >= results[d.id].at);
  if (!newer.length) return;
  results = { ...results, ...Object.fromEntries(newer.map((d) => [d.id, d])) };
  emit();
}

export function useTabResults(): Record<string, RunDigest> {
  return useSyncExternalStore(subscribe, () => results, () => EMPTY);
}

export function useTabFindings(): Record<string, SeenFinding[]> {
  return useSyncExternalStore(subscribe, () => findings, () => EMPTY);
}

/** Rendered by a server page to hand its runs (and, optionally, their
 *  findings, keyed by merchant) to the store. Renders nothing. */
export function ReportRuns({ digests, seen }: { digests: RunDigest[]; seen?: Record<string, SeenFinding[]> }) {
  useEffect(() => {
    report(digests);
    if (seen) {
      findings = { ...findings, ...seen };
      emit();
    }
  }, [digests, seen]);
  return null;
}
