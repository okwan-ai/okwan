"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";
import { useTabResults } from "@/lib/tab-results";
import { IconAgent, IconAlert, IconHome, IconKey, IconMenu, IconClose, IconSignOut, IconStore } from "./ui/icons";

/** Nothing sets a plan or exposes usage yet (§10), so this states the plan
 * every tenant is held to and draws no meter it cannot fill. */
const PLAN_NAME = "Free plan";
const PLAN_QUOTA = "5,000 requests/month";

/** `tenant` is the workspace name. A self-serve workspace is named by its
 * verified address, so for most accounts this line is the email; the API
 * exposes no other. */
export function Sidebar({ tenant, merchants }: { tenant: string; merchants: number }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const results = useTabResults();
  const open_findings = Object.values(results).reduce((n, r) => n + r.open, 0);
  const seen = Object.keys(results).length > 0;

  // A link followed on the narrow layout closes the menu behind it.
  useEffect(() => setOpen(false), [path]);

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-0.5 text-sm">
      <Item href="/overview" path={path} icon={<IconHome />}>Overview</Item>
      <Item href="/merchants" path={path} icon={<IconStore />} count={<Count n={merchants} label="merchants" />}>
        Merchants
      </Item>
      <Item
        href="/findings"
        path={path}
        icon={<IconAlert />}
        count={seen ? <Count n={open_findings} label="open findings" strong={open_findings > 0} /> : null}
      >
        Findings
      </Item>
      <p className="mt-6 mb-1 px-3 text-xs font-medium text-ink-soft">Developers</p>
      <Item href="/key" path={path} icon={<IconKey />}>API keys</Item>
      <Item href="/mcp" path={path} icon={<IconAgent />}>MCP for agents</Item>
    </nav>
  );

  const footer = (
    <div className="space-y-3 border-t border-line pt-4 text-xs">
      <div>
        <p className="font-medium text-ink">{PLAN_NAME}</p>
        <p className="text-ink-soft">{PLAN_QUOTA}</p>
      </div>
      <div className="min-w-0">
        <p className="text-ink-soft">Signed in as</p>
        <p className="truncate font-medium text-ink" title={tenant}>{tenant}</p>
      </div>
      <SignOutButton />
    </div>
  );

  return (
    <>
      {/* Narrow: a top bar with the menu button. */}
      <div className="sticky top-0 z-40 flex items-center justify-between border-b border-line bg-canvas/95 px-2 backdrop-blur min-[900px]:hidden">
        <Logo />
        <button
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
      {open && (
        <div id="mobile-nav" className="fixed inset-x-0 top-[45px] bottom-0 z-40 flex flex-col overflow-y-auto border-t border-line bg-canvas px-3 py-4 min-[900px]:hidden">
          {nav}
          <div className="mt-auto pt-6">{footer}</div>
        </div>
      )}

      {/* Wide: the fixed sidebar. */}
      <aside className="sticky top-0 hidden h-screen w-[236px] shrink-0 flex-col border-r border-line bg-surface px-3 py-4 min-[900px]:flex">
        <div className="mb-5"><Logo /></div>
        {nav}
        <div className="mt-auto">{footer}</div>
      </aside>
    </>
  );
}

function Logo() {
  return (
    <Link href="/overview" className="flex min-h-11 items-center gap-2 rounded-lg px-2">
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

function Count({ n, label, strong = false }: { n: number; label: string; strong?: boolean }) {
  return (
    <span
      className={`min-w-6 rounded-full px-1.5 text-center text-xs tabular-nums ${
        strong ? "bg-ink font-medium text-canvas" : "bg-ink/[0.06] text-ink-soft"
      }`}
    >
      {n}
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
