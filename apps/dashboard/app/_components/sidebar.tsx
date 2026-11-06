"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { type RunDigest, type SeenFinding, verdictOf } from "@/lib/finding";
import { CommandPalette, openPalette } from "./command-palette";
import { openSupport } from "./support-panel";
import { useDialog } from "./ui/dialog";
import type { Usage } from "@/lib/usage-shape";
import { IconArrowRight, IconClose, IconGear, IconHelp, IconLock, IconMenu, IconSearch, IconSignOut, NavAgents, NavApi, NavFindings, NavHome, NavIntegrations, NavMerchants } from "./ui/icons";
import { planState } from "@/lib/usage-shape";
import { OkwanLogo } from "./ui/mark";

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
    <nav aria-label="Main" className="flex flex-col gap-[3px] text-[17px] 2xl:text-[19px]">
      <Item href="/overview" path={path} icon={<NavHome />} first>Overview</Item>
      <Item
        href="/findings"
        path={path}
        icon={<NavFindings />}
        count={
          ok.length ? <Count n={openFindings} label="open findings" strong={openFindings > 0} />
            : failed ? <Count text="!" label="checks couldn't run" strong />
              : null
        }
      >
        Findings
      </Item>
      <Item href="/merchants" path={path} icon={<NavMerchants />} count={<Count n={merchants.length} label="merchants" />}>
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
      <p className="mt-6 mb-1 px-[13px] text-[14px] font-medium text-ink-soft 2xl:text-[15px]">Connect</p>
      {/* The glass narrows here (the waist of the drawing), so these rows do too. */}
      <div className="flex flex-col gap-[3px] min-[900px]:mr-5">
        <Item href="/integrations" path={path} icon={<NavIntegrations />}>Integrations</Item>
        <Item href="/agents" path={path} icon={<NavAgents />}>Agents</Item>
        <a
          href={`${apiBase}/docs`}
          target="_blank"
          rel="noopener"
          className="flex min-h-[51px] items-center gap-5 rounded-full px-[22px] font-medium text-ink hover:bg-accent-soft/70"
        >
          <NavApi />
          <span className="flex-1">API reference</span>
          <span className="sr-only">(opens in a new tab)</span>
        </a>
        {operator && (
          <>
            <p className="mt-6 mb-1 px-[13px] text-[14px] font-medium text-ink-soft 2xl:text-[15px]">Run Okwan</p>
            <Item href="/ops" path={path} icon={<IconLock />}>Console</Item>
          </>
        )}
      </div>
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
        <div className="flex items-center justify-between gap-1 px-3 py-1.5">
          <Logo />
          {current && <span className="min-w-0 flex-1 truncate px-1 text-sm font-medium">{current.name}</span>}
          <div className="flex shrink-0 items-center">
            <button
              type="button"
              aria-label="Ask Okwan"
              onClick={openSupport}
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-accent hover:bg-accent-soft"
            >
              <IconHelp />
            </button>
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
      <aside className="sticky top-0 hidden h-screen w-[300px] shrink-0 min-[900px]:block 2xl:w-[316px]">
        <Glass />
        <div className="relative flex h-full flex-col overflow-y-auto pt-[60px] pr-[44px] pb-[76px] pl-10 [scrollbar-width:none] 2xl:pr-[46px] [@media(max-height:860px)]:pt-9 [@media(max-height:860px)]:pb-12">
          <div className="mb-[44px] pl-[38px] [@media(max-height:860px)]:mb-7"><Logo /></div>
          {nav}
          <div className="min-h-[88px] flex-1" />
          <div className="relative">
            <PlanBubble />
            <div className="relative">{footer}</div>
          </div>
        </div>
      </aside>
    </>
  );
}

/**
 * The sidebar's glass, as the owner drew it: a frosted panel whose right
 * edge swells by the main pages, narrows by Connect and swells again by
 * the plan. Decoration only; it stretches with the window, its edges stay
 * a hairline.
 */
function Glass() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 320 940"
      preserveAspectRatio="none"
      className="pointer-events-none absolute inset-0 h-full w-full overflow-visible drop-shadow-[0_24px_40px_rgba(60,40,160,0.12)]"
    >
      <defs>
        <linearGradient id="glass-fill" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.78" />
          <stop offset="1" stopColor="#f4f2ff" stopOpacity="0.62" />
        </linearGradient>
      </defs>
      <path
        d="M72 16H232C276 16 302 44 302 92V300C302 352 258 392 258 452V560C258 628 318 652 318 730V872C318 906 296 928 262 928H64C34 928 12 906 12 876V76C12 42 38 16 72 16Z"
        fill="url(#glass-fill)"
        stroke="#ffffff"
        strokeOpacity="0.95"
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/** The bubble that holds the plan, lit violet along its rim. It wraps the
 *  plan card, not the window, so a short screen never pushes it into the
 *  links above. */
function PlanBubble() {
  return (
    <svg
      aria-hidden
      viewBox="12 652 306 276"
      preserveAspectRatio="none"
      className="pointer-events-none absolute -top-16 -right-11 -bottom-16 -left-7 h-[calc(100%+8rem)] w-[calc(100%+4.5rem)] overflow-visible"
    >
      <defs>
        <linearGradient id="bubble-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.35" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="0.9" />
        </linearGradient>
        <linearGradient id="bubble-rim" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#a58bff" stopOpacity="0" />
          <stop offset="0.55" stopColor="#8a6bff" stopOpacity="0.55" />
          <stop offset="1" stopColor="#6d45f5" stopOpacity="0.9" />
        </linearGradient>
        <filter id="rim-blur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3" /></filter>
      </defs>
      <path
        d="M12 724C40 676 110 652 176 654C250 656 306 694 318 750V872C318 906 296 928 262 928H64C34 928 12 906 12 876Z"
        fill="url(#bubble-fill)"
        stroke="#ffffff"
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
      />
      <path d="M150 655C228 652 298 688 316 746" fill="none" stroke="url(#bubble-rim)" strokeWidth="7" strokeLinecap="round" filter="url(#rim-blur)" />
    </svg>
  );
}

/** The month against the plan, and the one way up from Free. */
function PlanCard({ plan }: { plan: Usage["plan"] | null }) {
  if (!plan) {
    return (
      <div className="px-[13px] text-sm">
        <p className="text-[18px] font-bold 2xl:text-[20px]">Plan &amp; usage</p>
        <p className="mt-1 text-ink-soft">Usage isn&apos;t available right now.</p>
      </div>
    );
  }
  const s = planState(plan);
  const free = plan.name === "free";
  const fill = s.state === "danger" ? "bg-danger" : "bg-[linear-gradient(90deg,#2a22f4,var(--color-meter))]";
  const line = plan.unmetered
    ? `${plan.used.toLocaleString("en-US")} requests this month`
    : `${plan.used.toLocaleString("en-US")} of ${plan.limit.toLocaleString("en-US")} this month`;
  return (
    <div className="pr-[2px] pl-[13px]">
      <p className="text-[18px] font-bold capitalize 2xl:text-[20px]">{plan.name} Plan</p>
      {s.share !== null && (
        <div
          role="progressbar"
          aria-label="Requests used this month"
          aria-valuemin={0}
          aria-valuemax={plan.limit}
          aria-valuenow={Math.min(plan.used, plan.limit)}
          aria-valuetext={s.text}
          className="mt-3 h-3.5 overflow-hidden rounded-full bg-[#cbcdf2]"
        >
          {/* A pill stays visible at zero, so the bar reads as a bar. */}
          <div className={`h-full min-w-3.5 rounded-full ${fill}`} style={{ width: `${s.share * 100}%` }} />
        </div>
      )}
      <p className={`mt-2.5 text-[14px] 2xl:text-[15px] ${s.state === "danger" ? "font-semibold text-danger" : "text-ink"}`}>
        {s.state === "danger" ? "Limit reached this month" : line}
      </p>
      <Link
        href="/settings?tab=plan"
        className="mt-6 -mr-[4px] -ml-[3px] flex min-h-12 items-center justify-center gap-3 rounded-full bg-white text-[16px] font-bold text-accent shadow-[0_10px_24px_-12px_rgba(52,40,160,0.45),0_0_0_1px_rgba(232,233,246,0.9)] hover:bg-accent-soft 2xl:text-[18px]"
      >
        {free ? "Upgrade Plan" : "Manage plan"} <IconArrowRight />
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
    <Link href="/overview" prefetch={false} aria-label="Okwan, overview" className="inline-flex min-h-11 items-center rounded-lg">
      <span className="min-[900px]:hidden"><OkwanLogo width={96} /></span>
      <span className="hidden min-[900px]:block"><OkwanLogo width={124} /></span>
    </Link>
  );
}

function Item({ href, path, icon, count, first = false, children }: {
  href: string;
  path: string;
  icon: ReactNode;
  count?: ReactNode;
  /** The first row: a little air below it, as drawn. */
  first?: boolean;
  children: ReactNode;
}) {
  const active = path === href || path.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      // Looks active on a sub-page (/integrations/paypal) but names the page
      // only on its own URL; the sub-page's breadcrumb carries aria-current.
      aria-current={path === href ? "page" : undefined}
      className={`flex min-h-[51px] items-center gap-5 rounded-full pr-1 pl-[22px] ${first ? "mb-[11px]" : ""} ${
        active
          ? "bg-pill font-bold text-white"
          : "font-medium text-ink hover:bg-accent-soft/70"
      }`}
    >
      {icon}
      <span className="flex-1">{children}</span>
      {count}
    </Link>
  );
}

function Count({ n, text, label, strong = false }: { n?: number; text?: string; label: string; strong?: boolean }) {
  return (
    <span
      className={`inline-flex h-[34px] min-w-[34px] items-center justify-center rounded-full px-2 text-[17px] font-bold tabular-nums ${
        strong ? "bg-signal text-ink" : "bg-[#d1d0f1] text-ink"
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
