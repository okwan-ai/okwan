import Link from "next/link";
import { disputes } from "@/lib/ops";
import { ConsoleGate, ConsoleHeader } from "../../_components/ops/console-gate";
import { DisputeState, day } from "../../_components/ops/dispute-state";
import { Table, Td, Th } from "../../_components/ui/table";

export const metadata = { title: "Disputes · Console" };

export default function DisputesPage() {
  return (
    <ConsoleGate header={(op, lock) => <ConsoleHeader op={op} lock={lock} active="disputes" />}>
      {() => <Disputes />}
    </ConsoleGate>
  );
}

async function Disputes() {
  const r = await disputes();
  if (!r.ok) return <p className="text-ink-soft">The Okwan API didn&apos;t answer ({r.detail}). Try again in a moment.</p>;
  const { activity: act, data: rows } = r.data;
  return (
    <section aria-labelledby="disputes-title" className="space-y-5">
      <div className="rounded-xl border border-line bg-surface p-5">
        <h2 id="disputes-title" className="text-base font-semibold">Okwan&apos;s own payments, last {act.days} days</h2>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          {[
            ["Payments", act.payments.toLocaleString("en-US")],
            ["Disputed", act.disputes.toLocaleString("en-US")],
            ["Flagged by a bank", act.warnings.toLocaleString("en-US")],
            ["Disputed, of payments", act.rate ?? "no payments"],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-ink-soft">{label}</dt>
              <dd className="mt-0.5 text-xl font-semibold tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 text-sm text-ink-soft">{act.said}</p>
      </div>
      {rows.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line bg-surface px-5 py-10 text-center text-sm text-ink-soft">
          No payment has been disputed or flagged.
        </p>
      ) : (
        <>
          <ul className="space-y-2 sm:hidden">
            {rows.map((d) => (
              <li key={d.id}>
                <Link href={`/ops/disputes/${d.id}`} className="block rounded-xl border border-line bg-surface p-4 hover:border-ink/40">
                  <span className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium tabular-nums">{d.amount}</span>
                    <DisputeState d={d} />
                  </span>
                  <span className="mt-2 block text-sm">{d.account?.name ?? "Account not matched"}</span>
                  <span className="block text-xs text-ink-soft">
                    {d.reason?.replaceAll("_", " ") ?? "no reason given"} · opened {day(d.opened_at)}
                    {d.due_by && <> · due {day(d.due_by)}</>}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <div className="hidden sm:block">
            <Table label="Disputes" minWidth={820}>
              <thead>
                <tr>
                  <Th>Where it stands</Th>
                  <Th className="text-right">Amount</Th>
                  <Th>Account</Th>
                  <Th>The bank&apos;s reason</Th>
                  <Th className="text-right">Opened</Th>
                  <Th className="text-right">Answer due</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((d) => (
                  <tr key={d.id} className="hover:bg-canvas/60">
                    <Td className="whitespace-nowrap">
                      <Link href={`/ops/disputes/${d.id}`} className="hover:underline"><DisputeState d={d} /></Link>
                    </Td>
                    <Td className="text-right font-medium tabular-nums">{d.amount}</Td>
                    <Td>
                      {d.account ? (
                        <Link href={`/ops/accounts/${d.account.id}`} className="hover:underline">{d.account.name}</Link>
                      ) : (
                        <span className="text-ink-soft">not matched</span>
                      )}
                    </Td>
                    <Td className="text-ink-soft">{d.reason?.replaceAll("_", " ") ?? "none given"}</Td>
                    <Td className="whitespace-nowrap text-right text-ink-soft">{day(d.opened_at)}</Td>
                    <Td className="whitespace-nowrap text-right">{d.due_by ? day(d.due_by) : <span className="text-ink-soft">none</span>}</Td>
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
