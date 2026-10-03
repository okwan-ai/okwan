"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { type RunDigest, verdictOf } from "@/lib/finding";
import { useTabResults } from "@/lib/tab-results";
import { CommandPalette, openPalette } from "./command-palette";
import { useDialog } from "./ui/dialog";
import type { Usage } from "@/lib/usage-shape";
import { IconAgent, IconAlert, IconClose, IconGear, IconHome, IconKey, IconMenu, IconSearch, IconSignOut, IconStore } from "./ui/icons";
import { PlanMeter } from "./usage/plan-meter";

/** Shown only when the usage read fails: the plan every tenant is held to,
 * with no meter drawn from numbers the page doesn't have. */
const PLAN_NAME = "Free plan";
const PLAN_QUOTA = "5,000 requests/month";

/** Shown in the sidebar before the list asks to be filtered. */
const MERCHANT_LIST = 8;

export type MerchantLink = { id: string; name: string };

/** `tenant` is the workspace name. A self-serve workspace is named by its
 * verified address, so for most accounts this line is the email; the API
 * exposes no other. */
export function Sidebar({ tenant, merchants, plan }: { tenant: string; merchants: MerchantLink[]; plan: Usage["plan"] | null }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const menu = useDialog(open, () => setOpen(false));
  const results = useTabResults();
  const all = Object.values(results);
  const ok = all.filter((r) => r.ok);
  const openFindings = ok.reduce((n, r) => n + r.open, 0);
  const failed = all.length - ok.length;

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
    <>
      <nav aria-label="Main" className="flex flex-col gap-0.5 text-sm">
        <Item href="/overview" path={path} icon={<IconHome />}>Overview</Item>
        <Item href="/merchants" path={path} exact icon={<IconStore />} count={<Count n={merchants.length} label="merchants" />}>
          Merchants
        </Item>
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
        <p className="mt-6 mb-1 px-3 text-xs font-medium text-ink-soft">Developers</p>
        <Item href="/key" path={path} icon={<IconKey />}>API keys</Item>
        <Item href="/mcp" path={path} icon={<IconAgent />}>MCP for agents</Item>
        <p className="mt-6 mb-1 px-3 text-xs font-medium text-ink-soft">Manage</p>
        <Item href="/settings" path={path} icon={<IconGear />}>Settings</Item>
      </nav>
      {merchants.length > 0 && <MerchantSwitcher merchants={merchants} results={results} path={path} />}
    </>
  );

  const footer = (
    <div className="space-y-3 border-t border-line pt-4 text-xs">
      {plan ? (
        <Link href="/settings?tab=plan" className="block rounded-lg hover:bg-ink/[0.04]">
          <PlanMeter plan={plan} compact />
        </Link>
      ) : (
        <div>
          <p className="font-medium text-ink">{PLAN_NAME}</p>
          <p className="text-ink-soft">{PLAN_QUOTA}</p>
        </div>
      )}
      <div className="min-w-0">
        <p className="text-ink-soft">Workspace</p>
        <p className="truncate font-medium text-ink" title={tenant}>{tenant}</p>
      </div>
      <SignOutButton />
    </div>
  );

  return (
    <>
      <CommandPalette merchants={merchants} />

      {/* Narrow: a top bar with search and the menu button. */}
      <header className="sticky top-0 z-40 border-b border-line bg-canvas/95 backdrop-blur min-[900px]:hidden">
        <div className="flex items-center justify-between px-2">
          <Logo />
          <div className="flex items-center">
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
              aria-label={open ? "Close menu" : "Open menu"}
              aria-expanded={open}
              aria-controls="mobile-nav"
              onClick={() => setOpen((v) => !v)}
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg hover:bg-ink/5"
            >
              {open ? <IconClose /> : <IconMenu />}
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
          <div className="flex justify-end">
            <button type="button" onClick={() => setOpen(false)} className="min-h-11 rounded-lg px-3 text-sm text-ink-soft hover:bg-ink/5">
              Close menu
            </button>
          </div>
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
        <div className="min-h-0 flex-1 overflow-y-auto">{nav}</div>
        <div className="pt-3">{footer}</div>
      </aside>
    </>
  );
}

/**
 * Every merchant one click away, each with the last result this tab saw.
 * Findings sort first. No run is made here: the glyphs come from the
 * in-tab store fed by Overview, Findings and the merchant's own Run.
 */
function MerchantSwitcher({ merchants, results, path }: {
  merchants: MerchantLink[];
  results: Record<string, RunDigest>;
  path: string;
}) {
  const [q, setQ] = useState("");
  const weight = (id: string) => {
    const d = results[id];
    if (!d) return 3;
    if (!d.ok) return 1;
    return d.twice ? 0 : d.open ? 1 : 2;
  };
  const shown = merchants
    .filter((m) => m.name.toLowerCase().includes(q.trim().toLowerCase()))
    .sort((a, b) => weight(a.id) - weight(b.id) || a.name.localeCompare(b.name));
  return (
    <section aria-labelledby="switcher-title" className="mt-6">
      <h2 id="switcher-title" className="mb-1 px-3 text-xs font-medium text-ink-soft">Merchants</h2>
      {merchants.length > MERCHANT_LIST && (
        <label className="mb-1 block px-1">
          <span className="sr-only">Filter merchants</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter…" className="field min-h-9 py-1.5 text-xs" />
        </label>
      )}
      <ul className="flex flex-col gap-0.5 text-sm">
        {shown.slice(0, q ? undefined : MERCHANT_LIST).map((m) => {
          const href = `/merchants/${encodeURIComponent(m.id)}`;
          const active = path === href;
          return (
            <li key={m.id}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={`flex min-h-10 items-center gap-2.5 rounded-lg px-3 ${
                  active ? "bg-ink/[0.06] font-medium text-ink" : "text-ink-soft hover:bg-ink/[0.04] hover:text-ink"
                }`}
              >
                <Glyph d={results[m.id]} />
                <span className="min-w-0 flex-1 truncate">{m.name}</span>
              </Link>
            </li>
          );
        })}
      </ul>
      {!q && merchants.length > MERCHANT_LIST && (
        <Link href="/merchants" className="mt-1 block px-3 py-2 text-xs text-ink-soft underline-offset-4 hover:underline">
          All {merchants.length} merchants
        </Link>
      )}
    </section>
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

/** Overview and Findings run a metered check per ready merchant when they
 *  render, so a link to them is never prefetched: only a click may cost. */
export const RUNS_CHECKS = new Set(["/overview", "/findings"]);

function Item({ href, path, icon, count, exact = false, children }: {
  href: string;
  path: string;
  icon: ReactNode;
  count?: ReactNode;
  exact?: boolean;
  children: ReactNode;
}) {
  const active = path === href || (!exact && path.startsWith(`${href}/`));
  return (
    <Link
      href={href}
      prefetch={RUNS_CHECKS.has(href) ? false : undefined}
      aria-current={active ? "page" : undefined}
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
