"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { type RunDigest, type SeenFinding, verdictOf } from "@/lib/finding";
import { CommandPalette, openPalette } from "./command-palette";
import { useDialog } from "./ui/dialog";
import type { Usage } from "@/lib/usage-shape";
import { IconAgent, IconAlert, IconClose, IconGear, IconHome, IconLock, IconMenu, IconPlug, IconSearch, IconSignOut, IconStore, IconTerminal } from "./ui/icons";
import { planState } from "@/lib/usage-shape";
import { SwooshWordmark } from "./ui/mark";

export type MerchantLink = { id: string; name: string };

/** `tenant` is the workspace name. A self-serve workspace is named by its
 * verified address, so for most accounts this line is the email; the API
 * exposes no other. */
export function Sidebar({ tenant, merchants, connectors, plan, apiBase, verdicts, findings, operator = false }: {
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
  /** Okwan staff on the operator allowlist: show the console link. The API
   *  decides; for everyone else the link does not exist. */
  operator?: boolean;
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
    <nav aria-label="Main" className="flex flex-col gap-1.5 text-[15px]">
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
          className="ml-9 flex min-h-11 items-center gap-2.5 rounded-xl bg-accent-soft px-3 text-sm font-semibold text-accent"
        >
          <Glyph d={results[current.id]} />
          <span className="min-w-0 flex-1 truncate">{current.name}</span>
        </Link>
      )}
      <p className="mt-7 mb-1.5 px-4 text-[13px] font-medium text-ink-muted">Connect</p>
      <Item href="/integrations" path={path} icon={<IconPlug />}>Integrations</Item>
      <Item href="/agents" path={path} icon={<IconAgent />}>Agents</Item>
      <a
        href={`${apiBase}/docs`}
        target="_blank"
        rel="noopener"
        className="flex min-h-12 items-center gap-3.5 rounded-2xl px-4 font-medium text-ink hover:bg-accent-soft"
      >
        <span className="text-ink-soft"><IconTerminal /></span>
        <span className="flex-1">API reference</span>
        <span aria-hidden className="text-xs">↗</span>
        <span className="sr-only">(opens in a new tab)</span>
      </a>
      {operator && (
        <>
          <p className="mt-7 mb-1.5 px-4 text-[13px] font-medium text-ink-muted">Run Okwan</p>
          <Item href="/ops" path={path} icon={<IconLock />}>Console</Item>
        </>
      )}
    </nav>
  );

  const footer = (
    <div className="space-y-3">
      <PlanCard plan={plan} />
      {/* Phones: the account menu lives here; wide screens have the top bar. */}
      <div className="space-y-1 text-sm min-[900px]:hidden">
        <Link
          href="/settings"
          aria-current={path === "/settings" ? "page" : undefined}
          className="flex min-h-11 items-center gap-3 rounded-xl px-3 hover:bg-accent-soft"
        >
          <span className="text-ink-soft"><IconGear /></span>
          <span className="min-w-0">
            <span className="block font-medium">Settings</span>
            <span className="block truncate text-xs text-ink-soft" title={tenant}>{tenant}</span>
          </span>
        </Link>
        <SignOutButton />
      </div>
    </div>
  );

  return (
    <>
      <CommandPalette merchants={merchants} connectors={connectors} verdicts={verdicts} findings={findings} />

      {/* Narrow: a top bar with search and the menu button. */}
      <header className="sticky top-0 z-40 border-b border-line bg-white/85 backdrop-blur min-[900px]:hidden">
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
          className="absolute inset-x-0 top-full flex h-[calc(100dvh-100%)] flex-col overflow-y-auto overscroll-contain border-t border-line bg-white px-3 py-4"
        >
          {nav}
          <div className="mt-auto pt-6">{footer}</div>
        </div>
      </header>

      {/* Wide: the sidebar, a white panel floating on the canvas. Search is
          ⌘K, or the account menu in the top bar. */}
      <aside className="sticky top-0 hidden h-screen w-[288px] shrink-0 p-5 pr-2 min-[900px]:flex">
        <div className="flex w-full flex-col overflow-y-auto rounded-[32px] border border-white/80 bg-white/80 px-4 py-7 shadow-float backdrop-blur-xl">
          <div className="mb-8 px-3"><Logo /></div>
          {nav}
          <div className="min-h-6 flex-1" />
          {footer}
        </div>
      </aside>
    </>
  );
}

/** The month against the plan, and the one way up from Free. */
function PlanCard({ plan }: { plan: Usage["plan"] | null }) {
  if (!plan) {
    return (
      <div className="rounded-3xl border border-line bg-[linear-gradient(160deg,var(--color-accent-soft),#ffffff_70%)] p-4 text-sm">
        <p className="font-semibold">Plan &amp; usage</p>
        <p className="mt-1 text-xs text-ink-soft">Usage isn&apos;t available right now.</p>
      </div>
    );
  }
  const s = planState(plan);
  const free = plan.name === "free";
  const fill = s.state === "danger" ? "bg-danger" : "bg-[linear-gradient(90deg,var(--color-accent),var(--color-accent-2))]";
  const line = plan.unmetered
    ? `${plan.used.toLocaleString("en-US")} requests this month`
    : `${plan.used.toLocaleString("en-US")} of ${plan.limit.toLocaleString("en-US")} this month`;
  return (
    <div className="rounded-3xl border border-line bg-[linear-gradient(160deg,var(--color-accent-soft),#ffffff_70%)] p-4">
      <p className="text-[15px] font-bold capitalize">{plan.name} Plan</p>
      {s.share !== null && (
        <div
          role="progressbar"
          aria-label="Requests used this month"
          aria-valuemin={0}
          aria-valuemax={plan.limit}
          aria-valuenow={Math.min(plan.used, plan.limit)}
          aria-valuetext={s.text}
          className="mt-3 h-2 overflow-hidden rounded-full bg-white shadow-[inset_0_0_0_1px_var(--color-line)]"
        >
          {/* A sliver stays visible at zero, so the bar reads as a bar. */}
          <div className={`h-full rounded-full ${fill}`} style={{ width: `${Math.max(s.share * 100, 3)}%` }} />
        </div>
      )}
      <p className={`mt-2 text-[13px] ${s.state === "danger" ? "font-semibold text-danger" : "text-ink-soft"}`}>
        {s.state === "danger" ? "Limit reached this month" : line}
      </p>
      <Link
        href="/settings?tab=plan"
        className="mt-4 flex min-h-11 items-center justify-center gap-2 rounded-2xl bg-white text-sm font-semibold text-accent shadow-[0_6px_18px_-10px_rgba(60,38,160,0.5)] hover:bg-accent-soft"
      >
        {free ? "Upgrade Plan" : "Manage plan"} <span aria-hidden>→</span>
      </Link>
    </div>
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
    <Link href="/overview" prefetch={false} aria-label="Okwan, overview" className="inline-flex min-h-11 items-center rounded-lg px-1">
      <SwooshWordmark size={28} />
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
      className={`flex min-h-12 items-center gap-3.5 rounded-2xl px-4 font-medium ${
        active
          ? "bg-[linear-gradient(135deg,var(--color-accent),var(--color-accent-2))] text-white shadow-lift"
          : "text-ink hover:bg-accent-soft"
      }`}
    >
      <span className={active ? "text-white" : "text-ink-soft"}>{icon}</span>
      <span className="flex-1">{children}</span>
      {count}
    </Link>
  );
}

function Count({ n, text, label, strong = false }: { n?: number; text?: string; label: string; strong?: boolean }) {
  return (
    <span
      className={`inline-flex h-7 min-w-7 items-center justify-center rounded-full px-2 text-[13px] font-bold tabular-nums ${
        strong ? "bg-signal text-ink" : "bg-accent-soft text-accent"
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
