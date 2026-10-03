"use client";

import { Fragment, useState } from "react";
import {
  type Coverage, type Finding, type FindingRow, NEEDS_LOOK, OUTCOME_LABEL, OUTCOME_MARK, OUTCOME_TONE, railLabel,
} from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import { useMerchantRun } from "./merchant-run";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { EmptyState } from "./ui/empty-state";
import { IconChevron, IconPlay } from "./ui/icons";
import { Skeleton, SkeletonRows } from "./ui/skeleton";
import { Table, Td, Th } from "./ui/table";

type Filter = "all" | "attention" | "paid";

export function FindingsPanel() {
  const { busy, finding, error, ranAt, run } = useMerchantRun();

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
    return (
      <EmptyState
        title="No run yet"
        action={<Button variant="primary" onClick={() => void run()}><IconPlay className="h-4 w-4" /> Run reconciliation</Button>}
      >
        A run reads the order ledger and every connected rail once, and says whether each order was paid exactly once,
        twice, or not at all.
      </EmptyState>
    );
  }
  return <Result f={finding} ranAt={ranAt} />;
}

function Result({ f, ranAt }: { f: Finding; ranAt: Date | null }) {
  const [filter, setFilter] = useState<Filter>("all");
  const s = f.summary;
  const attention = f.rows.filter((r) => NEEDS_LOOK.includes(r.outcome));
  const paid = f.rows.filter((r) => !NEEDS_LOOK.includes(r.outcome));
  const rows = filter === "attention" ? attention : filter === "paid" ? paid : f.rows;

  return (
    <div className="space-y-5">
      {s.collected_twice > 0 && <TwiceBand f={f} />}

      <StatStrip f={f} />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_260px]">
        <div className="min-w-0 space-y-3">
          <div role="group" aria-label="Filter orders" className="flex flex-wrap gap-2">
            <Pill on={filter === "all"} onClick={() => setFilter("all")} count={f.rows.length}>All</Pill>
            <Pill on={filter === "attention"} onClick={() => setFilter("attention")} count={attention.length}>Needs attention</Pill>
            <Pill on={filter === "paid"} onClick={() => setFilter("paid")} count={paid.length}>Paid once</Pill>
          </div>
          {rows.length ? (
            <OrderTable rows={rows} />
          ) : (
            <EmptyState title={filter === "attention" ? "Nothing needs attention" : "No orders here"}>
              {filter === "attention" ? "Every order read was paid exactly once." : null}
            </EmptyState>
          )}
          {f.partial && (
            <p className="text-xs text-ink-soft">The first 1,000 orders are shown. The full result pages over the API and MCP.</p>
          )}
        </div>
        <CoveragePanel s={s} />
      </div>

      <p className="border-t border-line pt-4 text-xs text-ink-soft">
        {ranAt && <>Run at {ranAt.toLocaleTimeString("en-US", { timeStyle: "short" })}. Not saved; run again for current data. </>}
        Agents get the same result from <code className="font-mono">reconcile_across_rails</code> over the hosted MCP, with a key
        issued for this merchant.
      </p>
    </div>
  );
}

function TwiceBand({ f }: { f: Finding }) {
  const s = f.summary;
  const cur = f.twice_currency;
  return (
    <div role="status" className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-xl bg-volt px-5 py-4 text-ink">
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

function StatStrip({ f }: { f: Finding }) {
  const s = f.summary;
  const items: [string, string][] = [
    ["Orders", s.orders.toLocaleString("en-US")],
    ["Paid once", (s.collected + s.split_tender).toLocaleString("en-US")],
    ["Collected twice", s.collected_twice.toLocaleString("en-US")],
    ["No payment", s.uncollected.toLocaleString("en-US")],
    ["Couldn't verify", s.unverifiable.toLocaleString("en-US")],
    ["Match rate", s.match_rate === null ? "Withheld" : `${(s.match_rate * 100).toFixed(1)}%`],
  ];
  return (
    <dl className="flex flex-wrap gap-x-8 gap-y-3 rounded-xl border border-line bg-surface px-5 py-3">
      {items.map(([label, value]) => (
        <div key={label} className="flex items-baseline gap-2">
          <dt className="text-xs text-ink-soft">{label}</dt>
          <dd className="text-sm font-semibold tabular-nums">{value}</dd>
        </div>
      ))}
      {s.match_rate === null && (
        <p className="w-full text-xs text-ink-soft">Some orders couldn&apos;t be checked, so no match rate is given.</p>
      )}
    </dl>
  );
}

function Pill({ on, onClick, count, children }: { on: boolean; onClick: () => void; count: number; children: string }) {
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

function OrderTable({ rows }: { rows: FindingRow[] }) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const toggle = (i: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  return (
    <Table label="Orders" minWidth={600}>
      <thead>
        <tr>
          <Th className="w-12"><span className="sr-only">Details</span></Th>
          <Th>Order</Th>
          <Th>Outcome</Th>
          <Th>Rails</Th>
          <Th className="text-right">Amount</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const expanded = open.has(i);
          return (
            <Fragment key={`${r.order}-${i}`}>
              <tr className={`border-t border-line ${expanded ? "bg-canvas/60" : "hover:bg-canvas/40"}`}>
                <Td className="py-1 pr-0 pl-2">
                  <button
                    type="button"
                    aria-expanded={expanded}
                    aria-controls={`detail-${i}`}
                    aria-label={`${expanded ? "Hide" : "Show"} details for order ${r.order}`}
                    onClick={() => toggle(i)}
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
                </Td>
                <Td className="text-ink-soft">{r.collected_on.map(railLabel).join(" + ") || "—"}</Td>
                <Td className="text-right font-medium tabular-nums">{formatMinor(r.total_minor, r.currency)}</Td>
              </tr>
              {expanded && (
                <tr id={`detail-${i}`} className="bg-canvas/60">
                  <td />
                  <td colSpan={4} className="px-4 pb-4 text-sm">
                    <Detail r={r} />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </Table>
  );
}

/** Per-rail amounts formatted from minor units; the API's reason as given. */
function Detail({ r }: { r: FindingRow }) {
  return (
    <dl className="grid gap-x-6 gap-y-1.5 sm:grid-cols-[120px_1fr]">
      <dt className="text-xs text-ink-soft">Taken</dt>
      <dd>
        {r.paid.length
          ? r.paid.map((p) => `${railLabel(p.rail)} ${formatMinor(p.minor, p.currency ?? r.currency)}`).join(" · ")
          : "Nothing on any connected rail"}
        {r.outcome === "collected_twice" && r.collected_minor !== null && (
          <span className="text-ink-soft"> · {formatMinor(r.collected_minor, r.currency)} in all</span>
        )}
      </dd>
      {r.unverified.length > 0 && (
        <>
          <dt className="text-xs text-ink-soft">Not read</dt>
          <dd>{r.unverified.map(railLabel).join(", ")}</dd>
        </>
      )}
      {r.reason && (
        <>
          <dt className="text-xs text-ink-soft">Reason</dt>
          <dd className="break-words text-ink-soft">{r.reason}</dd>
        </>
      )}
      <dt className="text-xs text-ink-soft">Outcome code</dt>
      <dd><code className="font-mono text-xs">{r.outcome}</code></dd>
    </dl>
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
  return (
    <aside aria-labelledby="coverage-title" className="h-fit rounded-xl border border-line bg-surface">
      <h3 id="coverage-title" className="border-b border-line px-4 py-3 text-sm font-semibold">What was read</h3>
      <ul className="divide-y divide-line">
        {sides.map((side, i) => (
          <li key={`${side.name}-${i}`} className="px-4 py-3 text-sm">
            <p className="font-medium">
              {railLabel(side.name)}
              {i === 0 && <span className="ml-1.5 text-xs font-normal text-ink-soft">ledger</span>}
            </p>
            {side.cov ? (
              <>
                <p className="mt-0.5 text-xs text-ink-soft">
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
    </aside>
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
