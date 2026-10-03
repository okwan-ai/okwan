"use client";

import { useTabResults } from "@/lib/tab-results";
import { RunStatus, twiceAmount } from "./merchant-status";

/** The last run this tab saw for a merchant. Results aren't persisted, so
 *  before Overview, Findings or the merchant's Run button, there is none. */
export function LastResult({ id, ready, known }: { id: string; ready: string[]; known: boolean }) {
  const d = useTabResults()[id];
  if (!d) return <span className="text-xs text-ink-soft">Not run this session</span>;
  return (
    <span className="flex flex-wrap items-center gap-2">
      <RunStatus m={{ ready, known }} d={d} />
      {d.ok && d.twice > 0 && <span className="text-sm font-medium tabular-nums">{twiceAmount(d)}</span>}
    </span>
  );
}
