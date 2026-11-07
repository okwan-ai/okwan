"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "./ui/button";

/**
 * Delete one thing by URL, after a second click to confirm. On success the
 * page re-reads from the API; on failure the reason shows beside it.
 * `what` names the thing for the confirm and for screen readers.
 */
export function DeleteButton({ url, what, confirmLabel = "Delete" }: { url: string; what: string; confirmLabel?: string }) {
  const router = useRouter();
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    if (!armed) return setArmed(true);
    setBusy(true);
    setError(null);
    const res = await fetch(url, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    setArmed(false);
    if (!res.ok) return setError(data.detail ?? "it could not be deleted");
    router.refresh();
  }

  return (
    <span className="inline-flex flex-wrap items-center justify-end gap-2">
      {armed && !busy && (
        <Button variant="ghost" onClick={() => setArmed(false)} aria-label={`Keep ${what}`}>Keep</Button>
      )}
      <Button variant={armed ? "secondary" : "ghost"} onClick={go} disabled={busy} aria-label={armed ? `Confirm: delete ${what}` : `Delete ${what}`}>
        {busy ? "Deleting…" : armed ? `${confirmLabel}?` : "Delete"}
      </Button>
      {error && <span role="alert" className="basis-full text-right text-xs text-danger">{error}</span>}
    </span>
  );
}
