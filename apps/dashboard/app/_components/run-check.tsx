"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "./ui/button";
import { IconPlay } from "./ui/icons";

/**
 * Run one file-based check for a merchant. A run is fresh and counts
 * toward the plan; the API stores it, and on success the page re-reads
 * the stored run, so what shows is what was kept. A refusal (the plan is
 * spent, the session ended) shows its reason beside the button.
 */
export function RunCheck({ tenantId, name, again }: { tenantId: string; name: string; again: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setError(null);
    let res: Response;
    try {
      res = await fetch(`/api/merchants/${encodeURIComponent(tenantId)}/checks/${encodeURIComponent(name)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
    } catch {
      setBusy(false);
      return setError("The check could not be started. Try again.");
    }
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setError(typeof data.detail === "string" ? data.detail : "The check did not run.");
    router.refresh();
  }

  return (
    <span className="inline-flex flex-wrap items-center justify-end gap-2">
      <Button variant={again ? "secondary" : "primary"} onClick={go} disabled={busy} aria-busy={busy}>
        <IconPlay className="h-4 w-4" />
        {busy ? "Checking…" : again ? "Check again" : "Check these files"}
      </Button>
      <span role="status" className="sr-only">{busy ? "Checking the files." : ""}</span>
      {error && <span role="alert" className="basis-full text-right text-xs text-danger">{error}</span>}
    </span>
  );
}
