import Link from "next/link";
import { ago, SURFACE_LABEL } from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import type { StoredRun } from "@/lib/stored-runs";
import { Badge } from "./ui/badge";

/**
 * A merchant's stored runs, newest first: when, from which surface, how
 * it ended, and what was collected twice. Each is a link to that run on
 * the Findings tab; the one shown is marked. The API keeps the newest 50.
 */
export function RunHistory({ runs, selected, href }: {
  runs: StoredRun[];
  /** The run the tab is showing. */
  selected: string | null;
  /** Builds the link for a run id; null links back to the latest. */
  href: (runId: string | null) => string;
}) {
  if (runs.length === 0) return null;
  return (
    <section aria-labelledby="run-history-title" className="rounded-xl border border-line bg-surface">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-5 py-3">
        <h2 id="run-history-title" className="text-sm font-semibold">Run history</h2>
        {/* 50 mirrors okwan_vault RUNS_KEPT and the listing's maximum. */}
        <p className="text-xs text-ink-soft">Runs of the rails check from this dashboard, REST or an agent. The newest 50 are kept and listed.</p>
      </div>
      <ol className="divide-y divide-line">
        {runs.map((r, i) => {
          const at = Date.parse(r.finished_at);
          const current = selected ? r.id === selected : i === 0;
          const twice = r.summary?.collected_twice ?? 0;
          const cur = twice > 0 ? r.twice_currency : null;
          return (
            <li key={r.id}>
              <Link
                href={href(i === 0 ? null : r.id)}
                scroll={false}
                aria-current={current ? "true" : undefined}
                className={`grid min-h-11 items-center gap-x-4 gap-y-1 border-l-2 px-5 py-2 text-sm sm:grid-cols-[170px_120px_1fr_auto] ${
                  current ? "border-ink bg-canvas/70" : "border-transparent hover:bg-canvas/40"
                }`}
              >
                <span className={current ? "font-semibold" : "font-medium"}>
                  <time dateTime={r.finished_at} suppressHydrationWarning>{ago(at)}</time>
                  {i === 0 && <span className="ml-1.5 text-xs font-normal text-ink-soft">latest</span>}
                  {current && <span className="ml-1.5 text-xs font-normal text-ink-soft">· shown</span>}
                </span>
                <span className="text-ink-soft">{SURFACE_LABEL[r.surface] ?? r.surface}</span>
                <span>
                  {r.status === "ok"
                    ? <Badge tone={twice ? "danger" : "ok"} symbol={twice ? "×2" : "✓"}>{twice ? `${twice} collected twice` : "No double collection"}</Badge>
                    : <Badge tone="danger" symbol="!">Couldn&apos;t run</Badge>}
                  {r.status === "failed" && r.error && <span className="ml-2 text-xs break-words text-ink-soft">{r.error}</span>}
                  {r.status === "ok" && r.summary && (
                    <span className="ml-2 text-xs text-ink-soft tabular-nums">{r.summary.orders.toLocaleString("en-US")} orders</span>
                  )}
                </span>
                <span className="text-right font-medium tabular-nums">
                  {twice > 0 ? (cur ? formatMinor(r.summary!.overcollected_minor, cur) + " owed back" : `${twice} orders`) : ""}
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
