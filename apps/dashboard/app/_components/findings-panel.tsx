"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import {
  ago, ATTENTION, type AttentionRow, type Coverage, type Finding, railLabel, SURFACE_LABEL, truncated, unconfirmedRows,
} from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import { AgentPanel } from "./agent-panel";
import { FindingsTable, groupOf, type MerchantFilter } from "./findings-table";
import { type Shown, useMerchantRun } from "./merchant-run";
import { GRID } from "./ui/card";
import { EmptyState } from "./ui/empty-state";
import { IconPlay, IconPlug } from "./ui/icons";
import { OutcomeSpectrum } from "./ui/outcome-spectrum";
import { Skeleton, SkeletonRows } from "./ui/skeleton";

export function FindingsPanel({ apiBase, view = null, viewError = null, newKeys = [] }: {
  apiBase: string;
  /** A stored run chosen from the history, shown instead of the newest. */
  view?: Shown | null;
  /** The chosen run failed: its scrubbed error and when. */
  viewError?: { detail: string; at: number } | null;
  /** Findings not present in the run before this one. */
  newKeys?: string[];
}) {
  const { busy, shown: current, error: currentError, missing } = useMerchantRun();
  const shown = view ?? (viewError ? null : current);
  const error = viewError ? { status: 0, ...viewError } : view ? null : currentError;
  // A new ?order= on the same page (palette, drawer) remounts the result so
  // the filter, the open row and the scroll follow the link.
  const order = useSearchParams().get("order");

  if (busy) return <Loading />;
  if (error) {
    // No button here: the header's Run check is the retry.
    return (
      <div role="alert" className="rounded-xl border border-danger/30 bg-danger-soft px-5 py-4">
        <p className="font-medium text-danger">
          <span aria-hidden className="mr-1.5 font-mono">!</span>
          {viewError ? "This" : "The last"} check couldn&apos;t run{error.status ? ` (${error.status})` : ""}
          {error.at ? <span className="font-normal text-ink-soft" suppressHydrationWarning> · {ago(error.at)}</span> : null}
        </p>
        <p className="mt-1 text-sm break-words text-ink">{error.detail}</p>
        <Link href="?tab=connections" scroll={false} className="mt-3 inline-flex min-h-11 items-center text-sm font-medium underline underline-offset-4">
          Check connections →
        </Link>
      </div>
    );
  }
  if (!shown) {
    return missing.length ? (
      <EmptyState
        icon={<IconPlug />}
        title={`Connect ${listOf(missing.map(railLabel))} to check this merchant`}
        benefits={[
          "Reads Shopify orders, PayPal and Stripe once",
          "One verdict per order: paid once, twice, short or not at all",
          "Credentials are encrypted and never shown again",
        ]}
        action={<Link href="?tab=connections" scroll={false} className="inline-flex min-h-11 items-center text-sm font-medium underline underline-offset-4">Go to Connections</Link>}
      />
    ) : (
      <EmptyState icon={<IconPlay />} title="No check yet">
        Run check reads Shopify, PayPal and Stripe once and saves the result here.
      </EmptyState>
    );
  }
  return <Result key={`${shown.runId ?? ""}:${order ?? ""}`} shown={shown} newKeys={newKeys} apiBase={apiBase} />;
}

function Result({ shown, newKeys, apiBase }: { shown: Shown; newKeys: string[]; apiBase: string }) {
  const { tenantId, tenantName, justRan } = useMerchantRun();
  const order = useSearchParams().get("order");
  const f = shown.finding;
  const ranAt = new Date(shown.at);
  const s = f.summary;
  // Rows as the table, the drawer and the CSV need them: the merchant, the
  // check's time and whether it was cut short.
  const rows: AttentionRow[] = f.rows.map((r) => ({ ...r, merchantId: tenantId, merchantName: tenantName, at: ranAt.getTime(), partial: f.partial || truncated(f) }));
  // A deep link to one order opens on the group that holds it.
  const [filter, setFilter] = useState<MerchantFilter>(() => {
    const hit = order ? f.rows.find((r) => r.order === order) : undefined;
    if (hit) return groupOf(hit.outcome);
    return f.rows.some((r) => ATTENTION.includes(r.outcome)) ? "findings" : "all";
  });

  if (s.orders === 0) {
    return (
      <div className={GRID.mainAside}>
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
        <OutcomeSpectrum summary={s} unconfirmed={unconfirmedRows(f)} animate={justRan !== null && shown.runId === justRan} />
      </section>

      <div className={GRID.mainAside}>
        <FindingsTable
          scope="merchant"
          rows={rows}
          apiBase={apiBase}
          filter={filter}
          onFilter={setFilter}
          newKeys={newKeys}
          exportName={slug(tenantName)}
          partial={f.partial}
        />
        <CoveragePanel s={s} />
      </div>

      <p className="border-t border-line pt-4 text-xs text-ink-soft">
        Run{" "}
        {/* Server and browser may sit in different time zones. */}
        <time dateTime={ranAt.toISOString()} suppressHydrationWarning>{ago(shown.at)}</time>
        {" "}from {SURFACE_LABEL[shown.surface] ?? shown.surface}
        {shown.runId && <> · stored as <code className="font-mono">{shown.runId}</code></>}. Run again for current data.
      </p>
      <AgentPanel
        apiBase={apiBase}
        outcome={filter === "findings" && s.collected_twice ? "collected_twice" : filter === "unverifiable" ? "unverifiable" : undefined}
        keyFor={tenantName}
        setupHref={`/agents?merchant=${encodeURIComponent(tenantId)}`}
      />
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
              {i === 0 && <span className="ml-1.5 text-xs font-normal text-ink-soft">orders</span>}
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

/** In UTC: the API's spans are UTC instants, and formatting them in the
 *  server's or the browser's zone could shift a day (and disagree between
 *  the server render and hydration). */
function span(c: Coverage): string {
  const d = (iso: string | null) =>
    iso ? new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }) : null;
  const start = d(c.span_start);
  const end = d(c.span_end);
  if (start && end) return `${start} – ${end}`;
  if (end) return `full history to ${end}`;
  if (start) return `from ${start}`;
  return "full history";
}

function Loading() {
  return (
    <div role="status" aria-label="Reading the ledger, then each rail" className="space-y-5">
      <div className="flex flex-wrap gap-x-8 gap-y-3 rounded-xl border border-line bg-surface px-5 py-4">
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-4 w-24" />)}
      </div>
      <div className={GRID.mainAside}>
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

function listOf(items: string[]): string {
  return items.length <= 1 ? items[0] ?? "" : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}
