"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";

export type TabBadge = { text: string; tone: "neutral" | "strong" | "ok"; label: string };
export type TabItem = { href: string; label: string; short?: string; active: boolean; badge?: TabBadge };

const BADGE_TONE: Record<TabBadge["tone"], string> = {
  neutral: "bg-ink/5 text-ink-soft",
  strong: "bg-ink font-medium text-canvas",
  ok: "bg-ok-soft text-ok",
};

/**
 * URL-driven tabs for switching views: each is a link, the active one
 * marked for assistive tech. A badge is a short figure with its meaning
 * spoken after it. Below sm a tab may show a shorter label. When the row
 * is wider than the screen, the active tab is scrolled into view along the
 * row only, so the page itself never jumps (a #fragment target stays put).
 */
export function Tabs({ items, label }: { items: TabItem[]; label: string }) {
  const row = useRef<HTMLElement>(null);
  const current = useRef<HTMLAnchorElement>(null);
  const activeHref = items.find((t) => t.active)?.href;

  useEffect(() => {
    const box = row.current;
    const el = current.current;
    if (!box || !el) return;
    // scrollIntoView({ inline: "nearest" }), confined to this row.
    const b = box.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    if (r.left < b.left) box.scrollLeft -= b.left - r.left;
    else if (r.right > b.right) box.scrollLeft += r.right - b.right;
  }, [activeHref]);

  return (
    <nav ref={row} aria-label={label} className="-mx-1 overflow-x-auto border-b border-line">
      <ul className="flex min-w-max gap-1 px-1">
        {items.map((t) => (
          <li key={t.href}>
            <Link
              ref={t.active ? current : undefined}
              href={t.href}
              scroll={false}
              aria-current={t.active ? "page" : undefined}
              className={`-mb-px inline-flex min-h-11 items-center gap-2 border-b-2 px-3 text-sm font-medium ${
                t.active ? "border-ink text-ink" : "border-transparent text-ink-soft hover:text-ink"
              }`}
            >
              {t.short ? (
                <>
                  <span className="sm:hidden">{t.short}</span>
                  <span className="hidden sm:inline">{t.label}</span>
                </>
              ) : (
                t.label
              )}
              {t.badge && (
                <>
                  <span className={`rounded-full px-1.5 text-xs tabular-nums ${BADGE_TONE[t.badge.tone]}`}>{t.badge.text}</span>
                  <span className="sr-only"> {t.badge.label}</span>
                </>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
