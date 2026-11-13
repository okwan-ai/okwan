import Link from "next/link";
import { notFound } from "next/navigation";
import { type DisputeDetail, type EvidenceField, dispute, DISPUTE_ID } from "@/lib/ops";
import { ConsoleGate } from "../../../_components/ops/console-gate";
import { DisputeState, day } from "../../../_components/ops/dispute-state";
import { CopyButton } from "../../../_components/ui/copy-button";
import { PageHeader } from "../../../_components/ui/page-header";

export const metadata = { title: "Dispute · Console" };

export default async function DisputePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ConsoleGate>{() => <DisputeBody id={id} />}</ConsoleGate>;
}

async function DisputeBody({ id }: { id: string }) {
  if (!DISPUTE_ID.test(id)) notFound();
  const r = await dispute(id);
  if (!r.ok && r.status === 404) notFound();
  if (!r.ok) return <p className="text-ink-soft">The Okwan API didn&apos;t answer ({r.detail}). Try again in a moment.</p>;
  return <Dispute d={r.data} />;
}

/* One field of Stripe's dispute form: what the record shows, ready to
   copy, and what only a person can add. */
function Field({ name, field, wanted }: { name: string; field: EvidenceField; wanted: boolean }) {
  return (
    <li className="rounded-xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-mono text-[13px] font-medium">{name}</h3>
        <span className="flex items-center gap-2">
          {wanted && <span className="text-xs text-ink-soft">asked for with this reason</span>}
          {field.text && <CopyButton value={field.text} label={`Copy ${name}`} text="Copy" />}
        </span>
      </div>
      {field.text ? (
        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-canvas p-3 font-sans text-sm leading-relaxed">{field.text}</pre>
      ) : (
        <p className="mt-2 text-sm text-ink-soft">Nothing on record.</p>
      )}
      {field.add && (
        <p className="mt-2 text-sm"><span className="font-medium">To check or add:</span> <span className="text-ink-soft">{field.add}</span></p>
      )}
    </li>
  );
}

function Dispute({ d }: { d: DisputeDetail }) {
  const e = d.evidence;
  const wanted = new Set(e.for_this_reason);
  const names = Object.keys(e.fields);
  const ordered = [...names.filter((n) => wanted.has(n)), ...names.filter((n) => !wanted.has(n))];
  return (
    <>
      <PageHeader
        breadcrumb={[{ href: "/ops/disputes", label: "Disputes" }]}
        title={`${d.amount} ${d.kind === "warning" ? "flagged by a bank" : "disputed"}`}
        meta={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <DisputeState d={d} />
            {d.account ? (
              <Link href={`/ops/accounts/${d.account.id}`} className="hover:underline">{d.account.name}</Link>
            ) : (
              <span>{d.matched ? "account deleted" : "not an Okwan payment on record"}</span>
            )}
            <span aria-hidden>·</span>
            <span>opened {day(d.opened_at)}</span>
            <span aria-hidden>·</span>
            <span className="font-mono text-xs">{d.id}</span>
          </span>
        }
      />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
        <section aria-labelledby="evidence-title" className="min-w-0 space-y-3">
          <h2 id="evidence-title" className="text-base font-semibold">What Okwan holds, in the fields of Stripe&apos;s form</h2>
          {e.matched ? (
            <p className="text-sm text-ink-soft">
              Read each one, add what only you know, and enter it on the payment&apos;s page in Stripe. An answer is sent
              once and cannot be changed. {e.characters.toLocaleString("en-US")} characters in all; Stripe takes 150,000.
            </p>
          ) : (
            <p className="rounded-lg border border-line bg-canvas p-3 text-sm">
              This charge is not a payment Okwan has on record, so there is nothing here to send. The Stripe account
              takes payments for other products too: it may be one of theirs. Look the payment up in Stripe.
            </p>
          )}
          {e.matched && (
            <ul className="space-y-3">
              {ordered.map((name) => (
                <Field key={name} name={name} field={e.fields[name]} wanted={wanted.has(name)} />
              ))}
            </ul>
          )}
        </section>
        {/* On a phone the date and the bank's reason come first: they are what to act on. */}
        <aside className="order-first min-w-0 space-y-4 xl:order-none">
          <section aria-labelledby="due-title" className="rounded-xl border border-line bg-surface p-5">
            <h2 id="due-title" className="text-base font-semibold">{d.kind === "warning" ? "Nothing to answer yet" : "Answer by"}</h2>
            {d.kind === "warning" ? (
              <p className="mt-2 text-sm text-ink-soft">
                A bank reported this payment as possible fraud. No money has moved. Refund it in Stripe, or wait: a
                dispute may follow.
              </p>
            ) : (
              <>
                <p className="mt-1 text-2xl font-semibold tabular-nums">{day(d.due_by)}</p>
                <p className="mt-1 text-sm text-ink-soft">
                  {d.submitted
                    ? "An answer has been sent. The bank decides."
                    : d.stage === "ended"
                      ? "The bank has decided."
                      : d.saved
                        ? "Evidence is saved in Stripe but has not been sent. After this date the dispute is lost."
                        : "After this date the dispute is lost."}
                </p>
              </>
            )}
            <a
              href={d.stripe_url}
              target="_blank"
              rel="noreferrer"
              className="mt-4 inline-flex min-h-11 items-center rounded-lg border border-line px-4 text-sm font-medium hover:border-ink/40"
            >
              Open the payment in Stripe
            </a>
            <p className="mt-2 break-all font-mono text-xs text-ink-soft">{d.charge_id}</p>
          </section>
          <section aria-labelledby="reason-title" className="rounded-xl border border-line bg-surface p-5">
            <h2 id="reason-title" className="text-base font-semibold">The bank&apos;s reason</h2>
            <p className="mt-1 font-mono text-[13px]">{e.reason.code ?? "none given"}</p>
            <p className="mt-2 text-sm">{e.reason.says}</p>
            <p className="mt-2 text-sm text-ink-soft"><span className="font-medium text-ink">To show:</span> {e.reason.show}</p>
          </section>
{e.matched && (
                    <section aria-labelledby="attach-title" className="rounded-xl border border-line bg-surface p-5">
              <h2 id="attach-title" className="text-base font-semibold">To attach in Stripe</h2>
              <ul className="mt-2 space-y-2 text-sm">
                {e.attach.map((a) => (
                  <li key={a.field}>
                    <span className="font-mono text-[13px]">{a.field}</span>
                    <span className="block text-ink-soft">{a.what}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-ink-soft">
                {e.policy_version
                  ? `The policy on record for this payment is version ${e.policy_version}.`
                  : "No policy version is on record for this payment."}
              </p>
            </section>
          )}
        </aside>
      </div>
    </>
  );
}
