import Link from "next/link";
import { Suspense } from "react";
import { tenantTree } from "@/lib/api";
import { OUTCOME_LABEL, OUTCOME_MARK, OUTCOME_TONE, sentence } from "@/lib/finding";
import { requireTenant } from "@/lib/guard";
import { formatMinor } from "@/lib/money";
import { attentionRows, digest, eligible, type MerchantRun, runAll, twiceTotals } from "@/lib/runs";
import { ReportRuns } from "@/lib/tab-results";
import { RunStatus, twiceAmount } from "../_components/merchant-status";
import { RailChips } from "../_components/rail-chips";
import { SetupChecklist } from "../_components/setup-checklist";
import { Badge } from "../_components/ui/badge";
import { ButtonLink } from "../_components/ui/button";
import { EmptyState } from "../_components/ui/empty-state";
import { PageHeader, Section } from "../_components/ui/page-header";
import { Skeleton, SkeletonRows } from "../_components/ui/skeleton";
import { StatTile } from "../_components/ui/stat-tile";
import { Table, Td, Th } from "../_components/ui/table";

/** Rows shown before "View all". */
const ATTENTION_LIMIT = 8;

export default async function OverviewPage() {
  await requireTenant();
  return (
    <>
      <PageHeader
        title="Overview"
        description="Every merchant with two rails connected is checked when this page loads. Results aren't saved."
        actions={<ButtonLink href="/merchants?add=1" variant="secondary">Add merchant</ButtonLink>}
      />
      <Suspense fallback={<OverviewSkeleton />}>
        <OverviewBody />
      </Suspense>
    </>
  );
}

async function OverviewBody() {
  const [runs, tree] = await Promise.all([runAll(), tenantTree()]);
  if (!runs || !tree) {
    return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  }
  const rows = attentionRows(runs);
  const twice = twiceTotals(runs);
  const checked = runs.filter((r) => r.state === "ok");
  const firstEligible = runs.find((r) => eligible(r.merchant));
  const firstMerchant = runs[0]?.merchant.tenant.id;

  return (
    <>
      <ReportRuns digests={runs.map(digest).filter((d) => d !== null)} />

      <div className="grid gap-3 md:grid-cols-[1.4fr_1fr_1fr]">
        <StatTile
          hero
          label="Collected twice"
          value={heroValue(twice, checked.length)}
          sub={
            checked.length === 0
              ? "No merchant has been checked yet"
              : twice.orders === 0
                ? `No order taken twice across ${plural(checked.length, "merchant")}`
                : `${plural(twice.orders, "order")} · ${plural(twice.merchants, "merchant")} · owed back to customers`
          }
          action={twice.orders > 0 ? { href: "/findings", label: "Review" } : undefined}
        />
        <StatTile label="Orders checked" value={ordersChecked(runs).toLocaleString("en-US")} sub={matchRate(runs)} />
        <StatTile
          label="Merchants · rails"
          value={`${runs.length} · ${runs.reduce((n, r) => n + r.merchant.ready.length, 0)}`}
          sub={`${plural(checked.length, "merchant")} checked${failedNote(runs)}`}
        />
      </div>

      <Section
        title="Needs attention"
        aside={rows.length > ATTENTION_LIMIT ? <Link href="/findings" className="underline underline-offset-4 hover:text-ink">View all {rows.length}</Link> : null}
      >
        {rows.length === 0 ? (
          <EmptyState title={checked.length ? "Nothing needs attention" : "Nothing checked yet"}>
            {checked.length
              ? "Every order checked was paid exactly once."
              : "Connect a ledger and at least one payment rail on a merchant, and its orders are checked here."}
          </EmptyState>
        ) : (
          <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
            {rows.slice(0, ATTENTION_LIMIT).map((r, i) => (
              <li key={`${r.merchantId}-${r.order}-${i}`}>
                <Link
                  href={`/merchants/${encodeURIComponent(r.merchantId)}`}
                  className="flex min-h-11 flex-col gap-1 px-4 py-3 hover:bg-canvas/60 sm:grid sm:grid-cols-[150px_1fr_150px_110px] sm:items-center sm:gap-3"
                >
                  <span className="flex items-center justify-between gap-3">
                    <Badge tone={OUTCOME_TONE[r.outcome]} symbol={OUTCOME_MARK[r.outcome]}>{OUTCOME_LABEL[r.outcome]}</Badge>
                    <span className="text-sm font-medium tabular-nums sm:hidden">{formatMinor(r.total_minor, r.currency)}</span>
                  </span>
                  <span className="text-sm">{sentence(r)}</span>
                  <span className="truncate text-xs text-ink-soft sm:text-sm">{r.merchantName}</span>
                  <span className="hidden text-right text-sm font-medium tabular-nums sm:block">{formatMinor(r.total_minor, r.currency)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <SetupChecklist
        steps={[
          { id: "merchant", label: "Add a merchant", href: "/merchants?add=1", done: runs.length > 0 },
          {
            id: "rails",
            label: "Connect a ledger and a payment rail",
            href: firstMerchant ? `/merchants/${encodeURIComponent(firstMerchant)}?tab=connections` : "/merchants",
            done: runs.some((r) => eligible(r.merchant)),
          },
          {
            id: "run",
            label: "Run a reconciliation",
            href: firstEligible ? `/merchants/${encodeURIComponent(firstEligible.merchant.tenant.id)}` : "/merchants",
            done: checked.length > 0,
          },
          {
            id: "key",
            label: "Issue an API key for a merchant",
            href: firstMerchant ? `/merchants/${encodeURIComponent(firstMerchant)}?tab=keys` : "/key",
            done: null,
          },
          { id: "mcp", label: "Connect an agent over MCP", href: "/mcp", done: null },
        ]}
      />

      <Section title="Merchants" aside={<Link href="/merchants" className="underline underline-offset-4 hover:text-ink">All merchants</Link>}>
        {runs.length === 0 ? (
          <EmptyState
            title="No merchants yet"
            action={<ButtonLink href="/merchants?add=1" variant="primary">Add merchant</ButtonLink>}
          >
            Add one for each business you serve. Its rails and keys are kept under that merchant.
          </EmptyState>
        ) : (
          <MerchantTable runs={runs} />
        )}
      </Section>
    </>
  );
}

function MerchantTable({ runs }: { runs: MerchantRun[] }) {
  return (
    <Table label="Merchants" minWidth={680}>
      <thead>
        <tr>
          <Th>Merchant</Th>
          <Th>Rails</Th>
          <Th>Status</Th>
          <Th className="text-right">Collected twice</Th>
        </tr>
      </thead>
      <tbody className="divide-y divide-line">
        {runs.map((r) => {
          const m = r.merchant;
          const d = digest(r);
          return (
            <tr key={m.tenant.id} className="hover:bg-canvas/60">
              <Td>
                <Link href={`/merchants/${encodeURIComponent(m.tenant.id)}`} className="font-medium underline-offset-4 hover:underline">
                  {m.tenant.name}
                </Link>
                <code className="block font-mono text-xs text-ink-soft">{m.tenant.id}</code>
              </Td>
              <Td><RailChips ready={m.ready} partial={m.partial} known={m.known} /></Td>
              <Td>
                <RunStatus m={m} d={d} />
                {r.state === "failed" && (
                  <p className="mt-1 max-w-[260px] text-xs break-words text-danger">
                    {r.status ? `${r.status} · ` : ""}{r.detail}
                  </p>
                )}
              </Td>
              <Td className="text-right font-medium tabular-nums">{twiceAmount(d)}</Td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}

function heroValue(t: ReturnType<typeof twiceTotals>, checked: number): string {
  if (checked === 0) return "—";
  const amounts = [...t.byCurrency].map(([cur, minor]) => formatMinor(minor, cur));
  if (t.orders === 0) return "None";
  if (t.mixed || amounts.length > 2) return plural(t.orders, "order");
  return amounts.join(" + ");
}

function ordersChecked(runs: MerchantRun[]): number {
  return runs.reduce((n, r) => n + (r.state === "ok" ? r.finding.summary.orders : 0), 0);
}

/** Paid exactly once over orders, as the fold defines it, and withheld on
 * the same terms: if any merchant's rate is withheld, so is the total. */
function matchRate(runs: MerchantRun[]): string {
  const ok = runs.flatMap((r) => (r.state === "ok" ? [r.finding.summary] : []));
  if (!ok.length) return "Match rate appears after a check";
  if (ok.some((s) => s.match_rate === null)) return "Match rate withheld · some orders couldn't be checked";
  const orders = ok.reduce((n, s) => n + s.orders, 0);
  if (!orders) return "No orders read";
  const paid = ok.reduce((n, s) => n + s.collected + s.split_tender, 0);
  return `${((paid / orders) * 100).toFixed(1)}% match rate`;
}

function failedNote(runs: MerchantRun[]): string {
  const n = runs.filter((r) => r.state === "failed").length;
  return n ? ` · ${n} couldn't run` : "";
}

function plural(n: number, word: string): string {
  return `${n.toLocaleString("en-US")} ${word}${n === 1 ? "" : "s"}`;
}

function OverviewSkeleton() {
  return (
    <div role="status" aria-label="Checking merchants">
      <div className="grid gap-3 md:grid-cols-[1.4fr_1fr_1fr]">
        {[0, 1, 2].map((i) => (
          <div key={i} className="rounded-xl border border-line bg-surface px-5 py-4">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="mt-3 h-8 w-32" />
            <Skeleton className="mt-3 h-3 w-40" />
          </div>
        ))}
      </div>
      <p className="mt-4 text-xs text-ink-soft">Reading each merchant&apos;s ledger and rails…</p>
      <div className="mt-8"><SkeletonRows rows={4} cols={4} label="Loading findings" /></div>
      <div className="mt-8"><SkeletonRows rows={3} cols={4} label="Loading merchants" /></div>
    </div>
  );
}
