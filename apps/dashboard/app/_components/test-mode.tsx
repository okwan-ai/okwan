"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

type Mode = "test" | "live";

/** The mode this page was shown in, and whether it is switching now. */
let pageMode: Mode = "live";
let switching = false;
let stamped = false;
const CHANNEL = "okwan.mode";

/** Every request this page makes to the dashboard's own /api/ says the mode
 *  the page was shown in (lib/api.ts, PAGE_MODE_HEADER), so one left open in
 *  one mode never acts, or reads, in the other. Installed once. */
function stampRequests() {
  if (stamped) return;
  stamped = true;
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const href = input instanceof Request ? input.url : input instanceof URL ? input.href : String(input);
    const url = new URL(href, window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/")) return original(input, init);
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    headers.set("Okwan-Page-Mode", pageMode);
    return original(input, { ...init, headers });
  };
}

/** Whether this browser has changed mode since the page was shown, as
 *  another tab's switch does: the page is then shown again. */
async function stillSo() {
  if (switching) return;
  const res = await fetch("/api/mode", { cache: "no-store" }).catch(() => null);
  const now = res?.ok ? ((await res.json().catch(() => ({}))) as { mode?: Mode }).mode : undefined;
  if (now && now !== pageMode && !switching) window.location.reload();
}

/**
 * Keeps a page true to the mode it was shown in: its requests say that mode
 * (stampRequests), and when another tab switches, or the page comes back
 * into view, or moves to another page after a switch, it is shown again in
 * the mode the browser is in now. Renders nothing.
 */
export function ModeStamp({ mode }: { mode: Mode }) {
  if (typeof window !== "undefined") {
    pageMode = mode;
    stampRequests();
  }
  const path = usePathname();

  useEffect(() => {
    let channel: BroadcastChannel | null = null;
    try {
      channel = new BroadcastChannel(CHANNEL);
      channel.onmessage = (e) => {
        if (!switching && e.data !== pageMode) window.location.reload();
      };
    } catch {
      // No BroadcastChannel: the checks on view and on navigation still run.
    }
    const onView = () => {
      if (document.visibilityState === "visible") void stillSo();
    };
    document.addEventListener("visibilitychange", onView);
    return () => {
      channel?.close();
      document.removeEventListener("visibilitychange", onView);
    };
  }, []);

  // A page reached by a link after another tab switched is drawn in the new
  // mode under this page's old banner: show it again, whole.
  useEffect(() => {
    void stillSo();
  }, [path]);

  return null;
}

/**
 * Test mode on or off (app/api/mode), then Overview: a page of one side (a
 * merchant, an open item) is not there on the other. What it said went
 * wrong, or null once the page is on its way.
 */
export async function switchMode(on: boolean): Promise<string | null> {
  switching = true;
  const res = await fetch("/api/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: on ? "test" : "live" }),
  }).catch(() => null);
  if (!res?.ok) {
    switching = false;
    const data = await res?.json().catch(() => ({}));
    return data?.detail ?? "Test mode couldn't be switched. Try again.";
  }
  // Every other open tab is shown again in the new mode.
  try {
    const channel = new BroadcastChannel(CHANNEL);
    channel.postMessage(on ? "test" : "live");
    channel.close();
  } catch {
    // They catch up when next in view, or on their next page.
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
