import Link from "next/link";
import { notFound } from "next/navigation";
import { ago } from "@/lib/finding";
import { headline, TICKET_ID, ticket, type Line, type TicketDetail } from "@/lib/ops";
import { ReplyEditor } from "../../../_components/ops/console-actions";
import { ConsoleGate, until } from "../../../_components/ops/console-gate";
import { Badge } from "../../../_components/ui/badge";
import { PageHeader } from "../../../_components/ui/page-header";
import { Rich } from "../../../_components/ui/rich-text";

export const metadata = { title: "Ticket · Console" };

export default async function TicketPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <ConsoleGate>
      {(op) => <TicketBody id={id} unlockedUntil={until(op.unlocked_until!)} />}
    </ConsoleGate>
  );
}

async function TicketBody({ id, unlockedUntil }: { id: string; unlockedUntil: string }) {
  if (!TICKET_ID.test(id)) notFound();
  const r = await ticket(id);
  if (!r.ok && r.status === 404) notFound();
  if (!r.ok) return <p className="text-ink-soft">The Okwan API didn&apos;t answer ({r.detail}). Try again in a moment.</p>;
  return <Ticket t={r.data} unlockedUntil={unlockedUntil} />;
}

const STATUS_LABEL = { open: "Open", answered: "Answered", resolved: "Resolved" } as const;

function Ticket({ t, unlockedUntil }: { t: TicketDetail; unlockedUntil: string }) {
  const title = headline(t.issue);
  const sections: [string, string | undefined][] = [
    // The title was cut: the full text, as the assistant wrote it.
    ["The request", title === t.issue ? undefined : t.issue],
    ["What it checked", t.brief.findings],
    ["Likely cause", t.brief.likely_cause],
    ["How to resolve it", t.brief.resolution],
  ];
  return (
    <>
      <PageHeader
        breadcrumb={[{ href: "/ops", label: "Console" }]}
        title={title}
        meta={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <Badge tone={t.severity === "sev1" ? "danger" : t.severity === "sev2" ? "warn" : "neutral"}>{t.severity.toUpperCase()}</Badge>
            <Badge tone={t.status === "resolved" ? "ok" : t.status === "open" ? "ink" : "neutral"}>{STATUS_LABEL[t.status]}</Badge>
            <span>{t.category_label}</span>
            <span aria-hidden>·</span>
            <Link href={`/ops/accounts/${t.tenant_id}`} className="hover:text-ink hover:underline">{t.account}</Link>
            <span aria-hidden>·</span>
            <span suppressHydrationWarning>raised {ago(Date.parse(t.created_at))}</span>
            <span aria-hidden>·</span>
            <span className="font-mono text-xs">{t.id}</span>
          </span>
        }
      />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
        <div className="min-w-0 space-y-6">
          <section aria-labelledby="brief-title" className="rounded-xl border border-line bg-surface p-5 sm:p-6">
            <h2 id="brief-title" className="text-base font-semibold">The assistant&apos;s brief</h2>
            {!t.brief.findings && (
              <p className="mt-2 text-sm text-ink-soft">This ticket was raised before briefs existed. Read the conversation for the rest.</p>
            )}
            {sections.some(([, v]) => v) && (
              <dl className="mt-4 space-y-5">
                {sections.filter(([, v]) => v).map(([label, value]) => (
                  <div key={label}>
                    <dt className="text-xs font-medium tracking-wide text-ink-soft uppercase">{label}</dt>
                    <dd className="mt-1.5 text-sm leading-relaxed whitespace-pre-wrap">{value}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
          <ReplyEditor ticketId={t.id} draft={t.brief.draft_reply ?? ""} contact={t.contact} status={t.status} />
          {t.replies.length > 0 && (
            <section aria-labelledby="sent-title" className="rounded-xl border border-line bg-surface p-5 sm:p-6">
              <h2 id="sent-title" className="text-base font-semibold">Replies</h2>
              <ol className="mt-4 space-y-4">
                {t.replies.map((r) => (
                  <li key={r.id} className="border-l-2 border-ink/20 pl-4">
                    <p className="text-xs text-ink-soft">
                      {r.operator} · <span suppressHydrationWarning>{ago(Date.parse(r.created_at))}</span>
                      {!r.sent && <span className="text-danger"> · not sent</span>}
                    </p>
                    <p className="mt-1 text-sm leading-relaxed whitespace-pre-wrap">{r.body}</p>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </div>
        <Conversation lines={t.conversation} />
      </div>
      <p className="mt-8 text-xs text-ink-soft">Unlocked until {unlockedUntil}. Opening this ticket was logged.</p>
    </>
  );
}

function Conversation({ lines }: { lines: Line[] }) {
  return (
    <section aria-labelledby="conv-title" className="h-fit min-w-0 rounded-xl border border-line bg-surface p-5 sm:p-6">
      <h2 id="conv-title" className="text-base font-semibold">The conversation</h2>
      <p className="mt-1 text-xs text-ink-soft">Anything that looked like a secret was removed before it was stored.</p>
      {lines.length === 0 ? (
        <p className="mt-4 text-sm text-ink-soft">No conversation is kept for this ticket (they are kept 30 days).</p>
      ) : (
        <ol className="mt-4 space-y-3">
          {lines.map((l, i) =>
            l.from === "tool" ? (
              <li key={i} className="text-xs text-ink-soft">
                <span className="rounded-full border border-line bg-canvas px-2 py-0.5 font-mono">ran {l.text}</span>
              </li>
            ) : l.from === "note" ? (
              <li key={i} className="text-xs text-ink-soft italic">{l.text}</li>
            ) : (
              <li key={i} className={l.from === "customer" ? "flex justify-end" : "flex"}>
                {l.from === "customer" ? (
                  <p className="max-w-[85%] rounded-2xl rounded-br-md bg-ink px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap text-white">
                    <span className="sr-only">Customer: </span>
                    {l.text}
                  </p>
                ) : (
                  <div className="max-w-[90%] rounded-2xl rounded-bl-md border border-line bg-surface px-3.5 py-2.5 text-sm leading-relaxed text-ink">
                    <span className="sr-only">Assistant: </span>
                    <Rich text={l.text} />
                  </div>
                )}
              </li>
            ),
          )}
        </ol>
      )}
    </section>
  );
}
