import Link from "next/link";
import { Suspense } from "react";
import { requireTenant } from "@/lib/guard";
import { attentionRows, caveats, checkedAgo, digest, oldestAt, runAll, seenFindings } from "@/lib/runs";
import { ReportRuns } from "@/lib/tab-results";
import { FindingsTable } from "../_components/findings-table";
import { ButtonLink } from "../_components/ui/button";
import { EmptyState } from "../_components/ui/empty-state";
import { PageHeader } from "../_components/ui/page-header";
import { Skeleton, SkeletonRows } from "../_components/ui/skeleton";

export const metadata = { title: "Findings" };

export default async function FindingsPage() {
  await requireTenant();
  return (
    <>
      <PageHeader
        title="Findings"
        description="Every order collected twice, short or unpaid, across every merchant ready for a check. Filter, then export the worksheet for refunds. Checked when this page loads and reused for up to 10 minutes; not saved."
      />
      <Suspense fallback={<FindingsSkeleton />}>
        <FindingsBody />
      </Suspense>
    </>
  );
}

async function FindingsBody() {
  const runs = await runAll();
  if (!runs) return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  const rows = attentionRows(runs);
  const checked = runs.filter((r) => r.state === "ok");
  const failed = runs.flatMap((r) => (r.state === "failed" ? [r] : []));
  const skipped = runs.filter((r) => r.state === "skipped").length;

  return (
    <>
      <ReportRuns digests={runs.map(digest).filter((d) => d !== null)} seen={seenFindings(runs)} />
      <p className="mb-4 text-sm text-ink-soft">
        {checked.length} of {runs.length} merchant{runs.length === 1 ? "" : "s"} checked
        {skipped > 0 && <> · {skipped} not ready (a check needs Shopify, PayPal and Stripe)</>}
        {checked.length > 0 && <> · {checkedAgo(oldestAt(runs))}</>}
      </p>
      {failed.length > 0 && (
        <div role="alert" className="mb-4 rounded-xl border border-danger/30 bg-danger-soft px-4 py-3 text-sm">
          <ul className="space-y-1">
            {failed.map((r) => (
              <li key={r.merchant.tenant.id}>
                <span aria-hidden className="mr-1.5 font-mono text-danger">!</span>
                <Link href={`/merchants/${encodeURIComponent(r.merchant.tenant.id)}`} className="font-medium underline underline-offset-4">
                  {r.merchant.tenant.name}
                </Link>{" "}
                couldn&apos;t run: <span className="text-ink">{r.status ? `${r.status} · ` : ""}{r.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {caveats(runs).filter((c) => !c.includes("couldn't run")).map((c) => (
        <p key={c} className="mb-3 text-sm text-ink">
          <span aria-hidden className="mr-1.5 font-mono">?</span>{c}; totals below cover what was read.
        </p>
      ))}
      {checked.length === 0 ? (
        <EmptyState
          title="Nothing checked yet"
          action={<ButtonLink href="/merchants" variant="primary">Go to merchants</ButtonLink>}
        >
          A merchant is checked once it has a ledger and at least one payment rail connected.
        </EmptyState>
      ) : (
        <FindingsTable
          rows={rows}
          merchants={checked.map((r) => ({ id: r.merchant.tenant.id, name: r.merchant.tenant.name }))}
        />
      )}
    </>
  );
}

function FindingsSkeleton() {
  return (
    <div role="status" aria-label="Checking merchants" className="space-y-4">
      <Skeleton className="h-4 w-56" />
      <Skeleton className="h-11 w-64" />
      <SkeletonRows rows={6} cols={5} label="Loading findings" />
    </div>
  );
}
