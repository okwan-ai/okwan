import Link from "next/link";
import { Suspense } from "react";
import { apiUrl } from "@/lib/api";
import { FOLD_READS, missingFor, railLabel, unconfirmedRows } from "@/lib/finding";
import { requireTenant } from "@/lib/guard";
import { formatMinor } from "@/lib/money";
import {
  attentionRows, caveats, checkedAgo, digest, eligible, type MerchantRun, oldestAt, runAll, seenFindings, twiceTotals,
} from "@/lib/runs";
import { ReportRuns } from "@/lib/tab-results";
import { myUsage } from "@/lib/usage";
import { today, type Usage } from "@/lib/usage-shape";
import { AttentionList } from "../_components/attention-list";
import { owedAmount, RunStatus } from "../_components/merchant-status";
import { RailChips } from "../_components/rail-chips";
import { SetupChecklist, type Step } from "../_components/setup-checklist";
import { ButtonLink } from "../_components/ui/button";
import { EmptyState } from "../_components/ui/empty-state";
import { OutcomeSpectrum } from "../_components/ui/outcome-spectrum";
import { PageHeader, Section } from "../_components/ui/page-header";
import { Skeleton, SkeletonRows } from "../_components/ui/skeleton";
import { Table, Td, Th } from "../_components/ui/table";

export const metadata = { title: "Overview" };

/** Rows shown before "View all". */
const ATTENTION_LIMIT = 6;

export default async function OverviewPage() {
  await requireTenant();
  return (
    <>
      <PageHeader
        title="Overview"
        description="Each merchant ready for a check is checked when this page loads, and the result is reused for up to 10 minutes. Each check counts as one request against your plan; results aren't saved."
        actions={<ButtonLink href="/merchants?add=1" variant="secondary">Add merchant</ButtonLink>}
      />
      <Suspense fallback={<OverviewSkeleton />}>
        <OverviewBody />
      </Suspense>
    </>
  );
}

async function OverviewBody() {
  const [runs, usage] = await Promise.all([runAll(), myUsage(30)]);
  if (!runs) {
    return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  }
  const checked = runs.filter((r) => r.state === "ok");
  const steps = setupSteps(runs);

  // Before the first check there is nothing to report: setup leads.
  if (checked.length === 0) {
    const failed = runs.filter((r) => r.state === "failed");
    return (
      <>
        <ReportRuns digests={runs.map(digest).filter((d) => d !== null)} seen={seenFindings(runs)} serverNow={Date.now()} />
        <SetupChecklist steps={steps} prominent />
        {failed.length > 0 && <FailedNote runs={failed} />}
        {runs.length > 0 && (
          <Section title="Merchants">
            <MerchantTable runs={runs} />
          </Section>
        )}
      </>
    );
  }

  const rows = attentionRows(runs);

  return (
    <>
      <ReportRuns digests={runs.map(digest).filter((d) => d !== null)} seen={seenFindings(runs)} serverNow={Date.now()} />
      <VerdictStrip runs={runs} usage={usage} />

      <Section
        title="Needs attention"
        aside={rows.length > ATTENTION_LIMIT ? <Link href="/findings" prefetch={false} className="underline underline-offset-4 hover:text-ink">View all {rows.length}</Link> : null}
      >
        {rows.length === 0 ? (
          <EmptyState title="Nothing needs attention">
            No order was found collected twice, not adding up across rails, or unpaid in what was read.
          </EmptyState>
        ) : (
          <AttentionList rows={rows.slice(0, ATTENTION_LIMIT)} apiBase={apiUrl()} />
        )}
      </Section>

      <Section
        title="Merchants"
        aside={<>{coverageLine(runs)} · <Link href="/merchants" className="underline underline-offset-4 hover:text-ink">All merchants</Link></>}
      >
        <MerchantTable runs={runs} />
      </Section>

      <SetupChecklist steps={steps} />
    </>
  );
}

/**
 * The page's signature: the one volt figure (collected twice across every
 * merchant) beside every order checked, split by verdict. Caveats sit next
 * to the figure, so "None" never hides a merchant that couldn't run.
 */
function VerdictStrip({ runs, usage }: { runs: MerchantRun[]; usage: Usage | null }) {
  const t = twiceTotals(runs);
  const ok = runs.flatMap((r) => (r.state === "ok" ? [r.finding.summary] : []));
  const sum = (k: "orders" | "collected" | "split_tender" | "collected_twice" | "collected_inconsistent" | "uncollected" | "unverifiable") =>
    ok.reduce((n, s) => n + s[k], 0);
  const counts = {
    collected: sum("collected"),
    split_tender: sum("split_tender"),
    collected_twice: sum("collected_twice"),
    collected_inconsistent: sum("collected_inconsistent"),
    uncollected: sum("uncollected"),
    unverifiable: sum("unverifiable"),
  };
  const orders = sum("orders");
  const warn = caveats(runs);
  const amounts = [...t.byCurrency].map(([cur, minor]) => formatMinor(minor, cur));
  const owed = [...t.owedByCurrency].map(([cur, minor]) => formatMinor(minor, cur));
  const value = t.orders === 0 ? (warn.length ? "None found" : "None")
    : t.mixed || amounts.length > 2 ? plural(t.orders, "order") : amounts.join(" + ");
  const rate = ok.some((s) => s.match_rate === null) ? null
    : orders ? (counts.collected + counts.split_tender) / orders : null;

  return (
    <section aria-label="Verdict across merchants" className="grid overflow-hidden rounded-xl border border-line bg-surface md:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      {/* Volt only when there is something to act on: a clean "None" is not
          the page's hero figure, and volt on it would cry wolf. */}
      <div className={`flex flex-col px-5 py-5 text-ink ${t.orders > 0 ? "bg-volt" : "border-b border-line md:border-r md:border-b-0"}`}>
        <p className="text-xs font-medium">Collected twice</p>
        <p className={`mt-1 font-semibold tracking-tight tabular-nums ${amounts.length > 1 ? "text-3xl" : "text-4xl sm:text-5xl"}`}>
          {t.orders === 0 && !warn.length && <span aria-hidden className="mr-2 font-mono text-2xl text-ok">✓</span>}
          {value}
        </p>
        <p className="mt-2 text-sm">
          {t.orders === 0
            ? `No order taken twice across ${plural(ok.length, "merchant")}`
            : <>
              {plural(t.orders, "order")} · {plural(t.merchants, "merchant")}
              {owed.length && !t.mixed ? <> · <strong className="font-semibold">{owed.join(" + ")} owed back</strong></> : null}
            </>}
        </p>
        {t.orders > 0 && <HowItAddsUp runs={runs} />}
        {t.orders > 0 && (
          <Link href="/findings?outcome=collected_twice" prefetch={false} className="mt-auto inline-flex min-h-11 items-center gap-1 self-start pt-3 text-sm font-medium underline underline-offset-4">
            Review the orders <span aria-hidden>→</span>
          </Link>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-4 px-5 py-5">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <p>
            <span className="text-2xl font-semibold tabular-nums">{orders.toLocaleString("en-US")}</span>
            <span className="ml-1.5 text-sm text-ink-soft">orders checked across {plural(ok.length, "merchant")}</span>
          </p>
          <p className="text-sm text-ink-soft">
            {orders === 0
              ? "No orders in what was read"
              : rate === null
              ? "Match rate withheld: some orders couldn't be verified"
              : <><span className="font-semibold text-ink tabular-nums">{(rate * 100).toFixed(1)}%</span> paid exactly once</>}
          </p>
        </div>
        <OutcomeSpectrum
          summary={counts}
          unconfirmed={runs.reduce((n, r) => n + (r.state === "ok" ? unconfirmedRows(r.finding) : 0), 0)}
        />
        <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-soft">
          <span>{checkedAgo(oldestAt(runs))}</span>
          {usage && (
            <Link href="/settings?tab=plan" className="underline-offset-4 hover:text-ink hover:underline">
              {usage.plan.used.toLocaleString("en-US")}{usage.plan.unmetered ? "" : ` of ${usage.plan.limit.toLocaleString("en-US")}`} requests this month · {today(usage).toLocaleString("en-US")} today
            </Link>
          )}
          {warn.map((w) => (
            <span key={w} className="inline-flex items-center gap-1 text-ink">
              <span aria-hidden className="font-mono">?</span>{w}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

/** The hero's terms, merchant by merchant, and what it leaves out, so the
 *  one big number can be checked by eye. Same data as the figure. */
function HowItAddsUp({ runs }: { runs: MerchantRun[] }) {
  const terms = runs.flatMap((r) => (r.state === "ok" && r.finding.summary.collected_twice > 0 ? [r] : []));
  const left = runs.flatMap((r) => (r.state === "failed" ? [`${r.merchant.tenant.name}: couldn't run`] : []))
    .concat(terms.filter((r) => r.finding.twice_currency === null).map((r) => `${r.merchant.tenant.name}: counted as orders, not money (currencies differ or not all orders listed)`));
  return (
    <details className="mt-3 text-sm">
      <summary className="inline-flex min-h-11 cursor-pointer items-center font-medium underline-offset-4 hover:underline">How this adds up</summary>
      <ul className="mt-1 space-y-1">
        {terms.map((r) => {
          const s = r.finding.summary;
          const cur = r.finding.twice_currency;
          return (
            <li key={r.merchant.tenant.id} className="flex flex-wrap justify-between gap-x-4 tabular-nums">
              <span>{r.merchant.tenant.name} · {s.collected_twice} order{s.collected_twice === 1 ? "" : "s"}</span>
              <span>{cur ? <>{formatMinor(s.collected_twice_minor, cur)} · {formatMinor(s.overcollected_minor, cur)} owed back</> : "—"}</span>
            </li>
          );
        })}
      </ul>
      {left.length > 0 && <p className="mt-2 text-xs">Not in the figure: {left.join("; ")}.</p>}
    </details>
  );
}

/** Findings first (most owed back), then failures, then readiness. */
function rank(r: MerchantRun): number {
  if (r.state === "ok") return r.finding.summary.collected_twice ? 0 : r.finding.summary.collected_inconsistent + r.finding.summary.uncollected ? 1 : 3;
  if (r.state === "failed") return 2;
  return eligible(r.merchant) ? 4 : 5;
}

function MerchantTable({ runs }: { runs: MerchantRun[] }) {
  // Amounts compare only within one currency; a merchant without a single
  // owed-back currency sorts after the priced ones in its rank.
  const cur = (r: MerchantRun) => (r.state === "ok" ? r.finding.twice_currency ?? "~" : "~");
  const owed = (r: MerchantRun) => (r.state === "ok" ? r.finding.summary.overcollected_minor : 0);
  const sorted = [...runs].sort((a, b) => rank(a) - rank(b)
    || cur(a).localeCompare(cur(b))
    || owed(b) - owed(a)
    || a.merchant.tenant.name.localeCompare(b.merchant.tenant.name));
  return (
    <>
      <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface sm:hidden">
        {sorted.map((r) => {
          const m = r.merchant;
          const d = digest(r);
          return (
            <li key={m.tenant.id}>
              <Link href={`/merchants/${encodeURIComponent(m.tenant.id)}`} className="block px-4 py-3">
                <span className="flex items-center justify-between gap-3">
                  <span className="font-medium">{m.tenant.name}</span>
                  <span className="text-sm font-semibold tabular-nums">{owedAmount(d)}</span>
                </span>
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  <RunStatus m={m} d={d} />
                  <RailChips ready={m.ready} partial={m.partial} known={m.known} />
                </div>
                {r.state === "failed" && <span className="mt-1 block text-xs break-words text-danger">{r.detail}</span>}
              </Link>
            </li>
          );
        })}
      </ul>
      <div className="hidden sm:block">
        <Table label="Merchants" minWidth={760}>
          <thead>
            <tr>
              <Th>Merchant</Th>
              {FOLD_READS.map((c) => <Th key={c}>{railLabel(c)}{c === "shopify" ? <span className="font-normal"> · ledger</span> : null}</Th>)}
              <Th>Status</Th>
              <Th className="text-right">Owed back</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {sorted.map((r) => {
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
                  {FOLD_READS.map((c) => <Td key={c}><RailCell m={m} rail={c} /></Td>)}
                  <Td>
                    <RunStatus m={m} d={d} />
                    {r.state === "failed" && (
                      <p className="mt-1 max-w-[260px] text-xs break-words text-danger">
                        {r.status ? `${r.status} · ` : ""}{r.detail}
                      </p>
                    )}
                  </Td>
                  <Td className="text-right font-medium tabular-nums">{owedAmount(d)}</Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </div>
    </>
  );
}

/** One rail of the check, for one merchant: a glyph and a word, never
 *  colour alone. */
function RailCell({ m, rail }: { m: MerchantRun["merchant"]; rail: string }) {
  if (!m.known) return <span className="text-xs text-ink-soft">Unknown</span>;
  if (m.ready.includes(rail)) {
    return <span className="inline-flex items-center gap-1.5 text-sm"><span aria-hidden className="text-ok">●</span>Connected</span>;
  }
  if (m.partial.includes(rail)) {
    return <span className="inline-flex items-center gap-1.5 text-sm"><span aria-hidden>◐</span>Partial</span>;
  }
  return <span className="inline-flex items-center gap-1.5 text-sm text-ink-soft"><span aria-hidden>○</span>Missing</span>;
}

/** "3 of 5 merchants fully connected · 2 need PayPal" */
function coverageLine(runs: MerchantRun[]): string {
  const full = runs.filter((r) => eligible(r.merchant)).length;
  const need = new Map<string, number>();
  for (const r of runs) for (const c of r.merchant.known ? missingFor(r.merchant) : []) need.set(c, (need.get(c) ?? 0) + 1);
  const top = [...need].sort((a, b) => b[1] - a[1])[0];
  return `${full} of ${runs.length} merchant${runs.length === 1 ? "" : "s"} fully connected${top ? ` · ${top[1]} need${top[1] === 1 ? "s" : ""} ${railLabel(top[0])}` : ""}`;
}

function FailedNote({ runs }: { runs: MerchantRun[] }) {
  return (
    <ul role="alert" className="mt-6 space-y-1 rounded-xl border border-danger/30 bg-danger-soft px-4 py-3 text-sm">
      {runs.map((r) => r.state === "failed" && (
        <li key={r.merchant.tenant.id}>
          <span aria-hidden className="mr-1.5 font-mono text-danger">!</span>
          <Link href={`/merchants/${encodeURIComponent(r.merchant.tenant.id)}`} className="font-medium underline underline-offset-4">
            {r.merchant.tenant.name}
          </Link>{" "}
          couldn&apos;t run: {r.status ? `${r.status} · ` : ""}{r.detail}
        </li>
      ))}
    </ul>
  );
}

function setupSteps(runs: MerchantRun[]): Step[] {
  const first = runs[0]?.merchant;
  const closest = [...runs].map((r) => r.merchant).sort((a, b) => missingFor(a).length - missingFor(b).length)[0];
  const ready = runs.find((r) => eligible(r.merchant))?.merchant;
  const at = (id: string, tab?: string) => `/merchants/${encodeURIComponent(id)}${tab ? `?tab=${tab}` : ""}`;
  return [
    { id: "merchant", label: "Add a merchant", hint: "One for each business you serve.", href: "/merchants?add=1", done: runs.length > 0 },
    {
      id: "rails",
      label: "Connect Shopify, PayPal and Stripe",
      hint: closest && missingFor(closest).length
        ? `${closest.tenant.name} still needs ${missingFor(closest).map(railLabel).join(" and ")}.`
        : "A check reads the order ledger and both payment rails.",
      href: closest
        ? `${at(closest.tenant.id, "connections")}${missingFor(closest).length ? `&connect=${missingFor(closest)[0]}` : ""}`
        : "/merchants",
      done: Boolean(ready),
    },
    { id: "run", label: "Run the first check", href: ready ? at(ready.tenant.id) : "/merchants", done: runs.some((r) => r.state === "ok") },
    { id: "key", label: "Issue an API key for a merchant", href: first ? at(first.tenant.id, "keys") : "/key", done: null },
    { id: "mcp", label: "Connect an agent over MCP", href: "/mcp", done: null },
  ];
}

function plural(n: number, word: string): string {
  return `${n.toLocaleString("en-US")} ${word}${n === 1 ? "" : "s"}`;
}

function OverviewSkeleton() {
  return (
    <div role="status" aria-label="Checking merchants">
      <div className="grid overflow-hidden rounded-xl border border-line bg-surface md:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="px-6 py-5">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="mt-3 h-10 w-44" />
          <Skeleton className="mt-3 h-3 w-56" />
        </div>
        <div className="px-6 py-5">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="mt-5 h-3 w-full" />
          <Skeleton className="mt-3 h-3 w-3/4" />
        </div>
      </div>
      <p className="mt-4 text-xs text-ink-soft">Reading each merchant&apos;s ledger and rails…</p>
      <div className="mt-8"><SkeletonRows rows={4} cols={4} label="Loading findings" /></div>
      <div className="mt-8"><SkeletonRows rows={3} cols={4} label="Loading merchants" /></div>
    </div>
  );
}
