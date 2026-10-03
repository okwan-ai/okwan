"use client";

import { useState } from "react";
import { atStake, OUTCOME_LABEL, OUTCOME_MARK, OUTCOME_TONE, sentence } from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import type { AttentionRow } from "@/lib/finding";
import { MiniTrail } from "./money-trail";
import { OrderDrawer } from "./order-drawer";
import { Badge } from "./ui/badge";

/** Overview's findings, worst first. Each opens its proof in a drawer, on
 *  this page, from the row already here: no navigation, no run. */
export function AttentionList({ rows, apiBase }: { rows: AttentionRow[]; apiBase: string }) {
  const [proof, setProof] = useState<number | null>(null);
  return (
    <>
      <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
        {rows.map((r, i) => {
          const stake = atStake(r);
          return (
            <li key={`${r.merchantId}-${r.order}-${i}`}>
              <button
                type="button"
                onClick={() => setProof(i)}
                className="flex min-h-11 w-full flex-col gap-1 px-5 py-3 text-left hover:bg-canvas/60 sm:grid sm:grid-cols-[164px_minmax(0,1fr)_150px_120px] sm:items-center sm:gap-3 lg:grid-cols-[164px_minmax(0,1fr)_96px_150px_120px]"
              >
                <span className="flex items-center justify-between gap-3">
                  <Badge tone={OUTCOME_TONE[r.outcome]} symbol={OUTCOME_MARK[r.outcome]}>{OUTCOME_LABEL[r.outcome]}</Badge>
                  <Stake stake={stake} currency={r.currency} className="sm:hidden" />
                </span>
                <span className="text-sm">{sentence(r)}</span>
                <span className="hidden lg:block"><MiniTrail r={r} /></span>
                <span className="truncate text-xs text-ink-soft sm:text-sm">{r.merchantName}</span>
                <Stake stake={stake} currency={r.currency} className="hidden sm:block" />
              </button>
            </li>
          );
        })}
      </ul>
      <OrderDrawer rows={rows} index={proof} onIndex={setProof} onClose={() => setProof(null)} apiBase={apiBase} />
    </>
  );
}

function Stake({ stake, currency, className = "" }: {
  stake: ReturnType<typeof atStake>;
  currency: string | null;
  className?: string;
}) {
  if (!stake) return <span className={className}>—</span>;
  return (
    <span className={`text-right ${className}`}>
      <span className="block text-sm font-semibold tabular-nums">{formatMinor(stake.minor, currency)}</span>
      <span className="block text-[11px] text-ink-soft">{stake.label}</span>
    </span>
  );
}
