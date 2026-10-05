"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

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

/**
 * The arrival store: which systems just connected in this tab, so each
 * surface that shows them can pop once (OKWAN_PROJECT.md §9 2026-10-05).
 * Same rules as the tests above: memory only, so a reload or a revisit
 * never replays a pop. Each surface has its own flag, keyed
 * `${surface}:${tenantKey}:${name}`, and only clears its own: the grid
 * tile's animationend can never cancel the header's pop. While a tenant's
 * connect sheet is open nothing behind it pops; it plays when the sheet
 * closes and the refreshed props say connected.
 */
export type Surface = "grid" | "header";

let arrived: Readonly<Record<string, true>> = {};
let sheets: Readonly<Record<string, true>> = {};

/** How long a flag may outlive its pop: past the 420ms animation plus the
 *  disc's 180ms delay. Under reduced motion animationend never fires, so
 *  this is what clears it. */
const POP_FALLBACK_MS = 700;

function notify() {
  for (const l of listeners) l();
}

function without<T extends Record<string, true>>(map: T, key: string): T {
  if (!(key in map)) return map;
  const rest = { ...map };
  delete rest[key];
  return rest;
}

/** The connect that caused it: every surface showing this system pops once. */
export function markArrived(tenantKey: string, name: string) {
  arrived = { ...arrived, [`grid:${tenantKey}:${name}`]: true, [`header:${tenantKey}:${name}`]: true };
  notify();
}

export function clearArrived(surface: Surface, tenantKey: string, name: string) {
  const next = without(arrived, `${surface}:${tenantKey}:${name}`);
  if (next === arrived) return;
  arrived = next;
  notify();
}

export function useArrived(surface: Surface, tenantKey: string | undefined, name: string): boolean {
  const key = `${surface}:${tenantKey}:${name}`;
  return useSyncExternalStore(subscribe, () => !!tenantKey && key in arrived, () => false);
}

export function setSheetOpen(tenantKey: string, open: boolean) {
  const next = open ? (tenantKey in sheets ? sheets : { ...sheets, [tenantKey]: true as const }) : without(sheets, tenantKey);
  if (next === sheets) return;
  sheets = next;
  notify();
}

export function useSheetOpen(tenantKey: string | undefined): boolean {
  return useSyncExternalStore(subscribe, () => !!tenantKey && tenantKey in sheets, () => false);
}

/**
 * Whether this surface's logo should play its arrival now: the system
 * arrived in this tab, this surface's own props say it is connected
 * (`ready`), and the tenant's sheet is closed. A late refresh pops when it
 * lands; a failed one never pops. The flag clears on animationend or after
 * POP_FALLBACK_MS, whichever comes first.
 */
export function usePop(surface: Surface, tenantKey: string | undefined, name: string, ready: boolean): { pop: boolean; onAnimationEnd: () => void } {
  const isArrived = useArrived(surface, tenantKey, name);
  const sheetOpen = useSheetOpen(tenantKey);
  const pop = Boolean(tenantKey) && isArrived && ready && !sheetOpen;
  useEffect(() => {
    if (!pop || !tenantKey) return;
    const t = setTimeout(() => clearArrived(surface, tenantKey, name), POP_FALLBACK_MS);
    return () => clearTimeout(t);
  }, [pop, surface, tenantKey, name]);
  const onAnimationEnd = useCallback(() => {
    if (tenantKey) clearArrived(surface, tenantKey, name);
  }, [surface, tenantKey, name]);
  return { pop, onAnimationEnd };
}
