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
import { RangeSelect } from "../_components/range-select";
import { RANGES } from "@/lib/ranges";
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

const DAY = 86_400_000;

export default async function OverviewPage({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const me = await requireTenant();
  const raw = (await searchParams).days ?? "30";
  const days = (RANGES.map((r) => r.value) as readonly string[]).includes(raw) ? raw : "30";
  return (
    <>
      <PageHeader
        title="Overview"
        description="Your merchants' latest results, however each check was run."
        actions={<RangeSelect value={days} />}
      />
      <Suspense fallback={<OverviewSkeleton />}>
        <OverviewBody account={me.id} days={days} />
      </Suspense>
    </>
  );
}

async function OverviewBody({ account, days }: { account: string; days: string }) {
  const [all, usage] = await Promise.all([storedRuns(), myUsage(30)]);
  if (!all) {
    return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  }
  // The period counts each merchant's latest check if it finished inside
  // it; a merchant checked earlier is named, not silently dropped.
  const cutoff = days === "all" ? 0 : Date.now() - Number(days) * DAY;
  const stale = all.filter((r) => (r.state === "ok" || r.state === "failed") && r.at < cutoff);
  const runs = all.filter((r) => !stale.includes(r));
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

  if (checked.length === 0 && stale.length > 0) {
    return (
      <>
        {strip}
        <div className="rounded-[22px] border border-dashed border-line bg-white/70 px-6 py-10 text-center">
          <p className="text-lg font-semibold">No checks finished in this period</p>
          <p className="mt-1 text-ink-soft">
            {plural(stale.length, "merchant")} last checked earlier.{" "}
            <Link href="/overview?days=all" className="font-semibold text-accent underline underline-offset-4">Show all results</Link>
          </p>
        </div>
      </>
    );
  }

  // Before the first check there is nothing to report: setup leads.
  if (checked.length === 0) {
    return (
      <>
        {strip}
        <SetupChecklist account={account} steps={steps} prominent />
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
      <WorkspaceVerdict runs={runs} stale={stale.length} ready={all.filter((r) => eligible(r.merchant)).map((r) => ({ id: r.merchant.tenant.id, name: r.merchant.tenant.name }))} />
      <div className="mt-6 empty:hidden"><SetupChecklist account={account} steps={steps} /></div>

      <Section
        title="Needs attention"
        description={rows.every((r) => r.outcome === "collected_twice")
          ? "Orders where the same payment was collected more than once."
          : "Orders that need a look, from each merchant's latest check."}
        aside={rows.length > 0 ? (
          <Link href="/findings" prefetch={false} className="inline-flex min-h-11 items-center gap-2 text-[15px] font-semibold text-accent underline underline-offset-4 hover:text-accent-2">
            View all findings <span aria-hidden>→</span>
          </Link>
        ) : null}
      >
        {rows.length === 0 ? (
          <Card><CardBody><p className="text-[15px]"><span aria-hidden className="mr-1.5 font-mono text-ok">✓</span>Nothing needs attention in the latest checks.</p></CardBody></Card>
        ) : (
          <FindingsTable scope="workspace" rows={rows} limit={ATTENTION_LIMIT} toolbar={false} apiBase={apiUrl()} simple />
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
 * The page's signature, as two cards: what is at risk (collected twice,
 * owed back), and every order checked split by outcome. Caveats sit in the
 * footer, so "None" never hides a merchant that couldn't run.
 */
function WorkspaceVerdict({ runs, stale, ready }: {
  runs: MerchantRun[];
  stale: number;
  /** Merchants a Run all would check, for the card's footer. */
  ready: { id: string; name: string }[];
}) {
  const t = twiceTotals(runs);
  const ok = runs.flatMap((r) => (r.state === "ok" ? [r.finding.summary] : []));
  const sum = (k: "orders" | "collected" | "split_tender" | "collected_twice" | "collected_inconsistent" | "uncollected" | "unverifiable") =>
    ok.reduce((n, s) => n + s[k], 0);
  const orders = sum("orders");
  const warn = caveats(runs);
  const amounts = [...t.byCurrency].map(([cur, minor]) => formatMinor(minor, cur));
  // Integer compare per currency: everything taken twice is owed back.
  const allOwed = [...t.byCurrency].every(([cur, minor]) => t.owedByCurrency.get(cur) === minor);
  const owed = [...t.owedByCurrency].map(([cur, minor]) => formatMinor(minor, cur));
  const twice = t.orders > 0;
  const value = !twice ? "None"
    : t.mixed || amounts.length > 2 ? plural(t.orders, "order") : amounts.join(" + ");
  const paid = sum("collected") + sum("split_tender");
  const rate = ok.some((s) => s.match_rate === null) ? null : orders ? paid / orders : null;
  const unconfirmed = runs.reduce((n, r) => n + (r.state === "ok" ? unconfirmedRows(r.finding) : 0), 0);
  const outcomes = [
    { key: "paid", label: "Paid once", n: paid, dot: "bg-ok-bright", always: true },
    { key: "twice", label: "Collected twice", n: sum("collected_twice"), dot: "bg-signal", always: true },
    { key: "none", label: "Not paid", n: sum("uncollected"), dot: "bg-line-strong/40", always: true },
    { key: "inconsistent", label: "Doesn't add up", n: sum("collected_inconsistent"), dot: "bg-ink-soft", always: false },
    { key: "unverifiable", label: "Couldn't verify", n: sum("unverifiable"), dot: "bg-trace", always: false },
  ].filter((o) => o.always || o.n > 0);

  return (
    <>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <section
          aria-label="Amount at risk"
          className="relative isolate overflow-hidden rounded-[28px] bg-[linear-gradient(135deg,#26136f_0%,#3a1fa0_45%,#6d32e8_100%)] p-7 text-white shadow-[0_24px_50px_-24px_rgba(58,31,160,0.7)] sm:p-9"
        >
          <Ribbons />
          <p className="text-lg font-medium text-white/85">Amount at risk</p>
          <p className={`mt-3 font-bold tracking-[-0.03em] tabular-nums ${amounts.length > 1 ? "text-4xl" : "text-5xl sm:text-[60px]"} leading-none`}>
            {!twice && !warn.length && <span aria-hidden className="mr-3 font-mono text-3xl text-ok-bright">✓</span>}
            {value}
          </p>
          <p className="mt-4 text-[15px] text-white/80">
            {!twice
              ? `No order taken twice across ${plural(ok.length, "merchant")}`
              : <>
                {plural(t.orders, "order")} · {plural(t.merchants, "merchant")}
                {t.mixed || !owed.length ? null : allOwed
                  ? " · all owed back to customers"
                  : <> · <strong className="font-semibold text-white">{owed.join(" + ")} owed back</strong></>}
              </>}
          </p>
          <Link
            href={twice ? "/findings?outcome=collected_twice" : "/findings"}
            prefetch={false}
            className="mt-7 inline-flex min-h-12 items-center gap-2 rounded-2xl bg-white px-6 text-[15px] font-bold text-accent-deep shadow-[0_10px_24px_-12px_rgba(0,0,0,0.5)] hover:bg-accent-soft"
          >
            {twice ? "Review the orders" : "View findings"} <span aria-hidden>→</span>
          </Link>
        </section>

        <section aria-label="Orders checked" className="rounded-[28px] border border-white/80 bg-white/90 p-7 shadow-float sm:p-8">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <p className="min-w-0">
              <span className="text-4xl font-bold tracking-[-0.03em] tabular-nums">{orders.toLocaleString("en-US")}</span>
              <span className="ml-2 text-[15px] font-medium">orders checked across {plural(ok.length, "merchant")}</span>
            </p>
            {rate !== null && (
              <p className="text-2xl font-bold tabular-nums">
                {(rate * 100).toFixed(1)}%<span className="sr-only"> paid exactly once</span>
              </p>
            )}
          </div>
          {orders > 0 && (
            <div aria-hidden className="mt-5 h-3.5 overflow-hidden rounded-full bg-accent-soft">
              <div className="animate-settle h-full rounded-full bg-[linear-gradient(90deg,#16a34a,var(--color-ok-bright))]" style={{ width: `${(paid / orders) * 100}%` }} />
            </div>
          )}
          {rate === null && orders > 0 && (
            <p className="mt-2 text-xs text-ink-soft">Match rate withheld: some orders couldn&apos;t be verified.</p>
          )}
          <ul className="mt-5 space-y-3" aria-label="Orders by outcome">
            {outcomes.map((o) => (
              <li key={o.key} className="flex items-center justify-between gap-3 text-[15px]">
                <span className="inline-flex items-center gap-3">
                  <span aria-hidden className={`h-3.5 w-3.5 rounded-full ${o.dot}`} />
                  {o.label}
                </span>
                <span className="font-bold tabular-nums">{o.n.toLocaleString("en-US")}</span>
              </li>
            ))}
          </ul>
          {unconfirmed > 0 && (
            <p className="mt-3 text-xs text-ink-soft">{plural(unconfirmed, "paid-once order")} not ruled out on another rail.</p>
          )}
          <div className="mt-5 flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-t border-line pt-4 text-sm text-ink-soft">
            <div className="flex min-w-0 flex-wrap gap-x-4 gap-y-1">
              <span suppressHydrationWarning>{resultsFrom(runs)}</span>
              {stale > 0 && <span>{plural(stale, "merchant")} last checked before this period</span>}
              {warn.map((w) => (
                <span key={w} className="inline-flex items-center gap-1 text-ink">
                  <span aria-hidden className="font-mono">?</span>{w}
                </span>
              ))}
            </div>
            {ready.length > 0 && <RunAll merchants={ready} variant="secondary" />}
          </div>
        </section>
      </div>
      {twice && <HowItAddsUp runs={runs} />}
    </>
  );
}

/** The hero's decoration: two soft ribbons sweeping out of the corner. */
function Ribbons() {
  return (
    <svg aria-hidden viewBox="0 0 400 260" preserveAspectRatio="none" className="pointer-events-none absolute right-0 bottom-0 -z-10 h-[78%] w-[62%] opacity-90">
      <defs>
        <linearGradient id="okwan-ribbon-a" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0" />
          <stop offset="0.6" stopColor="#c4b5fd" stopOpacity="0.55" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="0.85" />
        </linearGradient>
        <linearGradient id="okwan-ribbon-b" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#a78bfa" stopOpacity="0" />
          <stop offset="1" stopColor="#ddd6fe" stopOpacity="0.6" />
        </linearGradient>
      </defs>
      <path d="M40 260 C 160 250, 250 200, 300 120 S 370 10, 400 0 L 400 260 Z" fill="url(#okwan-ribbon-b)" />
      <path d="M120 260 C 210 240, 280 190, 320 130 S 380 40, 400 30 L 400 60 C 370 80, 350 140, 320 180 S 240 255, 170 260 Z" fill="url(#okwan-ribbon-a)" />
    </svg>
  );
}

/** The hero's terms, merchant by merchant, and what it leaves out, so the
 *  one big number can be checked by eye. Same data as the figure. */
function HowItAddsUp({ runs }: { runs: MerchantRun[] }) {
  const terms = runs.flatMap((r) => (r.state === "ok" && r.finding.summary.collected_twice > 0 ? [r] : []));
  const left = runs.flatMap((r) => (r.state === "failed" ? [`${r.merchant.tenant.name}: couldn't run`] : []))
    .concat(terms.filter((r) => r.finding.twice_currency === null).map((r) => `${r.merchant.tenant.name}: counted as orders, not money (currencies differ or not all orders listed)`));
  return (
    <details className="mt-4 px-1 text-sm text-ink-soft">
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
