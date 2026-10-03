"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, type ReactNode, useCallback, useContext, useState } from "react";
import { digestOf, failedDigest, type Finding, railLabel } from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import { report } from "@/lib/tab-results";
import { Button, buttonClass } from "./ui/button";
import { IconPlay } from "./ui/icons";

type RunState = {
  tenantId: string;
  tenantName: string;
  busy: boolean;
  finding: Finding | null;
  ranAt: Date | null;
  /** The result came from an earlier check this server still holds,
   *  not from a run on this page. */
  reused: boolean;
  error: { status: number; detail: string } | null;
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
 * tabs (each tab is a URL, and the page beneath remounts). Each run is a
 * fresh, metered read through the session route; nothing is kept.
 */
export function MerchantRunProvider({ tenantId, tenantName, fold = "rails", initial = null, missing = [], children }: {
  tenantId: string;
  tenantName: string;
  fold?: string;
  missing?: string[];
  /** A result Overview or Findings already paid for, still fresh on the
   *  server; shown without running again. */
  initial?: { finding: Finding; at: number } | null;
  children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [finding, setFinding] = useState<Finding | null>(initial?.finding ?? null);
  const [ranAt, setRanAt] = useState<Date | null>(initial ? new Date(initial.at) : null);
  const [reused, setReused] = useState(initial !== null);
  const [error, setError] = useState<RunState["error"]>(null);
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
      setError({ status: res?.status ?? 0, detail });
      setSaid(`The run didn't finish: ${detail}`);
      report([failedDigest(tenantId, detail, Date.now())]);
      return;
    }
    const at = Date.now();
    setFinding(data as Finding);
    setRanAt(new Date(at));
    setReused(false);
    const f = data as Finding;
    const twice = f.summary.collected_twice;
    setSaid(`Run finished: ${f.summary.orders} orders checked, ${twice} collected twice${
      twice && f.twice_currency ? ` (${formatMinor(f.summary.collected_twice_minor, f.twice_currency)})` : ""}.`);
    report([digestOf(tenantId, f, at)]);
  }, [tenantId, fold]);

  return (
    <Ctx.Provider value={{ tenantId, tenantName, busy, finding, ranAt, reused, error, missing, run }}>
      <p role="status" className="sr-only">{said}</p>
      {children}
    </Ctx.Provider>
  );
}

/** The header's primary action. Runs, and shows the Findings tab. */
export function RunButton() {
  const { busy, finding, missing, run } = useMerchantRun();
  const router = useRouter();
  const path = usePathname();
  const tab = useSearchParams().get("tab");
  // A run with a rail missing fails on the API (and would read as a broken
  // product); lead to what's missing instead.
  if (missing.length && !finding) {
    return (
      <Link href={`${path}?tab=connections&connect=${missing[0]}`} scroll={false} className={buttonClass(tab === "connections" ? "secondary" : "primary")}>
        Connect {missing.map(railLabel).join(" + ")}
      </Link>
    );
  }
  return (
    <Button
      // One volt element per view: once a result exists the band carries it,
      // and a re-run (metered) is a secondary action.
      variant={finding ? "secondary" : "primary"}
      disabled={busy}
      aria-busy={busy}
      onClick={() => {
        if (tab && tab !== "findings") router.push(path, { scroll: false });
        void run();
      }}
    >
      <IconPlay className="h-4 w-4" />
      {busy ? "Reading rails…" : finding ? "Run again" : "Run reconciliation"}
    </Button>
  );
}
