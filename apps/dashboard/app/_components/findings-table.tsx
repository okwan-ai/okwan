"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { ATTENTION, atStake, OUTCOME_LABEL, OUTCOME_MARK, OUTCOME_TONE, what } from "@/lib/finding";
import { downloadFindings } from "@/lib/csv";
import { formatMinor } from "@/lib/money";
import type { AttentionRow } from "@/lib/finding";
import { MiniTrail } from "./money-trail";
import { OrderDrawer } from "./order-drawer";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { EmptyState } from "./ui/empty-state";
import { IconDownload, IconSearch } from "./ui/icons";
import { Table, Td, Th } from "./ui/table";

/**
 * The cross-merchant worksheet. Every filter runs in the browser over rows
 * the page already has: a server-side filter would re-run, and re-meter,
 * every merchant's fold. Filters live in the URL (replaceState, no server
 * round trip), so `/findings?outcome=collected_twice` can be linked.
 */
export function FindingsTable({ rows, merchants, apiBase }: {
  rows: AttentionRow[];
  merchants: { id: string; name: string }[];
  apiBase: string;
}) {
  const params = useSearchParams();
  const [outcome, setOutcome] = useState(() => valid(params.get("outcome")));
  const [merchant, setMerchant] = useState(() => params.get("merchant") ?? "");
  const [q, setQ] = useState(() => params.get("q") ?? "");
  const [proof, setProof] = useState<number | null>(null);

  function sync(next: { outcome?: string; merchant?: string; q?: string }) {
    const u = new URL(window.location.href);
    for (const [k, v] of Object.entries(next)) {
      if (v) u.searchParams.set(k, v);
      else u.searchParams.delete(k);
    }
    window.history.replaceState(null, "", u);
  }

  const inMerchant = merchant ? rows.filter((r) => r.merchantId === merchant) : rows;
  const counts = Object.fromEntries(ATTENTION.map((o) => [o, inMerchant.filter((r) => r.outcome === o).length]));
  const shown = useMemo(() => {
    const term = q.trim().toLowerCase().replace(/^#/, "");
    return inMerchant.filter((r) => (!outcome || r.outcome === outcome) && (!term || r.order.toLowerCase().replace(/^#/, "").includes(term)));
  }, [inMerchant, outcome, q]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Filter by outcome" className="flex flex-wrap gap-2">
          <Chip on={!outcome} onClick={() => { setOutcome(""); sync({ outcome: "" }); }} count={inMerchant.length}>All findings</Chip>
          {ATTENTION.map((o) => (
            <Chip key={o} on={outcome === o} onClick={() => { setOutcome(o); sync({ outcome: o }); }} count={counts[o]} mark={OUTCOME_MARK[o]}>
              {OUTCOME_LABEL[o]}
            </Chip>
          ))}
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <label className="relative">
            <span className="sr-only">Find an order</span>
            <IconSearch className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-ink-soft" />
            <input
              value={q}
              onChange={(e) => { setQ(e.target.value); sync({ q: e.target.value }); }}
              placeholder="Order #"
              className="field w-36 py-2 pl-8"
            />
          </label>
          <label>
            <span className="sr-only">Merchant</span>
            <select
              value={merchant}
              onChange={(e) => { setMerchant(e.target.value); sync({ merchant: e.target.value }); }}
              className="field w-auto min-w-48 py-2"
            >
              <option value="">All merchants ({rows.length})</option>
              {merchants.map((m) => (
                <option key={m.id} value={m.id}>{m.name} ({rows.filter((r) => r.merchantId === m.id).length})</option>
              ))}
            </select>
          </label>
          <Button variant="secondary" onClick={() => downloadFindings(shown)} disabled={!shown.length}>
            <IconDownload className="h-4 w-4" /> Export CSV
          </Button>
        </div>
      </div>
      <p className="text-xs text-ink-soft" aria-live="polite">
        {shown.length} of {rows.length} finding{rows.length === 1 ? "" : "s"} shown
        {shown.length > 0 && <>{" · "}{stakeTotals(shown)}</>}
      </p>

      {shown.length === 0 ? (
        <EmptyState title={rows.length ? "No finding matches these filters" : "Nothing needs attention"}>
          {rows.length ? "Clear a filter to see the rest." : "No order was found collected twice, short or unpaid in what was read."}
        </EmptyState>
      ) : (
        <>
          {/* Narrow screens: one stacked card per finding, money first. */}
          <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface sm:hidden">
            {shown.map((r, i) => {
              const s = atStake(r);
              return (
                <li key={`${r.merchantId}-${r.order}-${i}`}>
                  <button type="button" onClick={() => setProof(i)} className="block w-full px-4 py-3 text-left">
                    <span className="flex items-center justify-between gap-3">
                      <Badge tone={OUTCOME_TONE[r.outcome]} symbol={OUTCOME_MARK[r.outcome]}>{OUTCOME_LABEL[r.outcome]}</Badge>
                      <span className="text-right text-sm font-semibold tabular-nums">
                        {s ? formatMinor(s.minor, r.currency) : "—"}
                        {s && <span className="block text-[11px] font-normal text-ink-soft">{s.label}</span>}
                      </span>
                    </span>
                    <span className="mt-1 block text-sm"><span className="font-mono">{r.order}</span> · {what(r, formatMinor)}</span>
                    <span className="mt-0.5 block text-xs text-ink-soft">{r.merchantName}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="hidden sm:block">
            <Table label="Findings" minWidth={820}>
              <thead>
                <tr>
                  <Th>Outcome</Th>
                  <Th>Order</Th>
                  <Th>What happened</Th>
                  <Th className="hidden w-28 lg:table-cell"><span className="sr-only">Order against what was taken</span></Th>
                  <Th>Merchant</Th>
                  <Th className="text-right">At stake</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {shown.map((r, i) => {
                  const s = atStake(r);
                  return (
                    <tr key={`${r.merchantId}-${r.order}-${i}`} className="hover:bg-canvas/60">
                      <Td><Badge tone={OUTCOME_TONE[r.outcome]} symbol={OUTCOME_MARK[r.outcome]}>{OUTCOME_LABEL[r.outcome]}</Badge></Td>
                      <Td>
                        <button
                          type="button"
                          onClick={() => setProof(i)}
                          aria-label={`Show the proof for order ${r.order}`}
                          className="inline-flex min-h-11 items-center font-mono text-[13px] underline underline-offset-4 decoration-line-strong hover:decoration-ink"
                        >
                          {r.order}
                        </button>
                      </Td>
                      <Td className="text-ink-soft">{what(r, formatMinor)}</Td>
                      <Td className="hidden lg:table-cell"><MiniTrail r={r} /></Td>
                      <Td>
                        <Link href={`/merchants/${encodeURIComponent(r.merchantId)}`} className="inline-flex min-h-11 items-center whitespace-nowrap underline-offset-4 hover:underline">
                          {r.merchantName}
                        </Link>
                      </Td>
                      <Td className="text-right">
                        <span className="block font-semibold tabular-nums">{s ? formatMinor(s.minor, r.currency) : "—"}</span>
                        {s && <span className="block text-[11px] text-ink-soft">{s.label}</span>}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          </div>
        </>
      )}
      <OrderDrawer rows={shown} index={proof} onIndex={setProof} onClose={() => setProof(null)} apiBase={apiBase} />
    </div>
  );
}

function valid(o: string | null): string {
  return o && ATTENTION.includes(o) ? o : "";
}

/** "$1,639.00 owed back · $30.00 short", per currency and kind. */
function stakeTotals(rows: AttentionRow[]): string {
  const sums = new Map<string, { minor: number; currency: string | null; label: string }>();
  for (const r of rows) {
    const s = atStake(r);
    if (!s) continue;
    const key = `${s.label}|${(r.currency ?? "").toUpperCase()}`;
    const cur = sums.get(key);
    sums.set(key, { minor: (cur?.minor ?? 0) + s.minor, currency: r.currency, label: s.label });
  }
  return [...sums.values()].map((v) => `${formatMinor(v.minor, v.currency)} ${v.label}`).join(" · ");
}

function Chip({ on, onClick, count, mark, children }: {
  on: boolean;
  onClick: () => void;
  count: number;
  mark?: string;
  children: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`inline-flex min-h-11 items-center gap-2 rounded-full border px-4 text-sm ${
        on ? "border-ink bg-ink text-canvas" : "border-line bg-surface text-ink-soft hover:border-ink hover:text-ink"
      }`}
    >
      {mark && <span aria-hidden className="font-mono text-xs">{mark}</span>}
      {children}
      <span className="tabular-nums opacity-80">{count}</span>
    </button>
  );
}
