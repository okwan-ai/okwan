"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "./ui/button";
import { IconPlay } from "./ui/icons";

/**
 * Runs the check for every ready merchant, in parallel, then refreshes the
 * page so it reads the saved results. One metered request per merchant;
 * the button and its note say how many before it is pressed. Nothing runs
 * on a page load, so this is the only way a cross-merchant page spends.
 */
export function RunAll({ merchants, fold = "rails", variant = "primary" }: {
  merchants: { id: string; name: string }[];
  fold?: string;
  variant?: "primary" | "secondary";
}) {
  const router = useRouter();
  const note = useId();
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const n = merchants.length;
  if (n === 0) return null;

  async function run() {
    setBusy(true);
    setSaid(`Checking ${n} merchant${n === 1 ? "" : "s"}…`);
    const results = await Promise.all(merchants.map(async (m) => {
      const res = await fetch(`/api/merchants/${encodeURIComponent(m.id)}/across/${encodeURIComponent(fold)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      }).catch(() => null);
      return res?.ok ?? false;
    }));
    const failed = results.filter((ok) => !ok).length;
    setSaid(failed ? `${n - failed} of ${n} checks finished; ${failed} couldn't run. Results are saved.` : `${n} check${n === 1 ? "" : "s"} finished and saved.`);
    setBusy(false);
    router.refresh();
  }

  return (
    <div className="flex flex-col items-start gap-1 max-sm:w-full sm:items-end">
      <Button variant={variant} className="max-sm:w-full" disabled={busy} aria-busy={busy} aria-describedby={note} onClick={() => void run()}>
        <IconPlay className="h-4 w-4" />
        {busy ? "Running…" : `Run all checks (${n})`}
      </Button>
      <p id={note} className="text-xs text-ink-soft">{n} request{n === 1 ? "" : "s"} · results saved</p>
      <p role="status" className="sr-only">{said}</p>
    </div>
  );
}
