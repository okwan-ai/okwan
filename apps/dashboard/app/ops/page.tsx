import Link from "next/link";
import { ago } from "@/lib/finding";
import { headline, STATUS_FILTERS, type StatusFilter, type TicketRow, tickets } from "@/lib/ops";
import { CONSOLE_DESCRIPTION, CONSOLE_TITLE, ConsoleGate, until } from "../_components/ops/console-gate";
import { Badge, type Tone } from "../_components/ui/badge";
import { PageHeader } from "../_components/ui/page-header";
import { Segmented } from "../_components/ui/segmented";
import { Table, Td, Th } from "../_components/ui/table";

export const metadata = { title: "Console" };

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

const FILTER_LABEL: Record<StatusFilter, string> = { open: "Open", answered: "Answered", resolved: "Resolved", all: "All" };

export default async function ConsolePage({ searchParams }: { searchParams: Promise<{ status?: string }> }) {
  const raw = (await searchParams).status;
  const status: StatusFilter = (STATUS_FILTERS as readonly string[]).includes(raw ?? "") ? (raw as StatusFilter) : "open";
  return (
    <ConsoleGate
      header={(op, lock) => (
        <PageHeader
          title={CONSOLE_TITLE}
          description={CONSOLE_DESCRIPTION}
          meta={<span>Signed in as {op.email} · unlocked until {until(op.unlocked_until!)} · everything here is logged</span>}
          actions={lock}
        />
      )}
    >
      {() => <Inbox status={status} />}
    </ConsoleGate>
  );
}

async function Inbox({ status }: { status: StatusFilter }) {
  const r = await tickets(status);
  if (!r.ok) return <p className="text-ink-soft">The Okwan API didn&apos;t answer ({r.detail}). Try again in a moment.</p>;
  const rows = r.data.data;
  return (
    <section aria-labelledby="inbox-title" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="inbox-title" className="text-base font-semibold">Support inbox</h2>
        <Segmented
          label="Show tickets"
          value={status}
          items={STATUS_FILTERS.map((s) => ({ key: s, label: FILTER_LABEL[s], href: `/ops?status=${s}` }))}
        />
      </div>
      {rows.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line bg-surface px-5 py-10 text-center text-sm text-ink-soft">
          {status === "open" ? "Nothing open. Every request has an answer." : `No ${FILTER_LABEL[status].toLowerCase()} tickets.`}
        </p>
      ) : (
        <>
          <ul className="space-y-2 sm:hidden">
            {rows.map((t) => (
              <li key={t.id}>
                <Link href={`/ops/tickets/${t.id}`} className="block rounded-xl border border-line bg-surface p-4 hover:border-ink/40">
                  <span className="flex flex-wrap gap-1.5">
                    <Badge tone={SEVERITY[t.severity].tone}>{SEVERITY[t.severity].label}</Badge>
                    <Badge tone={STATUS[t.status].tone} symbol={STATUS[t.status].symbol}>{STATUS[t.status].label}</Badge>
                  </span>
                  <span className="mt-2 block font-medium">{headline(t.issue)}</span>
                  <span className="mt-1 block text-xs text-ink-soft">
                    {t.account} · {t.category_label} · <span suppressHydrationWarning>{ago(Date.parse(t.created_at))}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <div className="hidden sm:block">
            <Table label="Support tickets" minWidth={760}>
              <thead>
                <tr>
                  <Th>Severity</Th>
                  <Th>Issue</Th>
                  <Th>Account</Th>
                  <Th>Category</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Raised</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((t) => (
                  <tr key={t.id} className="hover:bg-canvas/60">
                    <Td><Badge tone={SEVERITY[t.severity].tone}>{SEVERITY[t.severity].label}</Badge></Td>
                    <Td className="max-w-md">
                      <Link href={`/ops/tickets/${t.id}`} className="font-medium hover:underline">{headline(t.issue)}</Link>
                      {!t.notified && <span className="block text-xs text-danger">The email to you didn&apos;t go out</span>}
                    </Td>
                    <Td>
                      <span className="block">{t.account}</span>
                      <span className="block text-xs text-ink-soft">{t.contact ?? "no sign-in address"}</span>
                    </Td>
                    <Td className="text-ink-soft">{t.category_label}</Td>
                    <Td><Badge tone={STATUS[t.status].tone} symbol={STATUS[t.status].symbol}>{STATUS[t.status].label}</Badge></Td>
                    <Td className="text-right whitespace-nowrap text-ink-soft"><span suppressHydrationWarning>{ago(Date.parse(t.created_at))}</span></Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
        </>
      )}
    </section>
  );
}
