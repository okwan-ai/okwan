import { redirect } from "next/navigation";
import { apiUrl } from "@/lib/api";
import { ATTENTION, missingFor, rowKey, toFinding } from "@/lib/finding";
import { configured, connectors, railState } from "@/lib/merchants";
import { defaultTab, tabOf } from "@/lib/merchant-tabs";
import { latestRuns, runHistory, storedRun } from "@/lib/stored-runs";
import { ConnectionsGrid } from "../../_components/connections-grid";
import { FindingsPanel } from "../../_components/findings-panel";
import { RunHistory } from "../../_components/run-history";

/** The active tab's content. The layout has already checked access and
 * fetched what this reads; React's per-render cache answers here. */
export default async function MerchantPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string; days?: string; run?: string; order?: string }>;
}) {
  const [{ id }, { tab, days, run: runParam, order }] = await Promise.all([params, searchParams]);
  // The old tabs' homes (§9 2026-10-05), before any other work. A redirect
  // here, not middleware, so tab=keys never merges into the destination.
  if (tab === "keys") redirect(`/agents?merchant=${encodeURIComponent(id)}`);
  if (tab === "usage") {
    const d = ["7", "30", "90"].includes(days ?? "") ? `&days=${days}` : "";
    redirect(`/settings?tab=plan&merchant=${encodeURIComponent(id)}${d}`);
  }

  // The same cached reads as the layout, so both agree on the default.
  const [catalog, stored, latest] = await Promise.all([connectors(), configured(id), latestRuns(id)]);
  const ready = catalog.ok && stored.ok ? missingFor(railState(catalog.data, stored.data.configured)).length === 0 : true;
  const active = tabOf(tab, defaultTab(ready, Boolean(latest?.[id])), Boolean(order || runParam));

  if (active === "connections") {
    if (!catalog.ok || !stored.ok) return null;
    // The sheet's subtitle says how credentials are kept; no intro here.
    return (
      <ConnectionsGrid
        fold
        tenantId={id}
        tenantKey={id}
        connectors={catalog.data.map((c) => ({ ...c, stored: stored.data.configured[c.name] ?? [] }))}
      />
    );
  }

  // Findings: the newest stored run (held by the layout's provider), or one
  // chosen from the history (?run=). Findings absent from the run before
  // the shown one are marked new.
  // The API keeps the newest 50 and lists at most 50.
  const history = (await runHistory(id, 50)) ?? [];
  const shownId = runParam && history.some((r) => r.id === runParam) ? runParam : (history[0]?.id ?? null);
  const index = history.findIndex((r) => r.id === shownId);
  const previousId = index >= 0 ? history.slice(index + 1).find((r) => r.status === "ok")?.id ?? null : null;
  const [chosen, previous] = await Promise.all([
    runParam && shownId === runParam && index > 0 ? storedRun(id, shownId) : Promise.resolve(null),
    previousId ? storedRun(id, previousId) : Promise.resolve(null),
  ]);
  const view = chosen && chosen.status === "ok" && chosen.summary
    ? { finding: toFinding({ summary: chosen.summary, rows: chosen.rows ?? [], has_more: chosen.has_more ?? false, twice_currency: chosen.twice_currency }), at: Date.parse(chosen.finished_at), runId: chosen.id, surface: chosen.surface }
    : null;
  // An older run that failed shows its error, not the newest result.
  const viewError = chosen && chosen.status === "failed" ? { detail: chosen.error ?? "the run failed", at: Date.parse(chosen.finished_at) } : null;
  const before = new Set(
    previous?.summary ? toFinding({ summary: previous.summary, rows: previous.rows ?? [], has_more: false }).rows.filter((r) => ATTENTION.includes(r.outcome)).map(rowKey) : [],
  );
  const newest = !chosen && history[0]?.status === "ok" && history[0].summary ? history[0] : null;
  const shownRows = view?.finding.rows ?? (newest
    ? toFinding({ summary: newest.summary!, rows: (await storedRun(id, newest.id))?.rows ?? [], has_more: false }).rows
    : []);
  const newKeys = previous ? shownRows.filter((r) => ATTENTION.includes(r.outcome)).map(rowKey).filter((k) => !before.has(k)) : [];
  return (
    <div className="space-y-6">
      <FindingsPanel apiBase={apiUrl()} view={view} viewError={viewError} newKeys={newKeys} saved={history.length} />
      <RunHistory runs={history} selected={shownId} href={(runId) => (runId ? `?run=${encodeURIComponent(runId)}` : "?tab=findings")} />
    </div>
  );
}
