import type { RunBrief, TicketRow } from "@/lib/ops";
import { Badge, type Tone } from "../ui/badge";

const SEVERITY: Record<TicketRow["severity"], { tone: Tone; label: string }> = {
  sev1: { tone: "danger", label: "SEV1 urgent" },
  sev2: { tone: "warn", label: "SEV2" },
  sev3: { tone: "neutral", label: "SEV3" },
};

const STATUS: Record<TicketRow["status"], { tone: Tone; symbol: string; label: string }> = {
  open: { tone: "ink", symbol: "●", label: "Open" },
  answered: { tone: "neutral", symbol: "↩", label: "Answered" },
  resolved: { tone: "ok", symbol: "✓", label: "Resolved" },
};

export function SeverityBadge({ severity }: { severity: TicketRow["severity"] }) {
  return <Badge tone={SEVERITY[severity].tone}>{SEVERITY[severity].label}</Badge>;
}

export function StatusBadge({ status }: { status: TicketRow["status"] }) {
  const s = STATUS[status];
  return <Badge tone={s.tone} symbol={s.symbol}>{s.label}</Badge>;
}

export function RunBadge({ run }: { run: RunBrief | null }) {
  if (!run) return <span className="text-ink-soft">Never run</span>;
  return run.status === "ok"
    ? <Badge tone="ok" symbol="✓">Ran</Badge>
    : <Badge tone="danger" symbol="!">Failed</Badge>;
}
