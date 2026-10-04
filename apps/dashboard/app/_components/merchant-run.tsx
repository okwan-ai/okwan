"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, type ReactNode, useCallback, useContext, useState } from "react";
import { type Finding, railLabel } from "@/lib/finding";
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
export function MerchantRunProvider({ tenantId, tenantName, fold = "rails", initial = null, initialError = null, missing = [], children }: {
  tenantId: string;
  tenantName: string;
  fold?: string;
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
  // Spoken progress: a run takes seconds and ends somewhere else on the page.
  const [said, setSaid] = useState("");

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    setSaid("Reading the ledger, then each rail.");
    const res = await fetch(
      `/api/merchants/${encodeURIComponent(tenantId)}/across/${encodeURIComponent(fold)}`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    ).catch(() => null);
    const data = res ? await res.json().catch(() => ({})) : {};
    setBusy(false);
    if (!res || !res.ok) {
      const detail = data.detail ?? "the dashboard couldn't reach the API";
      setError({ status: res?.status ?? 0, detail, at: Date.now() });
      setSaid(`The run didn't finish: ${detail}`);
      router.refresh();
      return;
    }
    const f = data as Finding & { run_id: string | null; at: number };
    setShown({ finding: f, at: f.at ?? Date.now(), runId: f.run_id ?? null, surface: "dashboard" });
    const twice = f.summary.collected_twice;
    setSaid(`Run finished and stored: ${f.summary.orders} orders checked, ${twice} collected twice${
      twice && f.twice_currency ? ` (${formatMinor(f.summary.collected_twice_minor, f.twice_currency)})` : ""}.`);
    // The API stored the run; the history, the sidebar and the other pages
    // read it on the next render.
    router.refresh();
  }, [tenantId, fold, router]);

  return (
    <Ctx.Provider value={{ tenantId, tenantName, busy, shown, error, missing, run }}>
      <p role="status" className="sr-only">{said}</p>
      {children}
    </Ctx.Provider>
  );
}

/** The header's primary action. Runs, and shows the Findings tab. */
export function RunButton() {
  const { busy, shown, missing, run } = useMerchantRun();
  const router = useRouter();
  const path = usePathname();
  const tab = useSearchParams().get("tab");
  // A run with a rail missing fails on the API (and would read as a broken
  // product); lead to what's missing instead.
  if (missing.length && !shown) {
    return (
      <Link href={`${path}?tab=connections&connect=${missing[0]}`} scroll={false} className={buttonClass(tab === "connections" || tab === "keys" ? "secondary" : "primary")}>
        Connect {missing.map(railLabel).join(" + ")}
      </Link>
    );
  }
  return (
    <Button
      // One volt element per view: once a result exists the band carries it,
      // a re-run (metered) is secondary, and on the keys tab issuing a key is
      // the primary action.
      variant={shown || tab === "keys" ? "secondary" : "primary"}
      disabled={busy}
      aria-busy={busy}
      onClick={() => {
        if (tab && tab !== "findings") router.push(path, { scroll: false });
        void run();
      }}
    >
      <IconPlay className="h-4 w-4" />
      {busy ? "Reading rails…" : shown ? "Run again" : "Run reconciliation"}
    </Button>
  );
}
