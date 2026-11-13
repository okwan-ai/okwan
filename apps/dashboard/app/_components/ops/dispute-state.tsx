import type { DisputeRow } from "@/lib/ops";
import { Badge } from "../ui/badge";

/** Where a dispute stands, in words: the days left are the point. */
export function DisputeState({ d }: { d: DisputeRow }) {
  if (d.kind === "warning") return <Badge tone="warn" symbol="!">Flagged by a bank</Badge>;
  if (d.status === "won") return <Badge tone="ok" symbol="✓">Won</Badge>;
  if (d.status === "lost") return <Badge tone="danger" symbol="✕">Lost</Badge>;
  if (d.ended) return <Badge>{d.status.replaceAll("_", " ")}</Badge>;
  if (!d.needs_answer) return <Badge symbol="…">{d.has_evidence ? "Answered, with the bank" : d.status.replaceAll("_", " ")}</Badge>;
  if (d.days_left === null) return <Badge tone="danger" symbol="!">Answer needed</Badge>;
  if (d.days_left < 0) return <Badge tone="danger" symbol="✕">Date passed</Badge>;
  return (
    <Badge tone={d.days_left <= 3 ? "danger" : "warn"} symbol="!">
      {d.days_left === 0 ? "Answer due today" : `Answer due in ${d.days_left} day${d.days_left === 1 ? "" : "s"}`}
    </Badge>
  );
}

export function day(iso: string | null): string {
  if (!iso) return "no date";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}
