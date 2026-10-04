"use client";

import { useSyncExternalStore } from "react";

/**
 * The last credential test per `${tenant}:${connector}` that this browser
 * tab has made. Memory only, gone on reload, never sent anywhere: a test
 * is a probe, not a result worth keeping. Reconciliation results are
 * stored by the API and read by every page; nothing about them lives here.
 */
let tests: Record<string, TestResult> = {};
const listeners = new Set<() => void>();
const EMPTY = {};

export type TestResult = {
  status: "rows" | "empty" | "failed" | "missing" | "untestable";
  operation?: string;
  rows?: number;
  detail: string;
};

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function reportTest(tenant: string, connector: string, result: TestResult) {
  tests = { ...tests, [`${tenant}:${connector}`]: result };
  for (const l of listeners) l();
}

export function useTabTests(): Record<string, TestResult> {
  return useSyncExternalStore(subscribe, () => tests, () => EMPTY);
}
