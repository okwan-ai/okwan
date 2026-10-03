"use client";

import type { RunDigest } from "@/lib/finding";
import { pickNewer, useTabResults } from "@/lib/tab-results";
import { owedAmount, RunStatus } from "./merchant-status";

/** The newest result for a merchant: from this tab's store, or one the
 *  server still holds (`initial`, read from memory, never a run). Without
 *  either, the merchant's readiness. */
export function LastResult({ id, ready, known, initial = null, serverNow = null }: {
  id: string;
  ready: string[];
  known: boolean;
  initial?: RunDigest | null;
  /** The server's clock for `initial.at`; rebased before comparing. */
  serverNow?: number | null;
}) {
  const fromTab = useTabResults()[id];
  const d = pickNewer(fromTab, initial, serverNow);
  return (
    <span className="flex flex-wrap items-center gap-2">
      <RunStatus m={{ ready, known }} d={d} />
      {d?.ok && d.twice > 0 && <span className="text-sm font-medium tabular-nums">{owedAmount(d)} owed back</span>}
      {d && !d.ok && d.detail && <span className="block w-full max-w-[260px] text-xs break-words text-danger">{d.detail}</span>}
    </span>
  );
}
