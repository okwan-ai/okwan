import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { tenantTree } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { configured, connectors, railState } from "@/lib/merchants";
import { toFinding } from "@/lib/finding";
import { latestRuns } from "@/lib/stored-runs";
import { myUsage } from "@/lib/usage";
import { PlanStrip } from "../../_components/usage/plan-strip";
import { missingFor } from "@/lib/finding";
import { MerchantRunProvider, RunButton } from "../../_components/merchant-run";
import { MerchantTabs } from "../../_components/merchant-tabs";
import { RailChips } from "../../_components/rail-chips";
import { CopyButton } from "../../_components/ui/copy-button";
import { PageHeader } from "../../_components/ui/page-header";

/** The merchant's name in the tab title; the tab itself is in the page. */
export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const tree = await tenantTree();
  return { title: tree?.children.find((c) => c.id === id)?.name ?? "Merchant" };
}

/**
 * One merchant: header, tabs, and the run they share. The run lives here,
 * not in the page, so it survives switching tabs. The id goes to the API
 * as given: whether the caller may administer it is the subtree guard's
 * answer, and its 404 (foreign or unknown) becomes this page's 404.
 */
export default async function MerchantLayout({
  params,
  children,
}: {
  params: Promise<{ id: string }>;
  children: React.ReactNode;
}) {
  await requireTenant();
  const { id } = await params;
  const [catalog, stored, tree, usage, latest] = await Promise.all([connectors(), configured(id), tenantTree(), myUsage(30), latestRuns(id)]);
  if (!stored.ok && stored.status === 404) notFound();
  if (!catalog.ok || !stored.ok) {
    return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  }
  // Display only: a grandchild is not in the direct list, so it shows its id.
  const name = tree?.children.find((c) => c.id === id)?.name || id;
  const rails = railState(catalog.data, stored.data.configured);
  // The newest stored run for this merchant: a read, never a run.
  const last = latest?.[id] ?? null;
  const initial = last && last.status === "ok" && last.summary
    ? { finding: toFinding({ summary: last.summary, rows: last.rows ?? [], has_more: last.has_more ?? false, twice_currency: last.twice_currency }), at: Date.parse(last.finished_at), runId: last.id, surface: last.surface }
    : null;
  const initialError = last && last.status === "failed" ? { detail: last.error ?? "the run failed", at: Date.parse(last.finished_at) } : null;

  return (
    <MerchantRunProvider tenantId={id} tenantName={name} initial={initial} initialError={initialError} missing={missingFor(rails)}>
      <PageHeader
        eyebrow={
          <nav aria-label="Breadcrumb">
            <ol className="flex items-center gap-1.5">
              <li><Link href="/merchants" className="hover:text-ink hover:underline">Merchants</Link></li>
              <li aria-hidden>/</li>
              <li aria-current="page" className="truncate text-ink">{name}</li>
            </ol>
          </nav>
        }
        title={name}
        description={
          <div className="space-y-2">
            <div className="flex items-center gap-1">
              <code className="font-mono text-xs">{id}</code>
              <CopyButton value={id} label="Copy merchant id" />
            </div>
            <RailChips ready={rails.ready} partial={rails.partial} />
            {usage && !usage.plan.unmetered && (
              <p className="text-xs text-ink-soft">
                A run is one request:{" "}
                <Link href="/settings?tab=plan" className="underline-offset-4 hover:text-ink hover:underline">
                  {usage.plan.used.toLocaleString("en-US")} of {usage.plan.limit.toLocaleString("en-US")} used this month
                </Link>
              </p>
            )}
          </div>
        }
        actions={<Suspense><RunButton /></Suspense>}
      />
      <PlanStrip usage={usage} spend={`Run checks ${name} once: one request.`} />
      <Suspense><MerchantTabs rails={rails.ready.length} /></Suspense>
      <div className="pt-6">{children}</div>
    </MerchantRunProvider>
  );
}
