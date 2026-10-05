"use client";

import Link from "next/link";

export type SegmentItem = { key: string; label: string; count?: number; glyph?: string; href?: string };

const GROUP = {
  light: "inline-flex shrink-0 rounded-lg border border-line bg-surface p-0.5",
  navy: "inline-flex shrink-0 rounded-lg bg-canvas/10 p-0.5",
} as const;

const ITEM_BASE = "inline-flex min-h-11 items-center gap-1.5 whitespace-nowrap rounded-md px-3 text-sm font-medium";
const ITEM = {
  light: `${ITEM_BASE} text-ink-soft hover:text-ink aria-pressed:bg-ink aria-pressed:text-canvas aria-[current=page]:bg-ink aria-[current=page]:text-canvas`,
  navy: `${ITEM_BASE} text-canvas/80 hover:text-canvas aria-pressed:bg-canvas aria-pressed:text-ink aria-[current=page]:bg-canvas aria-[current=page]:text-ink focus-visible:outline-canvas`,
} as const;

/**
 * One choice among a few: filters, a range, a channel. Buttons carry
 * aria-pressed; items with an href are links carrying aria-current and
 * keep the scroll position. Every item keeps the 44px target. The glyph is
 * decorative (the label says it); the count is read with the label.
 */
export function Segmented({
  items,
  value,
  onChange,
  label,
  tone = "light",
}: {
  items: SegmentItem[];
  value: string;
  onChange?: (key: string) => void;
  label: string;
  tone?: "light" | "navy";
}) {
  return (
    <div role="group" aria-label={label} className={GROUP[tone]}>
      {items.map((it) => {
        const on = it.key === value;
        const body = (
          <>
            {it.glyph && <span aria-hidden className="font-mono text-xs">{it.glyph}</span>}
            {it.label}
            {it.count !== undefined && <span className="tabular-nums opacity-70">{it.count.toLocaleString("en-US")}</span>}
          </>
        );
        return it.href ? (
          <Link key={it.key} href={it.href} scroll={false} aria-current={on ? "page" : undefined} className={ITEM[tone]}>
            {body}
          </Link>
        ) : (
          <button key={it.key} type="button" aria-pressed={on} onClick={() => onChange?.(it.key)} className={ITEM[tone]}>
            {body}
          </button>
        );
      })}
    </div>
  );
}
