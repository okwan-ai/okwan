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

export function report(digests: RunDigest[]) {
  if (!digests.length) return;
  results = { ...results, ...Object.fromEntries(digests.map((d) => [d.id, d])) };
  emit();
}

export function useTabResults(): Record<string, RunDigest> {
  return useSyncExternalStore(subscribe, () => results, () => EMPTY);
}

/** Rendered by a server page to hand its runs to the store. Renders nothing. */
export function ReportRuns({ digests }: { digests: RunDigest[] }) {
  useEffect(() => report(digests), [digests]);
  return null;
}
