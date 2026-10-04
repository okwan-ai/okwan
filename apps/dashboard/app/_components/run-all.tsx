"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "./ui/button";
import { IconPlay } from "./ui/icons";

/**
 * Runs the fold for every ready merchant, in parallel, then refreshes the
 * page so it reads the stored results. One metered request per merchant;
 * the button says how many before it is pressed. Nothing runs on a page
 * load any more, so this is the only way a cross-merchant page spends.
 */
export function RunAll({ merchants, fold = "rails", variant = "primary" }: {
  merchants: { id: string; name: string }[];
  fold?: string;
  variant?: "primary" | "secondary";
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const n = merchants.length;
  if (n === 0) return null;

  async function run() {
    setBusy(true);
    setSaid(`Running ${n} merchant${n === 1 ? "" : "s"}…`);
    const results = await Promise.all(merchants.map(async (m) => {
      const res = await fetch(`/api/merchants/${encodeURIComponent(m.id)}/across/${encodeURIComponent(fold)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      }).catch(() => null);
      return res?.ok ?? false;
    }));
    const failed = results.filter((ok) => !ok).length;
    setSaid(failed ? `${n - failed} of ${n} runs finished; ${failed} couldn't run. Results are stored.` : `${n} run${n === 1 ? "" : "s"} finished and stored.`);
    setBusy(false);
    router.refresh();
  }

  return (
    <>
      <Button variant={variant} disabled={busy} aria-busy={busy} onClick={() => void run()}>
        <IconPlay className="h-4 w-4" />
        {busy ? "Running…" : `Run all (${n})`}
      </Button>
      <p role="status" className="sr-only">{said}</p>
    </>
  );
}
