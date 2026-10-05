"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, type ReactNode, useCallback, useContext, useId, useState } from "react";
import { type Finding, railLabel } from "@/lib/finding";
import { type MerchantTab, tabOf } from "@/lib/merchant-tabs";
import { formatMinor } from "@/lib/money";
import { Button, buttonClass } from "./ui/button";
import { IconPlay } from "./ui/icons";

/** A result as the merchant page holds it: a stored run, or the run this
 *  page just made (which the API stored too). */
export type Shown = {
  finding: Finding;
  /** When the run finished (ms epoch). */
  at: number;
  runId: string | null;
  /** dashboard · rest · mcp */
  surface: string;
};

type RunState = {
  tenantId: string;
  tenantName: string;
  busy: boolean;
  shown: Shown | null;
  error: { status: number; detail: string; at: number | null } | null;
  /** Rails the fold reads that this merchant hasn't connected. */
  missing: string[];
  /** The id of the run this page made, so its result can settle into
   *  place once; a stored run shown on load does not animate. */
  justRan: string | null;
  /** Where a bare merchant URL opens (lib/merchant-tabs defaultTab). */
  defaultTab: MerchantTab;
  run: () => Promise<void>;
};

const Ctx = createContext<RunState | null>(null);

export function useMerchantRun(): RunState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useMerchantRun outside MerchantRunProvider");
  return v;
}

/**
 * One merchant's run, held in the merchant layout so it survives switching
 * tabs (each tab is a URL, and the page beneath remounts). The newest
 * stored run arrives as `initial`; Run makes a fresh, metered run through
 * the session route, which the API stores, and then refreshes the page so
 * the history and the sidebar read it back.
 */
export function MerchantRunProvider({ tenantId, tenantName, fold = "rails", initial = null, initialError = null, missing = [], defaultTab = "findings", children }: {
  tenantId: string;
  tenantName: string;
  fold?: string;
  defaultTab?: MerchantTab;
  missing?: string[];
  /** The newest stored run, read by the layout; shown without running. */
  initial?: Shown | null;
  /** The newest stored run failed: its scrubbed error and when. */
  initialError?: { detail: string; at: number } | null;
  children: ReactNode;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [shown, setShown] = useState<Shown | null>(initial);
  const [error, setError] = useState<RunState["error"]>(initialError ? { status: 0, ...initialError } : null);
  const [justRan, setJustRan] = useState<string | null>(null);
  // Spoken progress: a run takes seconds and ends somewhere else on the page.
  const [said, setSaid] = useState("");

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    setSaid("Reading Shopify orders, then PayPal and Stripe.");
    const res = await fetch(
      `/api/merchants/${encodeURIComponent(tenantId)}/across/${encodeURIComponent(fold)}`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    ).catch(() => null);
    const data = res ? await res.json().catch(() => ({})) : {};
    setBusy(false);
    if (!res || !res.ok) {
      const detail = data.detail ?? "the dashboard couldn't reach the API";
      setError({ status: res?.status ?? 0, detail, at: Date.now() });
      setSaid(`The check didn't finish: ${detail}`);
      router.refresh();
      return;
    }
    const f = data as Finding & { run_id: string | null; at: number };
    setShown({ finding: f, at: f.at ?? Date.now(), runId: f.run_id ?? null, surface: "dashboard" });
    setJustRan(f.run_id ?? null);
    const twice = f.summary.collected_twice;
    setSaid(`Check finished and saved: ${f.summary.orders} orders checked, ${twice} collected twice${
      twice && f.twice_currency ? ` (${formatMinor(f.summary.collected_twice_minor, f.twice_currency)})` : ""}.`);
    // The API stored the run; the history, the sidebar and the other pages
    // read it on the next render.
    router.refresh();
  }, [tenantId, fold, router]);

  return (
    <Ctx.Provider value={{ tenantId, tenantName, busy, shown, error, missing, justRan, defaultTab, run }}>
      <p role="status" className="sr-only">{said}</p>
      {children}
    </Ctx.Provider>
  );
}

/**
 * The header's one action (primitives, RunButton matrix). While a system
 * the check reads is missing there is no Run button, because the API would
 * fail the run: it leads to the next missing system instead, and on the
 * Connections tab renders nothing (CheckReadiness carries the volt). Ready
 * and never run, or the last check failed: a primary Run check. With a
 * result shown: a secondary Run again (the VerdictCard holds the volt).
 */
export function RunButton() {
  const { busy, shown, missing, run, defaultTab } = useMerchantRun();
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  const note = useId();
  const runParam = params.get("run");
  const effective = tabOf(params.get("tab"), defaultTab, Boolean(params.get("order") || runParam));

  if (missing.length) {
    if (effective === "connections") return null;
    return (
      <Link
        href={`${path}?tab=connections&connect=${encodeURIComponent(missing[0])}`}
        scroll={false}
        className={buttonClass(shown ? "secondary" : "primary", "max-sm:w-full")}
      >
        Connect {railLabel(missing[0])}
      </Link>
    );
  }
  return (
    <div className="flex flex-col items-start gap-1 max-sm:w-full sm:items-end">
      <Button
        variant={shown ? "secondary" : "primary"}
        className="max-sm:w-full"
        disabled={busy}
        aria-busy={busy}
        aria-describedby={note}
        onClick={() => {
          // The result lands on Findings, at the newest check: another tab,
          // or an older check (?run=) chosen from the history, would hide it.
          if (effective !== "findings" || runParam) router.push(`${path}?tab=findings`, { scroll: false });
          void run();
        }}
      >
        <IconPlay className="h-4 w-4" />
        {busy ? "Checking…" : shown ? "Run again" : "Run check"}
      </Button>
      <p id={note} className="text-xs text-ink-soft">1 request · result saved</p>
    </div>
  );
}
