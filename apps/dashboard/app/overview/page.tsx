import Link from "next/link";
import { Suspense } from "react";
import { apiUrl } from "@/lib/api";
import { missingFor, railLabel, unconfirmedRows } from "@/lib/finding";
import { requireTenant } from "@/lib/guard";
import { formatMinor } from "@/lib/money";
import {
  attentionRows, caveats, eligible, type MerchantRun, resultsFrom, storedRuns, toMerchantRow, twiceTotals,
} from "@/lib/runs";
import { myUsage } from "@/lib/usage";
import { FindingsTable } from "../_components/findings-table";
import { MerchantList } from "../_components/merchants-table";
import { RunAll } from "../_components/run-all";
import { SetupChecklist, type Step } from "../_components/setup-checklist";
import { VerdictCard } from "../_components/verdict-card";
import { Card, CardBody } from "../_components/ui/card";
import { OutcomeSpectrum } from "../_components/ui/outcome-spectrum";
import { PageHeader, Section } from "../_components/ui/page-header";
import { Skeleton, SkeletonRows } from "../_components/ui/skeleton";
import { PlanStrip } from "../_components/usage/plan-strip";

export const metadata = { title: "Overview" };

/** Rows shown before "View all". */
const ATTENTION_LIMIT = 6;

export default async function OverviewPage() {
  await requireTenant();
  return (
    <>
      <PageHeader
        title="Overview"
        description="Your merchants' latest results, however each check was run."
        actions={<Suspense><RunAllReady /></Suspense>}
      />
      <Suspense fallback={<OverviewSkeleton />}>
        <OverviewBody />
      </Suspense>
    </>
  );
}

/** The header's Run all, over the merchants that are ready. Same cached
 *  read as the body, so no second request. */
async function RunAllReady() {
  const runs = await storedRuns();
  const ready = (runs ?? []).filter((r) => eligible(r.merchant)).map((r) => ({ id: r.merchant.tenant.id, name: r.merchant.tenant.name }));
  // Secondary always: the hero figure or the setup checklist's next step
  // carries the view's one volt element (§2).
  return <RunAll merchants={ready} variant="secondary" />;
}

async function OverviewBody() {
  const [runs, usage] = await Promise.all([storedRuns(), myUsage(30)]);
  if (!runs) {
    return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  }
  const checked = runs.filter((r) => r.state === "ok");
  // The meter confirms what the API can't list: any request on a key-only
  // channel (REST, SQL, MCP) proves a key was issued and read with. A confirmation sticks (Step.sticky) so a quiet
  // month doesn't undo it.
  const surfaces = usage?.buckets.map((b) => b.surface) ?? [];
  const keySeen = surfaces.some((s) => s.startsWith("mcp:") || s.startsWith("rest:"));
  const steps = setupSteps(runs, keySeen);
  const strip = <PlanStrip usage={usage} />;
  const merchants = runs.map(toMerchantRow);
  const needYou = merchants.filter((m) => m.state !== "ok");

  // Before the first check there is nothing to report: setup leads.
  if (checked.length === 0) {
    return (
      <>
        {strip}
        <SetupChecklist steps={steps} prominent />
        {needYou.length > 0 && (
          <Section title="Needs you">
            <MerchantList mode="needs-you" rows={merchants} />
          </Section>
        )}
      </>
    );
  }

  const rows = attentionRows(runs);

  return (
    <>
      {strip}
      <WorkspaceVerdict runs={runs} />
      <div className="mt-6"><SetupChecklist steps={steps} /></div>

      <Section
        title="Needs attention"
        aside={rows.length > 0 ? <Link href="/findings" prefetch={false} className="inline-flex min-h-11 items-center underline underline-offset-4 hover:text-ink">View all {rows.length} →</Link> : null}
      >
        {rows.length === 0 ? (
          <Card><CardBody><p className="text-sm"><span aria-hidden className="mr-1.5 font-mono text-ok">✓</span>Nothing needs attention in the latest checks.</p></CardBody></Card>
        ) : (
          <FindingsTable scope="workspace" rows={rows} limit={ATTENTION_LIMIT} toolbar={false} apiBase={apiUrl()} />
        )}
      </Section>

      <Section
        title="Needs you"
        aside={<Link href="/merchants" className="inline-flex min-h-11 items-center underline underline-offset-4 hover:text-ink">All {runs.length} merchant{runs.length === 1 ? "" : "s"} →</Link>}
      >
        {needYou.length === 0
          ? <p className="text-sm text-ink-soft">Every merchant is connected and checked.</p>
          : <MerchantList mode="needs-you" rows={merchants} />}
      </Section>
    </>
  );
}

/**
 * The page's signature: the workspace VerdictCard. Collected twice across
 * every merchant beside every order checked, split by verdict. Caveats sit
 * in the footer, so "None" never hides a merchant that couldn't run.
 */
function WorkspaceVerdict({ runs }: { runs: MerchantRun[] }) {
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
  // Integer compare per currency: everything taken twice is owed back.
  const allOwed = [...t.byCurrency].every(([cur, minor]) => t.owedByCurrency.get(cur) === minor);
  const owed = [...t.owedByCurrency].map(([cur, minor]) => formatMinor(minor, cur));
  const twice = t.orders > 0;
  const value = !twice ? (warn.length ? "None found" : "None")
    : t.mixed || amounts.length > 2 ? plural(t.orders, "order") : amounts.join(" + ");
  const rate = ok.some((s) => s.match_rate === null) ? null
    : orders ? (counts.collected + counts.split_tender) / orders : null;

  return (
    <VerdictCard
      ariaLabel="Verdict across merchants"
      twice={twice}
      compactFigure={amounts.length > 1}
      figure={<>{!twice && !warn.length && <span aria-hidden className="mr-2 font-mono text-2xl text-ok">✓</span>}{value}</>}
      sub={!twice
        ? `No order taken twice across ${plural(ok.length, "merchant")}`
        : <>
          {plural(t.orders, "order")} · {plural(t.merchants, "merchant")}
          {t.mixed || !owed.length ? null : allOwed
            ? " · all owed back to customers"
            : <> · <strong className="font-semibold">{owed.join(" + ")} owed back</strong></>}
        </>}
      details={twice ? <HowItAddsUp runs={runs} /> : undefined}
      leftFooter={twice ? (
        <Link href="/findings?outcome=collected_twice" prefetch={false} className="inline-flex min-h-11 items-center gap-1 text-sm font-medium underline underline-offset-4">
          Review the orders <span aria-hidden>→</span>
        </Link>
      ) : undefined}
      counts={<>
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
      </>}
      spectrum={
        <OutcomeSpectrum
          summary={counts}
          unconfirmed={runs.reduce((n, r) => n + (r.state === "ok" ? unconfirmedRows(r.finding) : 0), 0)}
        />
      }
      footer={<>
        <span suppressHydrationWarning>{resultsFrom(runs)}</span>
        {warn.map((w) => (
          <span key={w} className="inline-flex items-center gap-1 text-ink">
            <span aria-hidden className="font-mono">?</span>{w}
          </span>
        ))}
      </>}
    />
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

function setupSteps(runs: MerchantRun[], keySeen: boolean): Step[] {
  const closest = [...runs].map((r) => r.merchant).sort((a, b) => missingFor(a).length - missingFor(b).length)[0];
  const ready = runs.find((r) => eligible(r.merchant))?.merchant;
  const agentFor = ready ?? runs[0]?.merchant;
  const at = (id: string, tab?: string) => `/merchants/${encodeURIComponent(id)}${tab ? `?tab=${tab}` : ""}`;
  return [
    { id: "merchant", label: "Add a merchant", hint: "One for each business you serve.", href: "/merchants?add=1", done: runs.length > 0 },
    {
      id: "rails",
      label: "Connect Shopify, PayPal and Stripe",
      hint: closest && missingFor(closest).length
        ? `${closest.tenant.name} still needs ${missingFor(closest).map(railLabel).join(" and ")}.`
        : "A check reads Shopify orders, PayPal and Stripe.",
      href: closest
        ? `${at(closest.tenant.id, "connections")}${missingFor(closest).length ? `&connect=${missingFor(closest)[0]}` : ""}`
        : "/merchants",
      done: Boolean(ready),
    },
    { id: "run", label: "Run the first check", href: ready ? at(ready.tenant.id, "findings") : "/merchants", done: runs.some((r) => r.state === "ok") },
    // Keeps id "mcp" so existing okwan.setup.ticked entries survive.
    {
      id: "mcp",
      label: "Connect an agent",
      hint: "Ticks itself once a key reads over MCP or REST.",
      href: agentFor ? `/agents?merchant=${encodeURIComponent(agentFor.tenant.id)}` : "/agents",
      done: keySeen ? true : null,
      sticky: true,
    },
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
      <p className="mt-4 text-xs text-ink-soft">Reading each merchant&apos;s latest check…</p>
      <div className="mt-8"><SkeletonRows rows={4} cols={4} label="Loading findings" /></div>
      <div className="mt-8"><SkeletonRows rows={3} cols={4} label="Loading merchants" /></div>
    </div>
  );
}
