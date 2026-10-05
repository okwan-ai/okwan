"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { type RunDigest, type SeenFinding, verdictOf } from "@/lib/finding";
import { CommandPalette, openPalette } from "./command-palette";
import { useDialog } from "./ui/dialog";
import type { Usage } from "@/lib/usage-shape";
import { IconAgent, IconAlert, IconClose, IconGear, IconHome, IconMenu, IconPlug, IconSearch, IconSignOut, IconStore, IconTerminal } from "./ui/icons";
import { PlanMeter } from "./usage/plan-meter";

export type MerchantLink = { id: string; name: string };

/** `tenant` is the workspace name. A self-serve workspace is named by its
 * verified address, so for most accounts this line is the email; the API
 * exposes no other. */
export function Sidebar({ tenant, merchants, connectors, plan, apiBase, verdicts, findings }: {
  tenant: string;
  merchants: MerchantLink[];
  /** Every connector, for the palette's Integrations group. */
  connectors: { name: string; label: string }[];
  plan: Usage["plan"] | null;
  apiBase: string;
  /** Each merchant's newest stored run, read by the layout; never a run. */
  verdicts: Record<string, RunDigest>;
  /** Open findings across merchants, for the palette's order search. */
  findings: SeenFinding[];
}) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const menu = useDialog(open, () => setOpen(false));
  const results = verdicts;
  const all = Object.values(results);
  const ok = all.filter((r) => r.ok);
  const openFindings = ok.reduce((n, r) => n + r.open, 0);
  const failed = all.length - ok.length;
  // The merchant this page is about, for the current-merchant row and the
  // phone top bar.
  const match = /^\/merchants\/([^/]+)/.exec(path);
  const currentId = match ? decodeURIComponent(match[1]) : null;
  const current = currentId ? merchants.find((m) => m.id === currentId) ?? null : null;
  const menuCount = ok.length ? (openFindings > 0 ? String(openFindings) : null) : failed ? "!" : null;
  const menuLabel = open ? "Close menu"
    : menuCount === "!" ? "Open menu, a check couldn't run"
      : menuCount ? `Open menu, ${menuCount} open finding${openFindings === 1 ? "" : "s"}`
        : "Open menu";

  // A link followed on the narrow layout closes the menu behind it.
  useEffect(() => setOpen(false), [path]);
  // So does the layout turning wide (rotation, zoom): the menu's header is
  // hidden then, and a modal nobody can see must not keep the page locked.
  useEffect(() => {
    const wide = window.matchMedia("(min-width: 900px)");
    const onChange = () => wide.matches && setOpen(false);
    wide.addEventListener("change", onChange);
    return () => wide.removeEventListener("change", onChange);
  }, []);

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-0.5 text-sm">
      <Item href="/overview" path={path} icon={<IconHome />}>Overview</Item>
      <Item
        href="/findings"
        path={path}
        icon={<IconAlert />}
        count={
          ok.length ? <Count n={openFindings} label="open findings" strong={openFindings > 0} />
            : failed ? <Count text="!" label="checks couldn't run" strong />
              : null
        }
      >
        Findings
      </Item>
      <Item href="/merchants" path={path} icon={<IconStore />} count={<Count n={merchants.length} label="merchants" />}>
        Merchants
      </Item>
      {current && (
        <Link
          href={`/merchants/${encodeURIComponent(current.id)}`}
          aria-current="page"
          className="ml-7 flex min-h-10 items-center gap-2.5 rounded-lg bg-ink/[0.06] px-3 text-sm font-medium text-ink"
        >
          <Glyph d={results[current.id]} />
          <span className="min-w-0 flex-1 truncate">{current.name}</span>
        </Link>
      )}
      <p className="mt-6 mb-1 px-3 text-xs font-medium text-ink-soft">Connect</p>
      <Item href="/integrations" path={path} icon={<IconPlug />}>Integrations</Item>
      <Item href="/agents" path={path} icon={<IconAgent />}>Agents</Item>
      <a
        href={`${apiBase}/docs`}
        target="_blank"
        rel="noopener"
        className="flex min-h-11 items-center gap-3 rounded-lg px-3 text-ink-soft hover:bg-ink/[0.04] hover:text-ink"
      >
        <span className="text-ink-soft"><IconTerminal /></span>
        <span className="flex-1">API reference</span>
        <span aria-hidden className="text-xs">↗</span>
        <span className="sr-only">(opens in a new tab)</span>
      </a>
    </nav>
  );

  const footer = (
    <div className="space-y-3 border-t border-line pt-4 text-xs">
      {plan ? (
        <Link href="/settings?tab=plan" className="block rounded-lg hover:bg-ink/[0.04]">
          <PlanMeter plan={plan} compact />
        </Link>
      ) : (
        <div>
          <p className="font-medium text-ink">Plan &amp; usage</p>
          <p className="text-ink-soft">Usage isn&apos;t available right now.</p>
        </div>
      )}
      <Link
        href="/settings"
        aria-current={path === "/settings" ? "page" : undefined}
        className={`flex min-h-11 items-center gap-3 rounded-lg px-2 text-sm hover:bg-ink/[0.04] ${path === "/settings" ? "bg-ink/[0.06] font-medium" : ""}`}
      >
        <span className="text-ink-soft"><IconGear /></span>
        <span className="min-w-0">
          <span className="block">Settings</span>
          <span className="block truncate text-xs font-normal text-ink-soft" title={tenant}>{tenant}</span>
        </span>
      </Link>
      <SignOutButton />
    </div>
  );

  return (
    <>
      <CommandPalette merchants={merchants} connectors={connectors} verdicts={verdicts} findings={findings} />

      {/* Narrow: a top bar with search and the menu button. */}
      <header className="sticky top-0 z-40 border-b border-line bg-canvas/95 backdrop-blur min-[900px]:hidden">
        <div className="flex items-center justify-between gap-1 px-2">
          <Logo />
          {current && <span className="min-w-0 flex-1 truncate px-1 text-sm font-medium">{current.name}</span>}
          <div className="flex shrink-0 items-center">
            <button
              type="button"
              aria-label="Search"
              onClick={openPalette}
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg hover:bg-ink/5"
            >
              <IconSearch />
            </button>
            <button
              ref={toggle}
              type="button"
              aria-label={menuLabel}
              aria-expanded={open}
              aria-controls="mobile-nav"
              onClick={() => setOpen((v) => !v)}
              className="relative inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg hover:bg-ink/5"
            >
              {open ? <IconClose /> : <IconMenu />}
              {!open && menuCount && (
                <span aria-hidden className="absolute top-1.5 right-1.5 min-w-4 rounded-full bg-ink px-1 text-center text-[11px] leading-4 font-medium text-canvas">
                  {menuCount}
                </span>
              )}
            </button>
          </div>
        </div>
        {/* Always rendered so aria-controls resolves; hidden when closed. */}
        <div
          id="mobile-nav"
          ref={menu}
          role="dialog"
          aria-modal="true"
          aria-label="Menu"
          hidden={!open}
          className="absolute inset-x-0 top-full flex h-[calc(100dvh-100%)] flex-col overflow-y-auto overscroll-contain border-t border-line bg-canvas px-3 py-4"
        >
          {nav}
          <div className="mt-auto pt-6">{footer}</div>
        </div>
      </header>

      {/* Wide: the fixed sidebar. */}
      <aside className="sticky top-0 hidden h-screen w-[236px] shrink-0 flex-col border-r border-line bg-surface px-3 py-4 min-[900px]:flex">
        <div className="mb-3"><Logo /></div>
        <button
          type="button"
          onClick={openPalette}
          className="mb-4 flex min-h-11 items-center gap-2 rounded-lg border border-line bg-canvas px-3 text-left text-sm text-ink-soft hover:border-ink/40 hover:text-ink"
        >
          <IconSearch className="h-4 w-4" />
          <span className="flex-1">Search</span>
          <kbd className="rounded border border-line bg-surface px-1.5 font-mono text-[11px]">⌘K</kbd>
        </button>
        {nav}
        <div className="flex-1" />
        <div className="pt-3">{footer}</div>
      </aside>
    </>
  );
}

function Glyph({ d }: { d: RunDigest | undefined }) {
  const v = verdictOf(d);
  const cls = { danger: "text-danger", ink: "text-ink", soft: "text-ink-soft", ok: "text-ok" }[v.tone];
  return (
    <span className={`w-5 shrink-0 text-center font-mono text-[11px] font-medium ${cls}`}>
      <span aria-hidden>{v.mark}</span>
      <span className="sr-only">{v.label}: </span>
    </span>
  );
}

function Logo() {
  return (
    <Link href="/overview" prefetch={false} className="flex min-h-11 items-center gap-2 rounded-lg px-2">
      <span className="inline-block h-6 w-6 rounded-md bg-volt ring-1 ring-ink" aria-hidden />
      <span className="font-display text-xl font-medium">Okwan</span>
    </Link>
  );
}

function Item({ href, path, icon, count, children }: {
  href: string;
  path: string;
  icon: ReactNode;
  count?: ReactNode;
  children: ReactNode;
}) {
  const active = path === href || path.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      // Looks active on a sub-page (/integrations/paypal) but names the page
      // only on its own URL; the sub-page's breadcrumb carries aria-current.
      aria-current={path === href ? "page" : undefined}
      className={`flex min-h-11 items-center gap-3 rounded-lg px-3 ${
        active ? "bg-ink/[0.06] font-medium text-ink" : "text-ink-soft hover:bg-ink/[0.04] hover:text-ink"
      }`}
    >
      <span className={active ? "text-ink" : "text-ink-soft"}>{icon}</span>
      <span className="flex-1">{children}</span>
      {count}
    </Link>
  );
}

function Count({ n, text, label, strong = false }: { n?: number; text?: string; label: string; strong?: boolean }) {
  return (
    <span
      className={`min-w-6 rounded-full px-1.5 text-center text-xs tabular-nums ${
        strong ? "bg-ink font-medium text-canvas" : "bg-ink/[0.06] text-ink-soft"
      }`}
    >
      {text ?? n}
      <span className="sr-only"> {label}</span>
    </span>
  );
}

function SignOutButton() {
  return (
    <button
      type="button"
      className="flex min-h-11 w-full items-center gap-2 rounded-lg px-2 text-sm text-ink-soft hover:bg-ink/[0.04] hover:text-ink"
      onClick={async () => {
        await fetch("/api/signout", { method: "POST" });
        window.location.href = "/signup?mode=signin";
      }}
    >
      <IconSignOut />
      Sign out
    </button>
  );
}
