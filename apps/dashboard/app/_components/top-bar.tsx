"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { openPalette } from "./command-palette";

/**
 * The wide-screen top bar: findings and the account. The bell is not a
 * notification feed: it is the way to Findings, with the signal dot while
 * any order needs a look. Phones have the sidebar's own top bar instead.
 */
export function TopBar({ account, openFindings }: { account: string; openFindings: number }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); button.current?.focus(); }
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);

  return (
    <div className="hidden justify-end pt-5 pb-2 min-[900px]:flex">
      <div ref={box} className="relative flex items-center gap-1 rounded-full border border-white/80 bg-white/80 py-1.5 pr-2 pl-2 shadow-float backdrop-blur-xl">
        <Link
          href="/findings"
          aria-label={openFindings > 0 ? `Findings: ${openFindings} need a look` : "Findings"}
          className="relative inline-flex h-11 w-11 items-center justify-center rounded-full text-ink hover:bg-accent-soft"
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M6 9a6 6 0 1 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15 6 9" />
            <path d="M10 20a2.2 2.2 0 0 0 4 0" />
          </svg>
          {openFindings > 0 && <span aria-hidden className="absolute top-2 right-2 h-2.5 w-2.5 rounded-full border-2 border-white bg-signal" />}
        </Link>
        <span aria-hidden className="mx-1 h-7 w-px bg-line" />
        <button
          ref={button}
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="inline-flex min-h-11 max-w-[260px] items-center gap-2 rounded-full px-3 text-[15px] font-semibold hover:bg-accent-soft"
        >
          <span className="truncate" title={account}>{account}</span>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden className={open ? "rotate-180" : ""}>
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
        {open && (
          <div role="menu" aria-label="Account" className="absolute top-[calc(100%+8px)] right-0 z-50 w-64 overflow-hidden rounded-2xl border border-line bg-white p-1.5 shadow-float">
            <p className="truncate px-3 pt-2 pb-2 text-xs text-ink-muted" title={account}>{account}</p>
            <MenuLink href="/settings" onPick={() => setOpen(false)}>Settings</MenuLink>
            <MenuLink href="/settings?tab=plan" onPick={() => setOpen(false)}>Plan &amp; billing</MenuLink>
            <button
              type="button"
              role="menuitem"
              onClick={() => { setOpen(false); openPalette(); }}
              className="flex min-h-11 w-full items-center justify-between rounded-xl px-3 text-left text-sm hover:bg-accent-soft"
            >
              Search <kbd className="rounded-md border border-line px-1.5 font-mono text-[11px] text-ink-soft">⌘K</kbd>
            </button>
            <div className="my-1 h-px bg-line" />
            <button
              type="button"
              role="menuitem"
              onClick={async () => {
                await fetch("/api/signout", { method: "POST" });
                window.location.href = "/signup?mode=signin";
              }}
              className="flex min-h-11 w-full items-center rounded-xl px-3 text-left text-sm text-danger hover:bg-danger-soft"
            >
              Sign out
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function MenuLink({ href, onPick, children }: { href: string; onPick: () => void; children: React.ReactNode }) {
  return (
    <Link href={href} role="menuitem" onClick={onPick} className="flex min-h-11 items-center rounded-xl px-3 text-sm hover:bg-accent-soft">
      {children}
    </Link>
  );
}
