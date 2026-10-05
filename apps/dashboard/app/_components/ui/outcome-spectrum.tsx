import type { Summary } from "@/lib/finding";
import { OUTCOME_MARK } from "@/lib/finding";

type Segment = { key: string; label: string; n: number; fill: string };

/** The same six outcomes as the fold, in the order a reader should see them:
 *  clean first, then money findings, then what couldn't be judged. */
function segments(s: Pick<Summary, "collected" | "split_tender" | "collected_twice" | "collected_inconsistent" | "uncollected" | "unverifiable">): Segment[] {
  return [
    { key: "collected", label: "Paid once", n: s.collected + s.split_tender, fill: "bg-ok" },
    { key: "collected_twice", label: "Collected twice", n: s.collected_twice, fill: "bg-danger" },
    { key: "collected_inconsistent", label: "Doesn't add up", n: s.collected_inconsistent, fill: "bg-hatch-ink" },
    { key: "uncollected", label: "No payment", n: s.uncollected, fill: "bg-ink-soft" },
    { key: "unverifiable", label: "Couldn't verify", n: s.unverifiable, fill: "bg-line" },
  ];
}

/**
 * Every order checked, as one bar split by outcome. Fills are paired with a
 * symbol and a count in the legend, so the bar is never the only carrier;
 * the bar itself is hidden from assistive tech and the legend is the text.
 */
export function OutcomeSpectrum({ summary, unconfirmed = 0, compact = false, animate = false }: {
  summary: Parameters<typeof segments>[0];
  /** Settle into place: only for a result the viewer just ran. */
  animate?: boolean;
  /** Of the paid-once orders, those another rail couldn't rule out a second
   *  payment for. Drawn as an outlined part of the paid-once segment. */
  unconfirmed?: number;
  compact?: boolean;
}) {
  const base = segments(summary);
  const paid = base[0];
  const doubt = Math.min(unconfirmed, paid.n);
  const segs: Segment[] = doubt
    ? [
      { ...paid, n: paid.n - doubt },
      { key: "collected_unconfirmed", label: "Paid once, not ruled out elsewhere", n: doubt, fill: "bg-ok-soft ring-1 ring-inset ring-ok" },
      ...base.slice(1),
    ]
    : base;
  const total = segs.reduce((n, s) => n + s.n, 0);
  if (!total) return null;
  return (
    <div className="min-w-0">
      <div aria-hidden className={`${animate ? "animate-settle " : ""}flex w-full overflow-hidden rounded-full bg-canvas ${compact ? "h-2" : "h-3"}`}>
        {segs.filter((s) => s.n).map((s) => (
          <span key={s.key} className={`${s.fill} h-full border-r-2 border-surface last:border-r-0`} style={{ width: `${(s.n / total) * 100}%` }} />
        ))}
      </div>
      <ul
        className={compact ? "mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-soft" : "mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-ink-soft sm:flex sm:flex-wrap"}
        aria-label="Orders by outcome"
      >
        {segs.filter((s) => s.n || !compact).map((s) => (
          <li key={s.key} className="inline-flex items-center gap-1.5">
            <span aria-hidden className={`inline-block h-2 w-2 rounded-full ${s.fill}`} />
            <span aria-hidden className="font-mono">{OUTCOME_MARK[s.key] ?? "✓?"}</span>
            {s.label} <span className="font-medium text-ink tabular-nums">{s.n.toLocaleString("en-US")}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
