import type { Ranked } from "@/lib/usage-shape";

/** A ranked list with an inline bar in the one hue: identity is the label,
 *  magnitude is the bar and the number. */
export function Breakdown({ title, rows, empty }: { title: string; rows: Ranked[]; empty: string }) {
  return (
    <section aria-label={title} className="min-w-0">
      <h3 className="mb-2 text-sm font-semibold">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-xs text-ink-soft">{empty}</p>
      ) : (
        <ol className="space-y-2">
          {rows.map((r) => (
            <li key={r.key} className="text-xs">
              <div className="flex items-baseline justify-between gap-3">
                <span className="truncate">{r.label}</span>
                <span className="shrink-0 font-medium tabular-nums">
                  {r.requests.toLocaleString("en-US")}
                  <span className="ml-1 font-normal text-ink-soft">{Math.round(r.share * 100)}%</span>
                </span>
              </div>
              <div aria-hidden className="mt-1 h-1.5 overflow-hidden rounded-full bg-navy/10">
                <div className="h-full rounded-full bg-navy" style={{ width: `${Math.max(r.share * 100, 1)}%` }} />
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
