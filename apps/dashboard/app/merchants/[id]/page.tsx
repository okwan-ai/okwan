import { apiUrl } from "@/lib/api";
import { ATTENTION, rowKey, toFinding } from "@/lib/finding";
import { runHistory, storedRun } from "@/lib/stored-runs";
import { usageFor } from "@/lib/usage";
import { configured, connectors } from "@/lib/merchants";
import { tabOf } from "@/lib/merchant-tabs";
import { ConnectionsGrid } from "../../_components/connections-grid";
import { DevSnippets } from "../../_components/dev-snippets";
import { FindingsPanel } from "../../_components/findings-panel";
import { IssueKey } from "../../_components/issue-key";
import { RunHistory } from "../../_components/run-history";
import { PlanUsage } from "../../_components/settings/plan-usage";
import { EmptyState } from "../../_components/ui/empty-state";
import { Section } from "../../_components/ui/page-header";

/** The active tab's content. The layout has already checked access and
 * fetched what this reads; React's per-render cache answers here. */
export default async function MerchantPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string; days?: string; run?: string }>;
}) {
  const [{ id }, { tab, days: daysParam, run: runParam }] = await Promise.all([params, searchParams]);
  const active = tabOf(tab);

  if (active === "connections") {
    const [catalog, stored] = await Promise.all([connectors(), configured(id)]);
    if (!catalog.ok || !stored.ok) return null;
    return (
      <>
        <p className="mb-6 max-w-2xl text-sm text-ink-soft">
          Credentials go straight to an encrypted vault under this merchant and are never shown again. Each test makes one real
          read from the rail.
        </p>
        <ConnectionsGrid
          fold
          tenantId={id}
          tenantKey={id}
          connectors={catalog.data.map((c) => ({ ...c, stored: stored.data.configured[c.name] ?? [] }))}
        />
      </>
    );
  }

  if (active === "keys") {
    return (
      <>
        <p className="mb-4 max-w-2xl text-sm text-ink-soft">
          A key for this merchant reads only this merchant&apos;s rails. Issued once, shown once.
        </p>
        <IssueKey tenantId={id} />
        <Section title="Use the key">
          <DevSnippets />
        </Section>
      </>
    );
  }

  if (active === "usage") {
    const days = [7, 30, 90].includes(Number(daysParam)) ? Number(daysParam) : 30;
    const r = await usageFor(id, days);
    if (!r.ok) {
      return <EmptyState title="Usage isn't available right now">The Okwan API didn&apos;t answer. Try again in a moment.</EmptyState>;
    }
    return (
      <>
        <p className="mb-6 max-w-2xl text-sm text-ink-soft">
          Every request that read this merchant&apos;s rails, whichever surface made it: its agents over MCP, REST and SQL with
          its key, checks from this dashboard, and connection tests. Reading this page is not metered.
        </p>
        <PlanUsage usage={r.data} names={{}} selfId={id} scope="merchant" rangeHref={(d) => `?tab=usage&days=${d}`} />
      </>
    );
  }

  // Findings: the newest stored run (held by the layout's provider), or one
  // chosen from the history (?run=). Findings absent from the run before
  // the shown one are marked new.
  // The API keeps the newest 50 (okwan_vault RUNS_KEPT) and lists at most 50.
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
      <FindingsPanel apiBase={apiUrl()} view={view} viewError={viewError} newKeys={newKeys} />
      <RunHistory runs={history} selected={shownId} href={(runId) => (runId ? `?run=${encodeURIComponent(runId)}` : "?tab=findings")} />
    </div>
  );
}
