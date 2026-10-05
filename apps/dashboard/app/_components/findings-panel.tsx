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
import { VerdictCard } from "./verdict-card";
import { Card, CardBody, CardHeader, GRID } from "./ui/card";
import { EmptyState } from "./ui/empty-state";
import { IconPlay, IconPlug } from "./ui/icons";
import { CopyButton } from "./ui/copy-button";
import { OutcomeSpectrum } from "./ui/outcome-spectrum";
import { Skeleton, SkeletonRows } from "./ui/skeleton";

export function FindingsPanel({ apiBase, view = null, viewError = null, newKeys = [], saved = 0 }: {
  apiBase: string;
  /** How many saved checks the history below lists. */
  saved?: number;
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
  return <Result key={`${shown.runId ?? ""}:${order ?? ""}`} shown={shown} older={Boolean(view)} saved={saved} newKeys={newKeys} apiBase={apiBase} />;
}

function Result({ shown, older, saved, newKeys, apiBase }: { shown: Shown; older: boolean; saved: number; newKeys: string[]; apiBase: string }) {
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
          The orders read came back empty for the window shown under What was read, so there was nothing to check.
        </EmptyState>
        <CoveragePanel s={s} />
      </div>
    );
  }

  const twice = s.collected_twice > 0;
  const cur = f.twice_currency;
  return (
    <div className="space-y-6">
      <h2 className="sr-only">Result</h2>
      <VerdictCard
        ariaLabel="Verdict for this check"
        twice={twice}
        figure={twice
          ? (cur ? formatMinor(s.collected_twice_minor, cur) : `${s.collected_twice} orders`)
          : <><span aria-hidden className="mr-2 font-mono text-2xl text-ok">✓</span>None</>}
        sub={twice
          ? <>
            {s.collected_twice} order{s.collected_twice === 1 ? "" : "s"}
            {cur ? (s.overcollected_minor === s.collected_twice_minor
              ? " · all owed back to customers"
              : <> · <strong className="font-semibold">{formatMinor(s.overcollected_minor, cur)} owed back</strong></>) : null}
          </>
          : `No order taken twice in ${s.orders.toLocaleString("en-US")} checked.`}
        leftFooter={<span className="[&_button]:text-ink"><RunStamp shown={shown} older={older} saved={saved} /></span>}
        counts={<>
          <p>
            <span className="text-2xl font-semibold tabular-nums">{s.orders.toLocaleString("en-US")}</span>
            <span className="ml-1.5 text-sm text-ink-soft">orders checked</span>
          </p>
          <p className="text-sm text-ink-soft">
            {s.match_rate === null
              ? "Match rate withheld: some orders couldn't be verified"
              : <><span className="font-semibold text-ink tabular-nums">{(s.match_rate * 100).toFixed(1)}%</span> paid exactly once</>}
          </p>
        </>}
        spectrum={<OutcomeSpectrum summary={s} unconfirmed={unconfirmedRows(f)} animate={justRan !== null && shown.runId === justRan} />}
        footer={<a href="#agents" className="inline-flex min-h-11 items-center underline-offset-4 hover:text-ink hover:underline">Same result for your agents ↓</a>}
      />

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

      <AgentPanel
        id="agents"
        apiBase={apiBase}
        outcome={filter === "findings" && s.collected_twice ? "collected_twice" : filter === "unverifiable" ? "unverifiable" : undefined}
        keyFor={tenantName}
        setupHref={`/agents?merchant=${encodeURIComponent(tenantId)}`}
      />
    </div>
  );
}

/** When the shown check ran, from where, its id, and the way to the saved
 *  checks (or back to the latest, for an older one). Sits inside the
 *  VerdictCard, so on volt the copy control is forced to ink. */
function RunStamp({ shown, older, saved }: { shown: Shown; older: boolean; saved: number }) {
  const runId = shown.runId;
  return (
    <p className="text-xs">
      {older ? "Older check from " : "Checked "}
      {/* Server and browser may sit in different time zones. */}
      <time dateTime={new Date(shown.at).toISOString()} suppressHydrationWarning>{ago(shown.at)}</time>
      {" · "}{SURFACE_LABEL[shown.surface] ?? shown.surface}
      {runId && <>
        {" · "}<span className="font-mono">{runId.slice(0, 8)}…</span>{" "}
        <CopyButton value={runId} label="Copy run id" />
      </>}
      {older
        ? <>{" · "}<Link href="?tab=findings" scroll={false} className="underline underline-offset-4">Show latest</Link></>
        : saved > 0 && <>{" · "}<a href="#history" className="underline underline-offset-4">{saved} saved check{saved === 1 ? "" : "s"} ↓</a></>}
    </p>
  );
}

function CoveragePanel({ s }: { s: Finding["summary"] }) {
  const ledger = s.ledger_coverage?.source.split(".")[0] ?? "shopify";
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
    <Card aria-labelledby="coverage-title" className="h-fit">
      <CardHeader id="coverage-title" title="What was read" />
      <CardBody list>
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
      </CardBody>
    </Card>
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
    <div role="status" aria-label="Reading Shopify orders, then PayPal and Stripe" className="space-y-5">
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
