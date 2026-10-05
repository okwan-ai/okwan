import Link from "next/link";
import { Suspense } from "react";
import { apiUrl } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { ago } from "@/lib/finding";
import { attentionRows, caveats, eligible, oldestAt, storedRuns } from "@/lib/runs";
import { myUsage } from "@/lib/usage";
import { FindingsTable } from "../_components/findings-table";
import { RunAll } from "../_components/run-all";
import { ButtonLink } from "../_components/ui/button";
import { EmptyState } from "../_components/ui/empty-state";
import { PlanStrip } from "../_components/usage/plan-strip";
import { HowItWorks } from "../_components/ui/how-it-works";
import { IconAlert } from "../_components/ui/icons";
import { PageHeader } from "../_components/ui/page-header";
import { Skeleton, SkeletonRows } from "../_components/ui/skeleton";

export const metadata = { title: "Findings" };

const TITLE = "Findings";
const DESCRIPTION = "Every order that needs a look, from each merchant's latest check.";

export default async function FindingsPage() {
  await requireTenant();
  return (
    <Suspense fallback={<><PageHeader title={TITLE} description={DESCRIPTION} /><FindingsSkeleton /></>}>
      <FindingsBody />
    </Suspense>
  );
}

async function FindingsBody() {
  const [runs, usage] = await Promise.all([storedRuns(), myUsage(30)]);
  if (!runs) {
    return (
      <>
        <PageHeader title={TITLE} description={DESCRIPTION} />
        <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>
      </>
    );
  }
  const rows = attentionRows(runs);
  const checked = runs.filter((r) => r.state === "ok");
  const failed = runs.flatMap((r) => (r.state === "failed" ? [r] : []));
  const skipped = runs.filter((r) => r.state === "skipped").length;
  const ready = runs.filter((r) => eligible(r.merchant)).map((r) => ({ id: r.merchant.tenant.id, name: r.merchant.tenant.name }));

  return (
    <>
      <PageHeader
        title={TITLE}
        description={DESCRIPTION}
        meta={
          <>
            <span>
              {checked.length} of {runs.length} merchant{runs.length === 1 ? "" : "s"} checked
              {checked.length > 0 && <> · <span suppressHydrationWarning>results from {ago(oldestAt(runs))}</span></>}
              {skipped > 0 && <> · <Link href="/merchants" className="underline-offset-4 hover:text-ink hover:underline">{skipped} not ready →</Link></>}
            </span>
            <HowItWorks
              items={[
                "Opening this page runs nothing. Run all checks runs each ready merchant once and saves the result.",
                "A single payment that took less than the order isn't flagged yet; the order's money trail shows it.",
              ]}
            />
          </>
        }
        // The EmptyState carries its own Run all before any check succeeds.
        actions={checked.length > 0 ? <RunAll merchants={ready} variant="secondary" /> : undefined}
      />
      <PlanStrip usage={usage} />
      {failed.length > 0 && (
        <div role="alert" className="mb-4 rounded-xl border border-danger/30 bg-danger-soft px-4 py-3 text-sm">
          <ul className="space-y-1">
            {failed.map((r) => (
              <li key={r.merchant.tenant.id}>
                <span aria-hidden className="mr-1.5 font-mono text-danger">!</span>
                <span className="font-medium">{r.merchant.tenant.name}</span> couldn&apos;t run:{" "}
                <span className="text-ink">{r.status ? `${r.status} · ` : ""}{r.detail}</span>{" · "}
                <Link href={`/merchants/${encodeURIComponent(r.merchant.tenant.id)}?tab=connections`} className="font-medium underline underline-offset-4">
                  Check connections →
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
      {caveats(runs).filter((c) => !c.includes("couldn't run")).map((c) => (
        <p key={c} className="mb-3 text-sm text-ink">
          <span aria-hidden className="mr-1.5 font-mono">?</span>{c}; totals cover what was read.
        </p>
      ))}
      {checked.length === 0 ? (
        <EmptyState
          icon={<IconAlert />}
          title="No check yet"
          benefits={[
            "Every order collected twice, with the amount owed back",
            "Orders where the rails don't add up to the order total",
            "Orders with no payment on any rail, ready to export for refunds",
          ]}
          action={ready.length
            ? <RunAll merchants={ready} />
            : <ButtonLink href="/merchants" variant="primary">Go to merchants</ButtonLink>}
        >
          A merchant can be checked once Shopify, PayPal and Stripe are connected. Every check is saved and shown here.
        </EmptyState>
      ) : (
        <FindingsTable
          scope="workspace"
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
