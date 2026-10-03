import type { Usage } from "@/lib/usage-shape";
import { planState } from "@/lib/usage-shape";

/** The month against the plan. The fill carries the state (navy → warn →
 *  danger); the track is a lighter step of the same hue; the text and a
 *  symbol say the same, so the bar is never the only carrier. */
export function PlanMeter({ plan, compact = false }: { plan: Usage["plan"]; compact?: boolean }) {
  const s = planState(plan);
  const fill = { ok: "bg-navy", warn: "bg-ink", danger: "bg-danger" }[s.state];
  const mark = { ok: "", warn: "!", danger: "✕" }[s.state];
  return (
    <div className="min-w-0">
      <div className={`flex items-baseline justify-between gap-2 ${compact ? "text-xs" : "text-sm"}`}>
        <span className="font-medium capitalize">{plan.name} plan</span>
        {!compact && s.share !== null && <span className="text-ink-soft tabular-nums">{Math.round(s.share * 100)}%</span>}
      </div>
      {s.share !== null && (
        <div
          role="progressbar"
          aria-label="Requests used this month"
          aria-valuemin={0}
          aria-valuemax={plan.limit}
          aria-valuenow={Math.min(plan.used, plan.limit)}
          aria-valuetext={s.text}
          className={`mt-1 overflow-hidden rounded-full bg-navy/10 ${compact ? "h-1.5" : "h-2"}`}
        >
          <div className={`h-full rounded-full ${fill}`} style={{ width: `${s.share * 100}%` }} />
        </div>
      )}
      <p className={`mt-1 ${compact ? "text-[11px]" : "text-xs"} ${s.state === "ok" ? "text-ink-soft" : "text-ink"}`}>
        {mark && <span aria-hidden className="mr-1 font-mono">{mark}</span>}
        {s.text}
      </p>
    </div>
  );
}
