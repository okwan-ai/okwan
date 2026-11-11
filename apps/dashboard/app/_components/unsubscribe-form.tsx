"use client";

import Link from "next/link";
import { useState } from "react";
import { buttonClass } from "./ui/button";

/** One button, pressed by a person: opening the page turns nothing off,
 *  so a mail scanner that follows the link changes nothing. */
export function UnsubscribeForm({ token }: { token: string }) {
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [error, setError] = useState<string | null>(null);

  async function stop() {
    setState("busy");
    setError(null);
    const res = await fetch("/api/unsubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (res.ok) return setState("done");
    const data = await res.json().catch(() => ({}));
    setState("idle");
    setError(data.detail ?? "something went wrong");
  }

  if (state === "done") {
    return (
      <div role="status" className="card space-y-3 p-6 sm:p-8">
        <p className="text-lg font-semibold">These emails are off.</p>
        <p className="text-sm text-ink-soft">
          Your merchants are still checked once a day, and the results are in the dashboard. You can turn the emails
          back on, or stop the daily checks, in <Link href="/settings#daily-checks" className="underline underline-offset-4">Settings</Link>.
        </p>
      </div>
    );
  }
  return (
    <div className="card space-y-5 p-6 sm:p-8">
      <p className="text-sm text-ink-soft">
        Okwan emails this workspace when a daily check finds something new. Turning the emails off doesn&apos;t stop the
        checks.
      </p>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      <button type="button" onClick={stop} disabled={state === "busy"} className={buttonClass("primary")}>
        {state === "busy" ? "…" : "Turn these emails off"}
      </button>
    </div>
  );
}
