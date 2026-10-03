import type { RunDigest } from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import { Badge } from "./ui/badge";

type Rails = { ready: string[]; known: boolean };

/** Whether a merchant can be checked at all: the fold needs two rails. */
export function Readiness({ m }: { m: Rails }) {
  if (!m.known) return <Badge symbol="?">Unavailable</Badge>;
  if (m.ready.length >= 2) return <Badge tone="ok" symbol="●">Ready</Badge>;
  return <Badge symbol="○">Needs rails</Badge>;
}

/** The outcome of the last run, or readiness when there is none. */
export function RunStatus({ m, d }: { m: Rails; d: RunDigest | null | undefined }) {
  if (!d) return <Readiness m={m} />;
  if (!d.ok) return <Badge tone="danger" symbol="!" title={d.detail}>Couldn&apos;t run</Badge>;
  if (d.twice > 0) return <Badge tone="danger" symbol="×2">{d.open} to review</Badge>;
  if (d.open > 0) return <Badge tone="warn" symbol="!">{d.open} to review</Badge>;
  return <Badge tone="ok" symbol="✓">All paid once</Badge>;
}

export function twiceAmount(d: RunDigest | null | undefined): string {
  if (!d || !d.ok) return "—";
  if (d.twice === 0) return "None";
  return d.currency ? formatMinor(d.twiceMinor, d.currency) : `${d.twice} order${d.twice === 1 ? "" : "s"}`;
}
