"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, type ReactNode, useCallback, useContext, useState } from "react";
import { digestOf, type Finding } from "@/lib/finding";
import { report } from "@/lib/tab-results";
import { Button } from "./ui/button";
import { IconPlay } from "./ui/icons";

type RunState = {
  tenantId: string;
  busy: boolean;
  finding: Finding | null;
  ranAt: Date | null;
  error: { status: number; detail: string } | null;
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
export function MerchantRunProvider({ tenantId, fold = "rails", children }: {
  tenantId: string;
  fold?: string;
  children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [finding, setFinding] = useState<Finding | null>(null);
  const [ranAt, setRanAt] = useState<Date | null>(null);
  const [error, setError] = useState<RunState["error"]>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    const res = await fetch(
      `/api/merchants/${encodeURIComponent(tenantId)}/across/${encodeURIComponent(fold)}`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    ).catch(() => null);
    const data = res ? await res.json().catch(() => ({})) : {};
    setBusy(false);
    if (!res || !res.ok) {
      const detail = data.detail ?? "the dashboard couldn't reach the API";
      setError({ status: res?.status ?? 0, detail });
      report([{ id: tenantId, ok: false, open: 0, twice: 0, twiceMinor: 0, currency: null, detail }]);
      return;
    }
    setFinding(data as Finding);
    setRanAt(new Date());
    report([digestOf(tenantId, data as Finding)]);
  }, [tenantId, fold]);

  return <Ctx.Provider value={{ tenantId, busy, finding, ranAt, error, run }}>{children}</Ctx.Provider>;
}

/** The header's primary action. Runs, and shows the Findings tab. */
export function RunButton() {
  const { busy, finding, run } = useMerchantRun();
  const router = useRouter();
  const path = usePathname();
  const tab = useSearchParams().get("tab");
  return (
    <Button
      variant="primary"
      disabled={busy}
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
