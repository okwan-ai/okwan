"use client";

import { useState } from "react";

/**
 * Test mode on or off (app/api/mode), then Overview: a page of one side (a
 * merchant, an open item) is not there on the other. What it said went
 * wrong, or null once the page is on its way.
 */
export async function switchMode(on: boolean): Promise<string | null> {
  const res = await fetch("/api/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: on ? "test" : "live" }),
  }).catch(() => null);
  if (!res?.ok) {
    const data = await res?.json().catch(() => ({}));
    return data?.detail ?? "Test mode couldn't be switched. Try again.";
  }
  window.location.href = "/overview";
  return null;
}

/** The track and thumb a switch draws: yellow when on. Decoration only. */
export function SwitchTrack({ on }: { on: boolean }) {
  return (
    <span aria-hidden className={`relative inline-flex h-6 w-10 shrink-0 rounded-full ${on ? "bg-signal" : "bg-[#cbcdf2]"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow ${on ? "left-[18px]" : "left-0.5"}`} />
    </span>
  );
}

/** The switch in the phone menu; wide screens have it in the account menu. */
export function TestModeSwitch({ on }: { on: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          const said = await switchMode(!on);
          if (said) {
            setError(said);
            setBusy(false);
          }
        }}
        className="flex min-h-11 w-full items-center justify-between gap-3 rounded-xl px-3 text-left text-sm font-medium hover:bg-accent-soft disabled:opacity-60"
      >
        Test mode
        <SwitchTrack on={on} />
      </button>
      {error && <p role="alert" className="px-3 pt-1 text-xs text-danger">{error}</p>}
    </div>
  );
}

/** Across the top of every page while in Test mode, with the way out. */
export function TestModeBanner() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <section aria-label="Test mode" className="border-b border-signal-deep bg-signal text-ink">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 text-sm sm:px-8 xl:px-10">
        <p className="min-w-0 flex-1">
          <strong className="font-bold">Test mode.</strong>{" "}
          You&apos;re in your sandbox: the sample week, test keys that start <code className="font-mono">okw_test_</code>,
          and nothing that reaches a live merchant or a rail.
        </p>
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            const said = await switchMode(false);
            if (said) {
              setError(said);
              setBusy(false);
            }
          }}
          className="inline-flex min-h-11 items-center rounded-full bg-ink px-4 font-semibold text-canvas hover:bg-ink-soft disabled:opacity-60"
        >
          {busy ? "Leaving…" : "Leave Test mode"}
        </button>
        {error && <p role="alert" className="w-full text-xs font-medium">{error}</p>}
      </div>
    </section>
  );
}
