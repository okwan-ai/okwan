"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ATTENTION, atStake, type AttentionRow, OUTCOME_LABEL, OUTCOME_MARK, OUTCOME_TONE, railLabel, rowKey, sameCurrency, what,
} from "@/lib/finding";
import { downloadFindings } from "@/lib/csv";
import { formatMinor } from "@/lib/money";
import { MiniTrail } from "./money-trail";
import { OrderDrawer } from "./order-drawer";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardHeader } from "./ui/card";
import { EmptyState } from "./ui/empty-state";
import { IconChevron, IconDownload } from "./ui/icons";
import { RowCard, RowCardList } from "./ui/row-card";
import { Segmented } from "./ui/segmented";
import { Table, Td, Th } from "./ui/table";
import { Toolbar, ToolbarSearch, ToolbarSelect } from "./ui/toolbar";

/** The merchant page's filter groups. A finding is a money outcome
 *  (ATTENTION); couldn't-verify is its own group, never folded into either. */
export type MerchantFilter = "findings" | "paid" | "unverifiable" | "all";

const PAID = ["collected", "split_tender"];

/** The rows a merchant filter shows. */
export function inGroup(rows: AttentionRow[], f: MerchantFilter): AttentionRow[] {
  if (f === "findings") return rows.filter((r) => ATTENTION.includes(r.outcome));
  if (f === "paid") return rows.filter((r) => PAID.includes(r.outcome));
  if (f === "unverifiable") return rows.filter((r) => r.outcome === "unverifiable");
  return rows;
}

/** The group that holds an outcome, for a deep link to one order. */
export function groupOf(outcome: string): MerchantFilter {
  return ATTENTION.includes(outcome) ? "findings" : outcome === "unverifiable" ? "unverifiable" : "paid";
}

/**
 * One findings table for every list of orders (§9 2026-10-05): the
 * cross-merchant worksheet (scope "workspace"), one merchant's check
 * (scope "merchant") and Overview's short list (toolbar off). Every filter
 * runs in the browser over rows the page already has, so filtering never
 * re-runs or re-meters a check. Each order opens the one OrderDrawer, the
 * only place its proof is drawn; closing it returns focus to the row.
 *
 * Workspace filters live in the URL (replaceState, no server round trip),
 * so `/findings?outcome=collected_twice` can be linked. The merchant
 * filter is controlled by the panel, so the AgentPanel's outcome follows it.
 */
export function FindingsTable({
  scope,
  rows,
  apiBase,
  merchants = [],
  limit,
  toolbar = true,
  filter: filterProp,
  onFilter,
  newKeys = [],
  exportName,
  partial = false,
}: {
  scope: "workspace" | "merchant";
  rows: AttentionRow[];
  apiBase: string;
  merchants?: { id: string; name: string }[];
  limit?: number;
  toolbar?: boolean;
  filter?: MerchantFilter;
  onFilter?: (f: MerchantFilter) => void;
  newKeys?: string[];
  exportName?: string;
  partial?: boolean;
}) {
  const params = useSearchParams();
  const ws = scope === "workspace";

  // Workspace: ?outcome, ?merchant and ?q, synced both ways.
  const [outcome, setOutcome] = useState(() => (ws ? valid(params.get("outcome")) : ""));
  const known = (id: string | null) => (id && merchants.some((m) => m.id === id) ? id : "");
  const [merchant, setMerchant] = useState(() => (ws ? known(params.get("merchant")) : ""));
  const [q, setQ] = useState(() => (ws ? params.get("q") ?? "" : ""));
  // The last query string this table wrote, so the URL-follow effect can
  // tell its own writes from a navigation.
  const own = useRef<string | null>(null);
  useEffect(() => {
    if (!ws || !toolbar) return;
    if (params.toString() === own.current) return;
    // Following a navigation (palette, sidebar): forget our last write, or a
    // later link to that same URL would be mistaken for our own and ignored.
    own.current = null;
    setOutcome(valid(params.get("outcome")));
    setMerchant(known(params.get("merchant")));
    setQ(params.get("q") ?? "");
  }, [params]);
  function sync(next: { outcome?: string; merchant?: string; q?: string }) {
    const u = new URL(window.location.href);
    for (const [k, v] of Object.entries(next)) {
      if (v) u.searchParams.set(k, v);
      else u.searchParams.delete(k);
    }
    own.current = u.searchParams.toString();
    window.history.replaceState(null, "", u);
  }

  // Merchant: controlled when the panel passes it, otherwise local.
  const [localFilter, setLocalFilter] = useState<MerchantFilter>(filterProp ?? "all");
  const filter = filterProp ?? localFilter;
  const setFilter = (f: MerchantFilter) => (onFilter ? onFilter(f) : setLocalFilter(f));

  const inMerchant = ws && merchant ? rows.filter((r) => r.merchantId === merchant) : rows;
  const shown = useMemo(() => {
    if (!toolbar) return rows.slice(0, limit ?? rows.length);
    if (!ws) return inGroup(rows, filter);
    const term = q.trim().toLowerCase().replace(/^#/, "");
    return inMerchant.filter((r) => (!outcome || r.outcome === outcome) && (!term || r.order.toLowerCase().replace(/^#/, "").includes(term)));
  }, [rows, toolbar, limit, ws, filter, inMerchant, outcome, q]);
  const total = rows.length;
  const fresh = useMemo(() => new Set(newKeys), [newKeys]);

  // One drawer over the filtered list. A merchant-page ?order= opens it on
  // that order when the table mounts (the panel picks the group holding it).
  const order = params.get("order");
  const [proof, setProof] = useState<number | null>(() => {
    if (ws || !order) return null;
    const i = shown.findIndex((r) => r.order === order);
    return i >= 0 ? i : null;
  });
  const deskButtons = useRef<(HTMLButtonElement | null)[]>([]);
  const phoneButtons = useRef<(HTMLButtonElement | null)[]>([]);
  function close() {
    const i = proof;
    setProof(null);
    if (i === null) return;
    // Back to the row's order button, including after prev/next and a deep
    // link; the visible one (the table above sm, the card list below).
    requestAnimationFrame(() => {
      const el = [phoneButtons.current[i], deskButtons.current[i]].find((x) => x && x.offsetParent !== null);
      el?.focus();
    });
  }

  const counts = Object.fromEntries(ATTENTION.map((o) => [o, inMerchant.filter((r) => r.outcome === o).length]));
  const unverifiable = ws ? 0 : inGroup(rows, "unverifiable").length;
  const filters = ws ? (
    <Segmented
      label="Filter by outcome"
      value={outcome || "all"}
      onChange={(k) => { const o = k === "all" ? "" : k; setOutcome(o); sync({ outcome: o }); }}
      items={[
        { key: "all", label: "All findings", count: inMerchant.length },
        ...ATTENTION.map((o) => ({ key: o, label: OUTCOME_LABEL[o], glyph: OUTCOME_MARK[o], count: counts[o] })),
      ]}
    />
  ) : (
    <Segmented
      label="Filter orders"
      value={filter}
      onChange={(k) => setFilter(k as MerchantFilter)}
      items={[
        { key: "findings", label: "Findings", count: inGroup(rows, "findings").length },
        { key: "paid", label: "Paid once", count: inGroup(rows, "paid").length },
        ...(unverifiable > 0 ? [{ key: "unverifiable", label: "Couldn't verify", count: unverifiable }] : []),
        { key: "all", label: "All", count: rows.length },
      ]}
    />
  );

  const exportCsv = () => downloadFindings(shown, exportName ?? "findings");

  return (
    <div className="min-w-0">
      {toolbar && (
        <Toolbar filters={filters}>
          {ws && (
            <>
              <ToolbarSearch
                label="Find an order"
                placeholder="Order #"
                value={q}
                onChange={(e) => { setQ(e.target.value); sync({ q: e.target.value }); }}
              />
              <ToolbarSelect
                aria-label="Merchant"
                value={merchant}
                onChange={(e) => { setMerchant(e.target.value); sync({ merchant: e.target.value }); }}
              >
                <option value="">All merchants ({rows.length})</option>
                {merchants.map((m) => (
                  <option key={m.id} value={m.id}>{m.name} ({rows.filter((r) => r.merchantId === m.id).length})</option>
                ))}
              </ToolbarSelect>
            </>
          )}
        </Toolbar>
      )}
      {toolbar && !ws && fresh.size > 0 && (
        <p className="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-soft">
          <Badge title="Not in the check before this one">New</Badge>
          {fresh.size} finding{fresh.size === 1 ? "" : "s"} not in the check before this one.
        </p>
      )}

      {shown.length === 0 ? (
        ws ? (
          <EmptyState title={rows.length ? "No finding matches these filters" : "Nothing needs attention"}>
            {rows.length ? "Clear a filter to see the rest." : "No order was collected twice, short or unpaid in what was read."}
          </EmptyState>
        ) : (
          <EmptyState title={filter === "findings" ? "No findings" : "No orders here"}>
            {filter === "findings" ? "No order was collected twice, short or unpaid in what was read." : null}
          </EmptyState>
        )
      ) : (
        <>
          <Card flush className="hidden sm:block">
            {toolbar && (
              <CardHeader
                title={<span aria-live="polite">{shown.length} of {total} {ws ? "shown" : "orders"}</span>}
                description={<span aria-live="polite">{stakeTotals(shown)}</span>}
                actions={<ExportButton onClick={exportCsv} />}
              />
            )}
            <Table label={ws ? "Findings" : "Orders"} minWidth={ws ? 820 : 720} flush>
              <thead>
                <tr>
                  <Th>Outcome</Th>
                  <Th>Order</Th>
                  <Th>What happened</Th>
                  <Th className="hidden w-28 lg:table-cell"><span className="sr-only">Order against what was taken</span></Th>
                  {ws && <Th>Merchant</Th>}
                  <Th className="text-right">At stake</Th>
                  <Th className="w-12"><span className="sr-only">Open</span></Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {shown.map((r, i) => {
                  const m = money(r);
                  return (
                    <tr key={`${r.merchantId}-${r.order}-${i}`} className="hover:bg-canvas/60">
                      <Td>
                        <span className="inline-flex flex-wrap items-center gap-1.5">
                          <Badge tone={OUTCOME_TONE[r.outcome]} symbol={OUTCOME_MARK[r.outcome]}>{OUTCOME_LABEL[r.outcome] ?? r.outcome}</Badge>
                          {fresh.has(rowKey(r)) && <Badge title="Not in the check before this one">New</Badge>}
                        </span>
                      </Td>
                      <Td>
                        <button
                          type="button"
                          ref={(el) => { deskButtons.current[i] = el; }}
                          onClick={() => setProof(i)}
                          aria-label={`Show the proof for order ${r.order}`}
                          className="inline-flex min-h-11 items-center font-mono text-[13px] underline underline-offset-4 decoration-line-strong hover:decoration-ink"
                        >
                          {r.order}
                        </button>
                      </Td>
                      <Td className="text-ink-soft">{what(r, formatMinor)}</Td>
                      <Td className="hidden lg:table-cell"><MiniTrail r={r} /></Td>
                      {ws && (
                        <Td>
                          <Link
                            href={`/merchants/${encodeURIComponent(r.merchantId)}?tab=findings`}
                            className="inline-flex min-h-11 items-center whitespace-nowrap underline-offset-4 hover:underline"
                          >
                            {r.merchantName}
                          </Link>
                        </Td>
                      )}
                      <Td className="text-right">
                        {m ? (
                          <>
                            <span className={`block font-semibold tabular-nums${m.soft ? " text-ink-soft" : ""}`}>{m.value}</span>
                            <span className="block text-xs text-ink-soft">{m.label}</span>
                          </>
                        ) : (
                          <span className="text-ink-soft">—</span>
                        )}
                      </Td>
                      <Td className="py-1 pr-2">
                        <button
                          type="button"
                          onClick={() => setProof(i)}
                          aria-label={`Open order ${r.order}`}
                          className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-ink-soft hover:bg-ink/5"
                        >
                          <IconChevron />
                        </button>
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          </Card>

          {/* Phones: the count and money line, then one card per order. */}
          {toolbar && (
            <div className="mb-3 flex items-center justify-between gap-3 sm:hidden">
              <div className="min-w-0" aria-live="polite">
                <p className="text-sm font-semibold">{shown.length} of {total} {ws ? "shown" : "orders"}</p>
                <p className="text-xs text-ink-soft">{stakeTotals(shown)}</p>
              </div>
              <ExportButton onClick={exportCsv} />
            </div>
          )}
          <RowCardList label={ws ? "Findings" : "Orders"}>
            {shown.map((r, i) => {
              const m = money(r);
              return (
                <RowCard
                  key={`${r.merchantId}-${r.order}-${i}`}
                  onClick={() => setProof(i)}
                  buttonRef={(el) => { phoneButtons.current[i] = el; }}
                  title={<Badge tone={OUTCOME_TONE[r.outcome]} symbol={OUTCOME_MARK[r.outcome]}>{OUTCOME_LABEL[r.outcome] ?? r.outcome}</Badge>}
                  money={m ?? undefined}
                  sentence={<><span className="font-mono text-[13px]">{r.order}</span> · {what(r, formatMinor)}</>}
                  meta={ws ? r.merchantName : fresh.has(rowKey(r)) ? <Badge title="Not in the check before this one">New</Badge> : undefined}
                />
              );
            })}
          </RowCardList>
        </>
      )}
      {partial && (
        <p className="mt-3 text-xs text-ink-soft">The first 1,000 orders are shown. The full result pages over the API and MCP.</p>
      )}
      <OrderDrawer rows={shown} index={proof} onIndex={setProof} onClose={close} apiBase={apiBase} onMerchantPage={!ws} />
    </div>
  );
}

function ExportButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="secondary" onClick={onClick} className="max-sm:w-11 max-sm:px-0">
      <IconDownload className="h-4 w-4" /> <span className="max-sm:sr-only">Export CSV</span>
    </Button>
  );
}

/** At stake for a finding; what was paid (soft) for a paid-once row;
 *  nothing for couldn't-verify. Never a finding tone on a paid row. */
function money(r: AttentionRow): { value: string; label: string; soft?: boolean } | null {
  const s = atStake(r);
  if (s) return { value: formatMinor(s.minor, r.currency), label: s.label };
  if (PAID.includes(r.outcome)) {
    const value = sameCurrency(r)
      ? r.collected_minor === null ? null : formatMinor(r.collected_minor, r.currency)
      : r.paid.map((p) => `${railLabel(p.rail)} ${formatMinor(p.minor, p.currency ?? r.currency)}`).join(" + ");
    return value ? { value, label: "paid", soft: true } : null;
  }
  return null;
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
