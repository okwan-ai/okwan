"use client";

import Link from "next/link";
import { useState } from "react";
import { OUTCOME_LABEL, OUTCOME_MARK, OUTCOME_TONE, sentence } from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import type { AttentionRow } from "@/lib/runs";
import { Badge } from "./ui/badge";
import { EmptyState } from "./ui/empty-state";
import { Table, Td, Th } from "./ui/table";

/** Filtered in the browser: a server-side filter would re-run every
 *  merchant's fold, and each run is metered. */
export function FindingsTable({ rows, merchants }: { rows: AttentionRow[]; merchants: { id: string; name: string }[] }) {
  const [merchant, setMerchant] = useState("");
  const shown = merchant ? rows.filter((r) => r.merchantId === merchant) : rows;
  const counts = new Map<string, number>();
  for (const r of rows) counts.set(r.merchantId, (counts.get(r.merchantId) ?? 0) + 1);

  return (
    <div className="space-y-3">
      <label className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-ink-soft">Merchant</span>
        <select
          value={merchant}
          onChange={(e) => setMerchant(e.target.value)}
          className="field w-auto min-w-56 py-2"
        >
          <option value="">All merchants ({rows.length})</option>
          {merchants.map((m) => (
            <option key={m.id} value={m.id}>{m.name} ({counts.get(m.id) ?? 0})</option>
          ))}
        </select>
      </label>
      {shown.length === 0 ? (
        <EmptyState title="Nothing needs attention">Every order checked for this merchant was paid exactly once.</EmptyState>
      ) : (
        <Table label="Findings" minWidth={760}>
          <thead>
            <tr>
              <Th>Outcome</Th>
              <Th>Order</Th>
              <Th>What happened</Th>
              <Th>Merchant</Th>
              <Th className="text-right">Amount</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {shown.map((r, i) => (
              <tr key={`${r.merchantId}-${r.order}-${i}`} className="hover:bg-canvas/60">
                <Td><Badge tone={OUTCOME_TONE[r.outcome]} symbol={OUTCOME_MARK[r.outcome]}>{OUTCOME_LABEL[r.outcome]}</Badge></Td>
                <Td className="font-mono text-[13px]">{r.order}</Td>
                <Td className="text-ink-soft">{sentence(r)}</Td>
                <Td>
                  <Link href={`/merchants/${encodeURIComponent(r.merchantId)}`} className="inline-flex min-h-11 items-center underline-offset-4 hover:underline">
                    {r.merchantName}
                  </Link>
                </Td>
                <Td className="text-right font-medium tabular-nums">{formatMinor(r.total_minor, r.currency)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
