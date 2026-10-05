import Link from "next/link";
import { planState, resetAfter, type Usage } from "@/lib/usage-shape";

/**
 * The page-level warning on the pages whose next click spends a request.
 * Nothing below 80% of the plan: a healthy account never sees it. At 80%
 * it states the numbers only (what a click costs sits under the button
 * that spends it); at the limit it states the consequence (a 402 on REST
 * and checks, an error result for an agent) and the reset instant from the
 * API's month start, never the browser clock. The sidebar meter stays the
 * always-on gauge. Never volt; warn is ink, danger is danger.
 */
export function PlanStrip({ usage }: { usage: Usage | null }) {
  if (!usage || usage.plan.unmetered) return null;
  const s = planState(usage.plan);
  if (s.state === "ok") return null;
  const p = usage.plan;
  const danger = s.state === "danger";
  const n = (v: number) => v.toLocaleString("en-US");
  return (
    <div
      role={danger ? "alert" : "status"}
      className={`mb-6 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded-xl border px-4 py-2.5 text-sm ${
        danger ? "border-danger/30 bg-danger-soft text-danger" : "border-ink-soft/50 bg-surface text-ink"
      }`}
    >
      <p className="min-w-0">
        <span aria-hidden className="mr-1.5 font-mono">{danger ? "✕" : "!"}</span>
        {danger ? (
          <>
            Limit reached: {n(p.used)} of {n(p.limit)} requests this month. Checks and REST are refused (HTTP 402) and an
            agent&apos;s tool call returns the same message as an error, until {resetAfter(p.month_start)}.
          </>
        ) : (
          <>
            {n(p.used)} of {n(p.limit)} requests used this month · {n(p.remaining ?? 0)} left.
          </>
        )}
      </p>
      <Link href="/settings?tab=plan" className={`font-medium whitespace-nowrap underline-offset-4 hover:underline ${danger ? "text-danger" : "text-ink"}`}>
        Plan &amp; usage →
      </Link>
    </div>
  );
}
