"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { openPalette } from "./command-palette";
import { openSupport } from "./support-panel";
import { IconBell, IconHelp } from "./ui/icons";

/**
 * The wide-screen top bar: findings and the account. The bell is not a
 * notification feed: it is the way to Findings, with the signal dot while
 * any order needs a look. Phones have the sidebar's own top bar instead.
 */
export function TopBar({ account, openFindings, docked = false }: { account: string; openFindings: number; docked?: boolean }) {
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
    // Above the page, so the account menu opens over everything below it.
    <div className={docked ? "relative z-40 flex justify-end pr-5" : "relative z-40 hidden justify-end pt-5 pb-5 min-[900px]:flex 2xl:hidden"}>
      <div ref={box} className="relative flex h-[68px] items-center rounded-full border border-white/90 bg-white/85 pr-4 pl-4 shadow-float backdrop-blur-xl">
        {/* Below 2xl Support is not docked: it opens from here, never from a
            button floating over the page. */}
        {!docked && (
          <>
            <button
              type="button"
              onClick={openSupport}
              className="inline-flex min-h-12 items-center gap-2.5 rounded-full px-4 text-[16px] font-semibold text-accent hover:bg-accent-soft"
            >
              <IconHelp />
              Ask Okwan
            </button>
            <span aria-hidden className="mx-3 h-10 w-px bg-line" />
          </>
        )}
        <Link
          href="/findings"
          aria-label={openFindings > 0 ? `Findings: ${openFindings} need a look` : "Findings"}
          className="relative inline-flex h-12 w-12 items-center justify-center rounded-full text-ink hover:bg-accent-soft"
        >
          <IconBell />
          {openFindings > 0 && <span aria-hidden className="absolute top-[5px] right-[7px] h-3 w-3 rounded-full bg-signal" />}
        </Link>
        <span aria-hidden className="mx-4 h-10 w-px bg-line" />
        <button
          ref={button}
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="inline-flex min-h-12 max-w-[280px] items-center gap-6 rounded-full px-3 text-[18px] font-semibold hover:bg-accent-soft 2xl:text-[21px]"
        >
          <span className="truncate" title={account}>{account}</span>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden className={open ? "rotate-180" : ""}>
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
