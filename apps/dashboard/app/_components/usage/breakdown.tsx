import Link from "next/link";
import type { Ranked } from "@/lib/usage-shape";

/** A ranked list with an inline bar in the one hue: identity is the label,
 *  magnitude is the bar and the number. The Card around it names it, so
 *  `title` is the list's accessible name only. `hrefs` (by row key) makes
 *  a row a link, e.g. to that merchant's usage. */
export function Breakdown({ title, rows, empty, hrefs }: { title: string; rows: Ranked[]; empty: string; hrefs?: Record<string, string> }) {
  if (rows.length === 0) return <p className="text-xs text-ink-soft">{empty}</p>;
  return (
    <ol aria-label={title} className="space-y-1">
      {rows.map((r) => {
        const body = (
          <>
            <span className="flex items-baseline justify-between gap-3">
              <span className={`truncate${hrefs?.[r.key] ? " underline-offset-4 group-hover:underline" : ""}`}>{r.label}</span>
              <span className="shrink-0 font-medium tabular-nums">
                {r.requests.toLocaleString("en-US")}
                <span className="ml-1 font-normal text-ink-soft">{Math.round(r.share * 100)}%</span>
              </span>
            </span>
            <span aria-hidden className="mt-1 block h-1.5 overflow-hidden rounded-full bg-navy/10">
              <span className="block h-full rounded-full bg-navy" style={{ width: `${Math.max(r.share * 100, 1)}%` }} />
            </span>
          </>
        );
        const href = hrefs?.[r.key];
        return (
          <li key={r.key} className="text-xs">
            {href ? (
              <Link href={href} className="group -mx-2 flex min-h-11 flex-col justify-center rounded-lg px-2 hover:bg-ink/[0.04]">{body}</Link>
            ) : (
              <div className="py-1">{body}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
