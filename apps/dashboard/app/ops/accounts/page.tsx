import Link from "next/link";
import { ago } from "@/lib/finding";
import { accounts, planLine } from "@/lib/ops";
import { RunBadge } from "../../_components/ops/badges";
import { ConsoleGate, ConsoleHeader } from "../../_components/ops/console-gate";
import { Table, Td, Th } from "../../_components/ui/table";

export const metadata = { title: "Accounts · Console" };

export default function AccountsPage() {
  return (
    <ConsoleGate header={(op, lock) => <ConsoleHeader op={op} lock={lock} active="accounts" />}>
      {() => <Accounts />}
    </ConsoleGate>
  );
}

async function Accounts() {
  const r = await accounts();
  if (!r.ok) return <p className="text-ink-soft">The Okwan API didn&apos;t answer ({r.detail}). Try again in a moment.</p>;
  const rows = r.data.data;
  if (rows.length === 0) {
    return <p className="rounded-xl border border-dashed border-line bg-surface px-5 py-10 text-center text-sm text-ink-soft">No accounts yet.</p>;
  }
  return (
    <section aria-labelledby="accounts-title" className="space-y-4">
      <h2 id="accounts-title" className="text-sm text-ink-soft">
        {rows.length} account{rows.length === 1 ? "" : "s"}, newest first. Connections are listed by name; nothing secret is shown.
      </h2>
      <ul className="space-y-2 sm:hidden">
        {rows.map((a) => (
          <li key={a.id}>
            <Link href={`/ops/accounts/${a.id}`} className="block rounded-xl border border-line bg-surface p-4 hover:border-ink/40">
              <span className="block font-medium">{a.name}</span>
              <span className="block text-xs text-ink-soft">{a.contact ?? "no sign-in address"}</span>
              <span className="mt-2 block text-xs text-ink-soft">
                {planLine(a.plan)} · {a.merchants} merchant{a.merchants === 1 ? "" : "s"}
                {a.open_tickets > 0 && <> · <span className="font-medium text-ink">{a.open_tickets} open</span></>}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      <div className="hidden sm:block">
        <Table label="Accounts" minWidth={900}>
          <thead>
            <tr>
              <Th>Account</Th>
              <Th>Plan this month</Th>
              <Th className="text-right">Merchants</Th>
              <Th>Connections</Th>
              <Th className="text-right">Keys</Th>
              <Th>Last check</Th>
              <Th className="text-right">Open tickets</Th>
              <Th className="text-right">Joined</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((a) => (
              <tr key={a.id} className="hover:bg-canvas/60">
                <Td>
                  <Link href={`/ops/accounts/${a.id}`} className="font-medium hover:underline">{a.name}</Link>
                  <span className="block text-xs text-ink-soft">{a.contact ?? "no sign-in address"}</span>
                </Td>
                <Td className="whitespace-nowrap text-ink-soft">{planLine(a.plan)}</Td>
                <Td className="text-right tabular-nums">{a.merchants}</Td>
                <Td className="text-ink-soft">{a.connections.length ? a.connections.join(", ") : "none"}</Td>
                <Td className="text-right tabular-nums">{a.active_keys}</Td>
                <Td className="whitespace-nowrap">
                  <RunBadge run={a.last_run} />
                  {a.last_run && <span className="ml-2 text-xs text-ink-soft" suppressHydrationWarning>{ago(Date.parse(a.last_run.finished_at))}</span>}
                </Td>
                <Td className="text-right tabular-nums">{a.open_tickets > 0 ? <span className="font-medium">{a.open_tickets}</span> : <span className="text-ink-soft">0</span>}</Td>
                <Td className="text-right whitespace-nowrap text-ink-soft"><span suppressHydrationWarning>{ago(Date.parse(a.created_at))}</span></Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </section>
  );
}
