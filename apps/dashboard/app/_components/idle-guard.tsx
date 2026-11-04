"use client";

import { useEffect } from "react";

/** Mirrors the API's limits (okwan_vault/accounts.py). The server is the
 *  authority: an idle session stops working there whatever this page does. */
const IDLE_MS = 30 * 60 * 1000;
/** While someone is using the page, tell the API at most this often, so an
 *  active person reading or typing is not signed out between requests. */
const PING_MS = 5 * 60 * 1000;
/** Activity is shared across tabs, so working in one keeps the others in. */
const SHARED = "okwan.activity";

function stamp(): number {
  try {
    return Number(localStorage.getItem(SHARED)) || 0;
  } catch {
    return 0;
  }
}

/**
 * Signs the browser out after 30 minutes without a key press, click, touch
 * or scroll in any Okwan tab, and says why on the sign-in page.
 */
export function IdleGuard() {
  useEffect(() => {
    let last = Date.now();
    let pinged = Date.now();
    let leaving = false;

    const active = () => {
      const now = Date.now();
      if (now - last < 15_000) return;
      last = now;
      try {
        localStorage.setItem(SHARED, String(now));
      } catch {
        // Storage blocked: this tab keeps its own clock.
      }
      if (now - pinged > PING_MS) {
        pinged = now;
        fetch("/api/session", { cache: "no-store" }).then((r) => {
          if (r.status === 401) signOut("ended");
        }).catch(() => {});
      }
    };

    const signOut = (why: "idle" | "ended") => {
      if (leaving) return;
      leaving = true;
      fetch("/api/signout", { method: "POST" }).finally(() => {
        window.location.href = `/signup?mode=signin&${why}=1`;
      });
    };

    const check = () => {
      const seen = Math.max(last, stamp());
      if (Date.now() - seen >= IDLE_MS) signOut("idle");
    };

    const events = ["pointerdown", "keydown", "scroll", "touchstart"] as const;
    events.forEach((e) => window.addEventListener(e, active, { passive: true }));
    // A laptop that slept: check the moment the page is seen again.
    const visible = () => document.visibilityState === "visible" && check();
    document.addEventListener("visibilitychange", visible);
    const timer = window.setInterval(check, 30_000);
    try {
      localStorage.setItem(SHARED, String(last));
    } catch {
      // As above.
    }
    return () => {
      events.forEach((e) => window.removeEventListener(e, active));
      document.removeEventListener("visibilitychange", visible);
      window.clearInterval(timer);
    };
  }, []);
  return null;
}
