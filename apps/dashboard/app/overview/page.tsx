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
import { SetupChecklist, type Step } from "../_components/setup-checklist";
import { VerdictCard } from "../_components/verdict-card";
import { Card, CardBody } from "../_components/ui/card";
import { OutcomeSpectrum } from "../_components/ui/outcome-spectrum";
import { IconArrowRight, IconClock } from "../_components/ui/icons";
import { PageHeader, Section } from "../_components/ui/page-header";
import { Skeleton, SkeletonRows } from "../_components/ui/skeleton";
import { PlanStrip } from "../_components/usage/plan-strip";

export const metadata = { title: "Overview" };

/** Rows shown before "View all". */
const ATTENTION_LIMIT = 6;

const DAY = 86_400_000;

export default async function OverviewPage({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const me = await requireTenant();
  // The last seven days unless the URL says otherwise, as drawn.
  const raw = (await searchParams).days ?? "7";
  const days = (RANGES.map((r) => r.value) as readonly string[]).includes(raw) ? raw : "7";
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
      <WorkspaceVerdict runs={runs} stale={stale.length} />

      <Section
        className="mt-12 2xl:mt-[52px]"
        title="Needs attention"
        description={rows.every((r) => r.outcome === "collected_twice")
          ? "Orders where the same payment was collected more than once."
          : "Orders that need a look, from each merchant's latest check."}
        aside={rows.length > 0 ? (
          <Link href="/findings" prefetch={false} className="group inline-flex min-h-11 items-center gap-3 text-[16px] font-semibold text-accent hover:text-accent-deep 2xl:text-[19px]">
            <span className="underline underline-offset-[5px]">View all findings</span> <IconArrowRight />
          </Link>
        ) : null}
      >
        {rows.length === 0 ? (
          <Card><CardBody><p className="text-[15px]"><span aria-hidden className="mr-1.5 font-mono text-ok">✓</span>Nothing needs attention in the latest checks.</p></CardBody></Card>
        ) : (
          <FindingsTable scope="workspace" rows={rows} limit={ATTENTION_LIMIT} toolbar={false} apiBase={apiUrl()} simple />
        )}
      </Section>

      {/* Setup, once a check has run, waits below the results. */}
      <div className="mt-8 empty:hidden"><SetupChecklist account={account} steps={steps} /></div>

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
function WorkspaceVerdict({ runs, stale }: { runs: MerchantRun[]; stale: number }) {
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
    { key: "none", label: "Not paid", n: sum("uncollected"), dot: "bg-[#dcdcef]", always: true },
    { key: "inconsistent", label: "Doesn't add up", n: sum("collected_inconsistent"), dot: "bg-ink-soft", always: false },
    { key: "unverifiable", label: "Couldn't verify", n: sum("unverifiable"), dot: "bg-trace", always: false },
  ].filter((o) => o.always || o.n > 0);

  return (
    <div className="grid gap-[21px] lg:grid-cols-[minmax(0,469fr)_minmax(0,404fr)]">
      <section
        aria-label="Amount at risk"
        className="relative isolate overflow-hidden rounded-[28px] bg-hero px-8 pt-9 pb-8 text-white shadow-[0_26px_50px_-24px_rgba(40,14,170,0.75)] 2xl:px-9 2xl:pt-[36px] 2xl:pb-[30px]"
      >
        <Ribbons />
        <p className="text-[18px] leading-tight font-medium text-white/90 2xl:text-[20px]">Amount at risk</p>
        <p className={`mt-4 font-extrabold tracking-[-0.01em] tabular-nums ${amounts.length > 1 ? "text-4xl" : "text-[48px] sm:text-[54px] 2xl:text-[58px]"} leading-none`}>
          {!twice && !warn.length && <span aria-hidden className="mr-3 font-mono text-3xl text-ok-bright">✓</span>}
          {value}
        </p>
        <p className="mt-5 text-[16px] text-white/85 2xl:text-[17px]">
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
          className="mt-7 inline-flex min-h-[50px] items-center gap-3.5 rounded-[16px] bg-white px-6 text-[16px] font-bold text-accent-ink shadow-[0_12px_26px_-12px_rgba(0,0,0,0.55)] hover:bg-accent-soft 2xl:text-[18px]"
        >
          {twice ? "Review the orders" : "View findings"} <IconArrowRight />
        </Link>
      </section>

      <section aria-label="Orders checked" className="rounded-[28px] border border-white/90 bg-white/90 px-[22px] pt-[26px] pb-6 shadow-float">
        <div className="flex items-baseline gap-x-3">
          <span className="text-[36px] leading-none font-extrabold tabular-nums 2xl:text-[40px]">{orders.toLocaleString("en-US")}</span>
          <span className="min-w-0 flex-1 text-[15px] leading-snug font-medium">orders checked across {plural(ok.length, "merchant")}</span>
          {rate !== null && (
            <p className="shrink-0 text-[22px] font-extrabold tabular-nums">
              {(rate * 100).toFixed(1)}%<span className="sr-only"> paid exactly once</span>
            </p>
          )}
        </div>
        {orders > 0 && (
          <div aria-hidden className="mt-5 h-[15px] overflow-hidden rounded-full bg-line">
            <div className="animate-settle h-full rounded-full bg-[linear-gradient(90deg,var(--color-ok-bright),#04c001)]" style={{ width: `${(paid / orders) * 100}%` }} />
          </div>
        )}
        {rate === null && orders > 0 && (
          <p className="mt-2 text-xs text-ink-soft">Match rate withheld: some orders couldn&apos;t be verified.</p>
        )}
        <ul className="mt-5 space-y-2" aria-label="Orders by outcome">
          {outcomes.map((o) => (
            <li key={o.key} className="flex items-center justify-between gap-3 text-[16px] 2xl:text-[17px]">
              <span className="inline-flex items-center gap-4">
                <span aria-hidden className={`h-4 w-4 rounded-full ${o.dot}`} />
                {o.label}
              </span>
              <span className="font-bold tabular-nums">{o.n.toLocaleString("en-US")}</span>
            </li>
          ))}
        </ul>
        {unconfirmed > 0 && (
          <p className="mt-3 text-xs text-ink-soft">{plural(unconfirmed, "paid-once order")} not ruled out on another rail.</p>
        )}
        <div className="mt-4 border-t border-line pt-3.5 text-[16px] text-ink-soft 2xl:text-[17px]">
          <p className="flex items-center gap-3">
            <IconClock />
            <span suppressHydrationWarning>{resultsFrom(runs)}</span>
          </p>
          {(stale > 0 || warn.length > 0) && (
            <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
              {stale > 0 && <span>{plural(stale, "merchant")} last checked before this period</span>}
              {warn.map((w) => (
                <span key={w} className="inline-flex items-center gap-1 text-ink">
                  <span aria-hidden className="font-mono">?</span>{w}
                </span>
              ))}
            </p>
          )}
        </div>
      </section>
    </div>
  );
}

/** The hero's decoration, as drawn: a glossy ribbon sweeping up out of
 *  the bottom-right corner. */
function Ribbons() {
  return (
    <svg aria-hidden viewBox="0 0 469 286" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 -z-10 h-full w-full">
      <defs>
        <linearGradient id="okwan-ribbon-a" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#7b5cff" stopOpacity="0" />
          <stop offset="0.5" stopColor="#8f6cff" stopOpacity="0.55" />
          <stop offset="1" stopColor="#c9b6ff" stopOpacity="0.7" />
        </linearGradient>
        <linearGradient id="okwan-ribbon-b" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.05" />
          <stop offset="0.55" stopColor="#efe8ff" stopOpacity="0.75" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="0.9" />
        </linearGradient>
      </defs>
      <path d="M262 286C352 270 420 214 469 128V286Z" fill="url(#okwan-ribbon-a)" />
      <path d="M306 286C384 262 438 212 469 160V196C440 238 392 272 344 286Z" fill="url(#okwan-ribbon-b)" />
      <path d="M352 286C408 268 446 234 469 204V226C448 252 418 274 388 286Z" fill="#ffffff" fillOpacity="0.28" />
    </svg>
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
