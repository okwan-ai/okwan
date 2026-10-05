import Link from "next/link";
import { ago, SURFACE_LABEL } from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import type { StoredRun } from "@/lib/stored-runs";
import { Badge } from "./ui/badge";
import { Card, CardHeader } from "./ui/card";
import { CopyButton } from "./ui/copy-button";

/** Rows shown before the "Show all" disclosure. */
const VISIBLE = 5;

/**
 * A merchant's saved checks, newest first: when, from which channel, how
 * it ended, and what was collected twice. Each is a link to that check on
 * the Findings tab; the one shown is marked. The API keeps the newest 50.
 */
export function RunHistory({ runs, selected, href, id = "history" }: {
  runs: StoredRun[];
  /** The run the tab is showing. */
  selected: string | null;
  /** Builds the link for a run id; null links back to the latest. */
  href: (runId: string | null) => string;
  id?: string;
}) {
  if (runs.length === 0) return null;
  const row = (r: StoredRun, i: number) => {
    const at = Date.parse(r.finished_at);
    const current = selected ? r.id === selected : i === 0;
    const twice = r.summary?.collected_twice ?? 0;
    const cur = twice > 0 ? r.twice_currency : null;
    return (
      <li key={r.id} className={`flex items-center border-l-2 ${current ? "border-ink bg-canvas/70" : "border-transparent"}`}>
        <Link
          href={href(i === 0 ? null : r.id)}
          scroll={false}
          aria-current={current ? "true" : undefined}
          className="grid min-h-11 flex-1 grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-5 py-2 text-sm hover:bg-canvas/40 sm:grid-cols-[170px_120px_1fr_auto]"
        >
          <span className={`col-span-2 sm:col-span-1 ${current ? "font-semibold" : "font-medium"}`}>
            <time dateTime={r.finished_at} suppressHydrationWarning>{ago(at)}</time>
            {i === 0 && <span className="ml-1.5 text-xs font-normal text-ink-soft">latest</span>}
            {current && <span className="ml-1.5 text-xs font-normal text-ink-soft">· shown</span>}
            <span className="font-normal text-ink-soft sm:hidden"> · {SURFACE_LABEL[r.surface] ?? r.surface}</span>
          </span>
          <span className="hidden text-ink-soft sm:block">{SURFACE_LABEL[r.surface] ?? r.surface}</span>
          <span className="min-w-0">
            {r.status === "ok"
              ? <Badge tone={twice ? "danger" : "ok"} symbol={twice ? "×2" : "✓"}>{twice ? `${twice} collected twice` : "No double collection"}</Badge>
              : <Badge tone="danger" symbol="!">Couldn&apos;t run</Badge>}
            {r.status === "failed" && r.error && <span className="ml-2 text-xs break-words text-ink-soft">{r.error}</span>}
            {r.status === "ok" && r.summary && (
              <span className="ml-2 text-xs text-ink-soft tabular-nums">{r.summary.orders.toLocaleString("en-US")} orders</span>
            )}
            <span className="ml-2 hidden font-mono text-xs text-ink-soft sm:inline">{r.id.slice(0, 8)}…</span>
          </span>
          <span className="text-right font-medium tabular-nums">
            {twice > 0 ? (cur ? <>{formatMinor(r.summary!.overcollected_minor, cur)}<span className="hidden sm:inline"> owed back</span></> : `${twice} orders`) : ""}
          </span>
        </Link>
        <span className="pr-2"><CopyButton value={r.id} label={`Copy run id ${r.id}`} /></span>
      </li>
    );
  };
  return (
    <Card flush id={id} aria-labelledby={`${id}-title`} className="scroll-mt-6">
      <CardHeader id={`${id}-title`} title="Saved checks" description="Latest 50 checks, from any channel." />
      <ol className="divide-y divide-line">{runs.slice(0, VISIBLE).map(row)}</ol>
      {runs.length > VISIBLE && (
        <details className="border-t border-line" open={runs.slice(VISIBLE).some((r) => r.id === selected) || undefined}>
          <summary className="flex min-h-11 cursor-pointer items-center px-5 text-sm text-ink-soft hover:text-ink">
            Show all {runs.length} saved checks
          </summary>
          <ol start={VISIBLE + 1} className="divide-y divide-line border-t border-line">
            {runs.slice(VISIBLE).map((r, i) => row(r, i + VISIBLE))}
          </ol>
        </details>
      )}
    </Card>
  );
}
