"use client";

import { useSearchParams } from "next/navigation";
import { Fragment, useEffect, useRef, useState } from "react";
import {
  ATTENTION, type Coverage, type Finding, type FindingRow, OUTCOME_LABEL, OUTCOME_MARK, OUTCOME_TONE, railLabel, sameCurrency,
} from "@/lib/finding";
import { downloadFindings } from "@/lib/csv";
import { formatMinor } from "@/lib/money";
import { AgentPanel } from "./agent-panel";
import { MoneyTrail } from "./money-trail";
import { useMerchantRun } from "./merchant-run";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { EmptyState } from "./ui/empty-state";
import { IconChevron, IconDownload } from "./ui/icons";
import { OutcomeSpectrum } from "./ui/outcome-spectrum";
import { Skeleton, SkeletonRows } from "./ui/skeleton";
import { Table, Td, Th } from "./ui/table";

/** One vocabulary across the product: a finding is a money outcome
 *  (ATTENTION); couldn't-verify is its own group, never folded into either. */
type Filter = "all" | "findings" | "paid" | "unverifiable";

export function FindingsPanel({ apiBase }: { apiBase: string }) {
  const { busy, finding, error, ranAt, reused, missing, run } = useMerchantRun();

  if (busy) return <Loading />;
  if (error) {
    return (
      <div role="alert" className="rounded-xl border border-danger/30 bg-danger-soft px-5 py-4">
        <p className="font-medium text-danger">
          <span aria-hidden className="mr-1.5 font-mono">!</span>
          The run didn&apos;t finish{error.status ? ` (${error.status})` : ""}
        </p>
        <p className="mt-1 text-sm break-words text-ink">{error.detail}</p>
        <Button variant="secondary" className="mt-4" onClick={() => void run()}>Try again</Button>
      </div>
    );
  }
  if (!finding) {
    return missing.length ? (
      <EmptyState title={`Connect ${missing.map(railLabel).join(" and ")} to check this merchant`}>
        A check reads the Shopify order ledger and both payment rails, PayPal and Stripe, and says whether each order was paid
        exactly once, twice, or not at all. Open Connections above to add what&apos;s missing.
      </EmptyState>
    ) : (
      <EmptyState title="No check yet">
        Run reconciliation, above, reads the Shopify ledger, PayPal and Stripe once and says whether each order was paid exactly
        once, twice, or not at all. Each run counts as one request against your plan.
      </EmptyState>
    );
  }
  return <Result f={finding} ranAt={ranAt} reused={reused} apiBase={apiBase} />;
}

function Result({ f, ranAt, reused, apiBase }: { f: Finding; ranAt: Date | null; reused: boolean; apiBase: string }) {
  const { tenantId, tenantName } = useMerchantRun();
  const order = useSearchParams().get("order");
  const s = f.summary;
  const findings = f.rows.filter((r) => ATTENTION.includes(r.outcome));
  const paid = f.rows.filter((r) => r.outcome === "collected" || r.outcome === "split_tender");
  const unverifiable = f.rows.filter((r) => r.outcome === "unverifiable");
  // A deep link to one order opens on the group that holds it.
  const [filter, setFilter] = useState<Filter>(() => {
    const hit = order ? f.rows.find((r) => r.order === order) : undefined;
    if (!hit) return findings.length ? "findings" : "all";
    return ATTENTION.includes(hit.outcome) ? "findings" : hit.outcome === "unverifiable" ? "unverifiable" : "paid";
  });
  const rows = filter === "findings" ? findings : filter === "paid" ? paid : filter === "unverifiable" ? unverifiable : f.rows;

  if (s.orders === 0) {
    return (
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_260px]">
        <EmptyState title="Shopify returned no orders">
          The ledger read came back empty for the window shown under What was read, so there was nothing to check.
        </EmptyState>
        <CoveragePanel s={s} />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <h2 className="sr-only">Result</h2>
      {s.collected_twice > 0 && <TwiceBand f={f} />}

      <section aria-label="Orders by outcome" className="rounded-xl border border-line bg-surface px-5 py-4">
        <div className="mb-3 flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <p>
            <span className="text-xl font-semibold tabular-nums">{s.orders.toLocaleString("en-US")}</span>
            <span className="ml-1.5 text-sm text-ink-soft">orders checked</span>
          </p>
          <p className="text-sm text-ink-soft">
            {s.match_rate === null
              ? "Match rate withheld: some orders couldn't be verified"
              : <><span className="font-semibold text-ink tabular-nums">{(s.match_rate * 100).toFixed(1)}%</span> paid exactly once</>}
          </p>
        </div>
        <OutcomeSpectrum summary={s} unconfirmed={unconfirmed(f)} />
      </section>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_260px]">
        <div className="min-w-0 space-y-3">
          <div role="group" aria-label="Filter orders" className="flex flex-wrap gap-2">
            <Pill on={filter === "findings"} onClick={() => setFilter("findings")} count={findings.length}>Findings</Pill>
            <Pill on={filter === "paid"} onClick={() => setFilter("paid")} count={paid.length}>Paid once</Pill>
            {unverifiable.length > 0 && (
              <Pill on={filter === "unverifiable"} onClick={() => setFilter("unverifiable")} count={unverifiable.length}>Couldn&apos;t verify</Pill>
            )}
            <Pill on={filter === "all"} onClick={() => setFilter("all")} count={f.rows.length}>All</Pill>
            <Button
              variant="ghost"
              className="ml-auto"
              disabled={!rows.length}
              onClick={() => downloadFindings(rows.map((r) => ({ ...r, merchantId: tenantId, merchantName: tenantName })), slug(tenantName))}
            >
              <IconDownload className="h-4 w-4" /> Export CSV
            </Button>
          </div>
          {rows.length ? (
            <OrderTable rows={rows} focus={order} />
          ) : (
            <EmptyState title={filter === "findings" ? "No findings" : "No orders here"}>
              {filter === "findings" ? "No order was found collected twice, short or unpaid in what was read." : null}
            </EmptyState>
          )}
          {f.partial && (
            <p className="text-xs text-ink-soft">The first 1,000 orders are shown. The full result pages over the API and MCP.</p>
          )}
        </div>
        <CoveragePanel s={s} />
      </div>

      <p className="border-t border-line pt-4 text-xs text-ink-soft">
        {ranAt && (
          <>
            {reused ? "Checked" : "Run"} at{" "}
            {/* Server and browser may sit in different time zones. */}
            <time dateTime={ranAt.toISOString()} suppressHydrationWarning>
              {ranAt.toLocaleTimeString("en-US", { timeStyle: "short" })}
            </time>
            {reused ? " by an earlier page load and reused for up to 10 minutes" : ""}. Not saved; run again for current data.{" "}
          </>
        )}
      </p>
      <AgentPanel apiBase={apiBase} outcome={filter === "findings" && s.collected_twice ? "collected_twice" : filter === "unverifiable" ? "unverifiable" : undefined} />
    </div>
  );
}

function TwiceBand({ f }: { f: Finding }) {
  const s = f.summary;
  const cur = f.twice_currency;
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-xl bg-volt px-5 py-4 text-ink">
      <span aria-hidden className="font-mono text-sm font-medium">×2</span>
      <p className="text-2xl font-semibold tracking-tight tabular-nums">
        {cur ? formatMinor(s.collected_twice_minor, cur) : `${s.collected_twice} orders`}
      </p>
      <p className="text-sm font-medium">
        collected twice · {s.collected_twice} order{s.collected_twice === 1 ? "" : "s"}
        {cur ? <> · {formatMinor(s.overcollected_minor, cur)} owed back</> : " · owed back"}
      </p>
    </div>
  );
}

function Pill({ on, onClick, count, children }: { on: boolean; onClick: () => void; count: number; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`inline-flex min-h-11 items-center gap-2 rounded-full border px-4 text-sm ${
        on ? "border-ink bg-ink text-canvas" : "border-line bg-surface text-ink-soft hover:border-ink hover:text-ink"
      }`}
    >
      {children}
      <span className="tabular-nums opacity-80">{count}</span>
    </button>
  );
}

/** Expanded rows are keyed by order, so a filter change never shows a
 *  different order as open. `focus` (from ?order=) opens and scrolls to one. */
function OrderTable({ rows, focus }: { rows: FindingRow[]; focus: string | null }) {
  const [open, setOpen] = useState<Set<string>>(() => new Set(focus ? [focus] : []));
  const target = useRef<HTMLTableRowElement>(null);
  useEffect(() => {
    target.current?.scrollIntoView({ block: "center" });
    target.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
  }, []);
  const toggle = (k: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  const gapOf = (r: FindingRow) =>
    r.collected_minor !== null && r.total_minor !== null && sameCurrency(r) ? r.collected_minor - r.total_minor : null;
  return (
    <>
      {/* Narrow screens: one card per order, money first, trail full width. */}
      <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface sm:hidden">
        {rows.map((r, i) => {
          const k = `${r.order}-${i}`;
          const expanded = open.has(r.order) || open.has(k);
          const id = `m-detail-${r.order.replace(/[^a-zA-Z0-9_-]/g, "")}-${i}`;
          const gap = gapOf(r);
          return (
            <li key={k} className={expanded ? "bg-canvas/60" : ""}>
              <button
                type="button"
                aria-expanded={expanded}
                aria-controls={id}
                onClick={() => toggle(open.has(r.order) ? r.order : k)}
                className="flex w-full items-start gap-3 px-4 py-3 text-left"
              >
                <IconChevron className={`mt-1 shrink-0 text-ink-soft transition-transform ${expanded ? "rotate-90" : ""}`} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[13px]">{r.order}</span>
                    <span className="text-sm font-medium tabular-nums">{taken(r)}</span>
                  </span>
                  <span className="mt-1 flex flex-wrap items-center justify-between gap-2">
                    <Badge tone={OUTCOME_TONE[r.outcome]} symbol={OUTCOME_MARK[r.outcome]}>{OUTCOME_LABEL[r.outcome] ?? r.outcome}</Badge>
                    <span className="text-xs text-ink-soft tabular-nums">
                      of {formatMinor(r.total_minor, r.currency)}
                      {gap !== null && gap !== 0 && <> · {gap > 0 ? "+" : "−"}{formatMinor(Math.abs(gap), r.currency)} {gap > 0 ? "over" : "short"}</>}
                    </span>
                  </span>
                </span>
              </button>
              <div id={id} hidden={!expanded} className="px-4 pb-4">{expanded && <Detail r={r} />}</div>
            </li>
          );
        })}
      </ul>
      <div className="hidden sm:block">
        <Table label="Orders" minWidth={640}>
          <thead>
            <tr>
              <Th className="w-12"><span className="sr-only">Details</span></Th>
              <Th>Order</Th>
              <Th>Outcome</Th>
              <Th className="text-right">Order total</Th>
              <Th className="text-right">Taken</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const k = `${r.order}-${i}`;
              const expanded = open.has(r.order) || open.has(k);
              const id = `detail-${r.order.replace(/[^a-zA-Z0-9_-]/g, "")}-${i}`;
              const gap = gapOf(r);
              return (
                <Fragment key={k}>
                  <tr
                    ref={focus === r.order ? target : undefined}
                    className={`border-t border-line ${expanded ? "bg-canvas/60" : "hover:bg-canvas/40"} ${focus === r.order ? "outline-2 -outline-offset-2 outline-ink/30" : ""}`}
                  >
                    <Td className="py-1 pr-0 pl-2">
                      <button
                        type="button"
                        aria-expanded={expanded}
                        aria-controls={id}
                        aria-label={`${expanded ? "Hide" : "Show"} details for order ${r.order}`}
                        onClick={() => toggle(open.has(r.order) ? r.order : k)}
                        className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-ink-soft hover:bg-ink/5 hover:text-ink"
                      >
                        <IconChevron className={`transition-transform ${expanded ? "rotate-90" : ""}`} />
                      </button>
                    </Td>
                    <Td className="font-mono text-[13px]">{r.order}</Td>
                    <Td>
                      <Badge tone={OUTCOME_TONE[r.outcome]} symbol={OUTCOME_MARK[r.outcome]}>
                        {OUTCOME_LABEL[r.outcome] ?? r.outcome}
                      </Badge>
                      <span className="mt-0.5 block text-xs text-ink-soft">{r.collected_on.map(railLabel).join(" + ") || "No rail"}</span>
                    </Td>
                    <Td className="text-right tabular-nums">{formatMinor(r.total_minor, r.currency)}</Td>
                    <Td className="text-right tabular-nums">
                      <span className="font-medium">{taken(r)}</span>
                      {gap !== null && gap !== 0 && (
                        <span className={`block text-xs ${gap > 0 ? "text-danger" : "text-ink-soft"}`}>
                          {gap > 0 ? "+" : "−"}{formatMinor(Math.abs(gap), r.currency)} {gap > 0 ? "over" : "short"}
                        </span>
                      )}
                    </Td>
                  </tr>
                  <tr id={id} hidden={!expanded} className="bg-canvas/60">
                    <td />
                    <td colSpan={4} className="px-4 pb-4 text-sm">
                      {expanded && <Detail r={r} />}
                    </td>
                  </tr>
                </Fragment>
              );
            })}
          </tbody>
        </Table>
      </div>
    </>
  );
}

/** The order's money trail, then what couldn't be ruled out, the API's
 *  reason as given, and the outcome code. */
function Detail({ r }: { r: FindingRow }) {
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <MoneyTrail r={r} />
      <dl className="grid content-start gap-x-4 gap-y-1.5 text-sm sm:grid-cols-[110px_1fr]">
        {r.unverified.length > 0 && (
          <>
            <dt className="text-xs text-ink-soft">Couldn&apos;t rule out</dt>
            <dd>{r.unverified.map(railLabel).join(", ")}</dd>
          </>
        )}
        {/* The trail already states amounts the reason gives in raw minor
            units; show the API's reason where the trail can't explain. */}
        {r.reason && (r.outcome === "unverifiable" || r.paid.length === 0) && (
          <>
            <dt className="text-xs text-ink-soft">Reason</dt>
            <dd className="break-words text-ink-soft">{r.reason}</dd>
          </>
        )}
        <dt className="text-xs text-ink-soft">Outcome code</dt>
        <dd><code className="font-mono text-xs">{r.outcome}</code></dd>
      </dl>
    </div>
  );
}

function CoveragePanel({ s }: { s: Finding["summary"] }) {
  const ledger = s.ledger_coverage?.source.split(".")[0] ?? "ledger";
  const sides: { name: string; cov: Coverage | null; orphans?: number }[] = [
    { name: ledger, cov: s.ledger_coverage },
    ...Object.entries(s.rails).map(([name, r]) => ({
      name: r.coverage?.source.split(".")[0] ?? name,
      cov: r.coverage,
      orphans: r.unmatched_right,
    })),
  ];
  // One time axis for every side, so a rail that read less than the ledger
  // is visibly shorter. Text below each bar states the same span.
  const t = (iso: string | null) => (iso ? Date.parse(iso) : NaN);
  const known = sides.flatMap((x) => (x.cov ? [t(x.cov.span_start), t(x.cov.span_end)] : [])).filter((v) => !Number.isNaN(v));
  const lo = known.length ? Math.min(...known) : NaN;
  const hi = known.length ? Math.max(...known) : NaN;
  const bar = (c: Coverage) => {
    if (Number.isNaN(lo) || hi <= lo) return { left: 0, width: 100, open: !c.span_start };
    const a = Number.isNaN(t(c.span_start)) ? lo : t(c.span_start);
    const b = Number.isNaN(t(c.span_end)) ? hi : t(c.span_end);
    return { left: ((a - lo) / (hi - lo)) * 100, width: Math.max(((b - a) / (hi - lo)) * 100, 2), open: !c.span_start };
  };
  return (
    <section aria-labelledby="coverage-title" className="h-fit rounded-xl border border-line bg-surface">
      <h2 id="coverage-title" className="border-b border-line px-5 py-3 text-sm font-semibold">What was read</h2>
      <ul className="divide-y divide-line">
        {sides.map((side, i) => (
          <li key={`${side.name}-${i}`} className="px-5 py-3 text-sm">
            <p className="font-medium">
              {railLabel(side.name)}
              {i === 0 && <span className="ml-1.5 text-xs font-normal text-ink-soft">ledger</span>}
            </p>
            {side.cov ? (
              <>
                <span aria-hidden className="relative mt-1.5 block h-1.5 rounded-full bg-canvas">
                  <span
                    className={`absolute inset-y-0 rounded-full ${side.cov.truncated ? "bg-hatch-danger" : "bg-ink-soft"} ${bar(side.cov).open ? "rounded-l-none" : ""}`}
                    style={{ left: `${bar(side.cov).left}%`, width: `${bar(side.cov).width}%` }}
                  />
                </span>
                <p className="mt-1 text-xs text-ink-soft">
                  {side.cov.records.toLocaleString("en-US")} records · {span(side.cov)}
                </p>
                {side.cov.truncated && (
                  <p className="mt-1 text-xs text-danger">
                    <span aria-hidden className="mr-1 font-mono">!</span>
                    Cut at {side.cov.cap.toLocaleString("en-US")} records; later ones weren&apos;t read.
                  </p>
                )}
              </>
            ) : (
              <p className="mt-0.5 text-xs text-ink-soft">No coverage reported</p>
            )}
            {side.orphans ? (
              <p className="mt-1 text-xs text-ink-soft">
                {side.orphans.toLocaleString("en-US")} {side.orphans === 1 ? "payment" : "payments"} with no {railLabel(ledger)} order
                (outside this check)
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function span(c: Coverage): string {
  const d = (iso: string | null) =>
    iso ? new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : null;
  const start = d(c.span_start);
  const end = d(c.span_end);
  if (start && end) return `${start} – ${end}`;
  if (end) return `to ${end}`;
  if (start) return `from ${start}`;
  return "full history";
}

function Loading() {
  return (
    <div role="status" aria-label="Reading the ledger, then each rail" className="space-y-5">
      <div className="flex flex-wrap gap-x-8 gap-y-3 rounded-xl border border-line bg-surface px-5 py-4">
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-4 w-24" />)}
      </div>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_260px]">
        <SkeletonRows rows={6} cols={4} label="Loading orders" />
        <div className="h-40 rounded-xl border border-line bg-surface p-4">
          <Skeleton className="h-3 w-28" />
          <Skeleton className="mt-4 h-3 w-full" />
          <Skeleton className="mt-3 h-3 w-3/4" />
        </div>
      </div>
    </div>
  );
}

function slug(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "merchant";
}

/** What was taken: one total, or each rail's own amount when they took a
 *  currency other than the order's (a sum would add unlike units). */
function taken(r: FindingRow): string {
  if (!sameCurrency(r)) return r.paid.map((p) => formatMinor(p.minor, p.currency ?? r.currency)).join(" + ");
  return r.collected_minor === null ? "—" : formatMinor(r.collected_minor, r.currency);
}

/** Paid once, but another rail couldn't rule out a second payment. From the
 *  rows on the page, so a partial page undercounts; it never overcounts. */
function unconfirmed(f: Finding): number {
  return f.rows.filter((r) => (r.outcome === "collected" || r.outcome === "split_tender") && r.unverified.length > 0).length;
}
