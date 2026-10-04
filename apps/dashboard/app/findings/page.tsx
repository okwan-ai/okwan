import Link from "next/link";
import { Suspense } from "react";
import { apiUrl } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { attentionRows, caveats, checkedAgo, digest, eligible, oldestAt, runAll, seenFindings } from "@/lib/runs";
import { ReportRuns } from "@/lib/tab-results";
import { myUsage } from "@/lib/usage";
import { FindingsTable } from "../_components/findings-table";
import { ButtonLink } from "../_components/ui/button";
import { EmptyState } from "../_components/ui/empty-state";
import { PlanStrip } from "../_components/usage/plan-strip";
import { IconAlert } from "../_components/ui/icons";
import { PageHeader } from "../_components/ui/page-header";
import { Skeleton, SkeletonRows } from "../_components/ui/skeleton";

export const metadata = { title: "Findings" };

export default async function FindingsPage() {
  await requireTenant();
  return (
    <>
      <PageHeader
        title="Findings"
        description="Every order collected twice, not adding up across rails, or unpaid, across every merchant ready for a check. Filter, then export the worksheet for refunds. Checked when this page loads and reused for up to 10 minutes; not saved. A single rail that took less than the order isn't flagged yet; the money trail on the merchant page shows it."
      />
      <Suspense fallback={<FindingsSkeleton />}>
        <FindingsBody />
      </Suspense>
    </>
  );
}

async function FindingsBody() {
  const [runs, usage] = await Promise.all([runAll(), myUsage(30)]);
  if (!runs) return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  const rows = attentionRows(runs);
  const checked = runs.filter((r) => r.state === "ok");
  const failed = runs.flatMap((r) => (r.state === "failed" ? [r] : []));
  const skipped = runs.filter((r) => r.state === "skipped").length;

  return (
    <>
      <ReportRuns digests={runs.map(digest).filter((d) => d !== null)} seen={seenFindings(runs)} serverNow={Date.now()} />
      <PlanStrip
        usage={usage}
        spend={`A load of this page checks each ready merchant once (${runs.filter((r) => eligible(r.merchant)).length} ready now); each check is one request.`}
      />
      <p className="mb-4 text-sm text-ink-soft">
        {checked.length} of {runs.length} merchant{runs.length === 1 ? "" : "s"} checked
        {skipped > 0 && <> · {skipped} not ready (a check needs Shopify, PayPal and Stripe)</>}
        {checked.length > 0 && <> · {checkedAgo(oldestAt(runs))}</>}
        {usage && !usage.plan.unmetered && (
          <> · <Link href="/settings?tab=plan" className="underline-offset-4 hover:text-ink hover:underline">{usage.plan.used.toLocaleString("en-US")} of {usage.plan.limit.toLocaleString("en-US")} requests this month</Link></>
        )}
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
          icon={<IconAlert />}
          title="Nothing checked yet"
          benefits={[
            "Every order collected twice, with the amount owed back",
            "Orders where the rails don't add up to the order total",
            "Orders with no payment on any rail, ready to export for refunds",
          ]}
          action={<ButtonLink href="/merchants" variant="primary">Go to merchants</ButtonLink>}
        >
          A merchant is checked once Shopify, PayPal and Stripe are all connected.
        </EmptyState>
      ) : (
        <FindingsTable
          apiBase={apiUrl()}
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
