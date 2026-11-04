import Link from "next/link";
import { notFound } from "next/navigation";
import { ago } from "@/lib/finding";
import { type AccountDetail, account, headline, planLine, TENANT_ID } from "@/lib/ops";
import { RunBadge, SeverityBadge, StatusBadge } from "../../../_components/ops/badges";
import { ConsoleGate, until } from "../../../_components/ops/console-gate";
import { Badge } from "../../../_components/ui/badge";
import { PageHeader } from "../../../_components/ui/page-header";
import { Table, Td, Th } from "../../../_components/ui/table";

export const metadata = { title: "Account · Console" };

const SURFACE: Record<string, string> = { rest: "REST", mcp: "MCP", dashboard: "Dashboard", sql: "SQL", query: "SQL" };

/** Usage is counted per surface and target ("rest:stripe"): "REST · stripe". */
function surfaceLabel(s: string): string {
  const [head, ...rest] = s.split(":");
  return `${SURFACE[head] ?? head}${rest.length ? ` · ${rest.join(":")}` : ""}`;
}

export default async function AccountPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <ConsoleGate>
      {(op) => <AccountBody id={id} unlockedUntil={until(op.unlocked_until!)} />}
    </ConsoleGate>
  );
}

async function AccountBody({ id, unlockedUntil }: { id: string; unlockedUntil: string }) {
  if (!TENANT_ID.test(id)) notFound();
  const r = await account(id);
  if (!r.ok && r.status === 404) notFound();
  if (!r.ok) return <p className="text-ink-soft">The Okwan API didn&apos;t answer ({r.detail}). Try again in a moment.</p>;
  return <Account a={r.data} unlockedUntil={unlockedUntil} />;
}

function Account({ a, unlockedUntil }: { a: AccountDetail; unlockedUntil: string }) {
  const names = Object.fromEntries(a.members.map((m) => [m.id, m.name]));
  const surfaces = Object.entries(a.usage_by_surface);
  return (
    <>
      <PageHeader
        breadcrumb={[{ href: "/ops/accounts", label: "Accounts" }]}
        title={a.name}
        meta={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span>{a.contact ?? "no sign-in address"}</span>
            <span aria-hidden>·</span>
            <span suppressHydrationWarning>joined {ago(Date.parse(a.created_at))}</span>
            <span aria-hidden>·</span>
            <span className="font-mono text-xs">{a.id}</span>
          </span>
        }
      />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
        <div className="min-w-0 space-y-6">
          <section aria-labelledby="members-title" className="space-y-3">
            <h2 id="members-title" className="text-base font-semibold">Account and merchants</h2>
            <Table label="Account and merchants" minWidth={640}>
              <thead>
                <tr><Th>Name</Th><Th>Connections saved</Th><Th>Last check</Th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {a.members.map((m) => {
                  const saved = Object.entries(m.connections);
                  return (
                    <tr key={m.id}>
                      <Td>
                        <span className="font-medium">{m.name}</span>
                        {m.is_account && <span className="ml-2"><Badge>Account</Badge></span>}
                        <span className="block font-mono text-xs text-ink-soft">{m.id}</span>
                      </Td>
                      <Td className="text-ink-soft">
                        {saved.length === 0 ? "none" : saved.map(([c, fields]) => (
                          <span key={c} className="block"><span className="text-ink">{c}</span> <span className="text-xs">({fields.join(", ")})</span></span>
                        ))}
                      </Td>
                      <Td>
                        <RunBadge run={m.last_run} />
                        {m.last_run && <span className="ml-2 text-xs text-ink-soft" suppressHydrationWarning>{ago(Date.parse(m.last_run.finished_at))}</span>}
                        {m.last_run?.error && <span className="mt-1 block text-xs text-danger">{m.last_run.error}</span>}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
            <p className="text-xs text-ink-soft">Connections show which credential fields are saved, never their values.</p>
          </section>

          <section aria-labelledby="runs-title" className="space-y-3">
            <h2 id="runs-title" className="text-base font-semibold">Recent checks</h2>
            {a.runs.length === 0 ? (
              <p className="rounded-xl border border-dashed border-line bg-surface px-5 py-6 text-sm text-ink-soft">No checks have run yet.</p>
            ) : (
              <Table label="Recent checks" minWidth={640}>
                <thead>
                  <tr><Th>Result</Th><Th>Merchant</Th><Th>Check</Th><Th>From</Th><Th className="text-right">When</Th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {a.runs.map((r) => (
                    <tr key={r.id}>
                      <Td><RunBadge run={r} />{r.error && <span className="mt-1 block max-w-xs text-xs text-danger">{r.error}</span>}</Td>
                      <Td>{names[r.tenant_id] ?? r.tenant_id}</Td>
                      <Td className="font-mono text-xs">{r.kind}:{r.name}</Td>
                      <Td className="text-ink-soft">{SURFACE[r.surface] ?? r.surface}</Td>
                      <Td className="text-right whitespace-nowrap text-ink-soft"><span suppressHydrationWarning>{ago(Date.parse(r.finished_at))}</span></Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </section>
        </div>

        <div className="min-w-0 space-y-6">
          <section aria-labelledby="plan-title" className="rounded-xl border border-line bg-surface p-5">
            <h2 id="plan-title" className="text-base font-semibold">Plan this month</h2>
            <p className="mt-2 text-sm">{planLine(a.plan)}</p>
            {surfaces.length > 0 && (
              <dl className="mt-3 space-y-1 text-sm">
                {surfaces.map(([s, n]) => (
                  <div key={s} className="flex justify-between gap-4">
                    <dt className="text-ink-soft">{surfaceLabel(s)}</dt>
                    <dd className="tabular-nums">{n.toLocaleString("en-US")}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>

          <section aria-labelledby="keys-title" className="rounded-xl border border-line bg-surface p-5">
            <h2 id="keys-title" className="text-base font-semibold">API keys</h2>
            {a.keys.length === 0 ? (
              <p className="mt-2 text-sm text-ink-soft">No keys issued.</p>
            ) : (
              <ul className="mt-3 space-y-2 text-sm">
                {a.keys.map((k) => (
                  <li key={k.id} className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      <span className="font-mono">{k.prefix}…</span>
                      <span className="block text-xs text-ink-soft">{names[k.tenant_id] ?? k.tenant_id} · <span suppressHydrationWarning>issued {ago(Date.parse(k.issued))}</span></span>
                    </span>
                    {k.revoked ? <Badge>Revoked</Badge> : <Badge tone="ok" symbol="✓">Active</Badge>}
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-3 text-xs text-ink-soft">Prefixes only. Okwan stores a hash, never the key.</p>
          </section>

          <section aria-labelledby="tickets-title" className="rounded-xl border border-line bg-surface p-5">
            <h2 id="tickets-title" className="text-base font-semibold">Support tickets</h2>
            {a.tickets.length === 0 ? (
              <p className="mt-2 text-sm text-ink-soft">None.</p>
            ) : (
              <ul className="mt-3 space-y-3">
                {a.tickets.map((t) => (
                  <li key={t.id}>
                    <Link href={`/ops/tickets/${t.id}`} className="block rounded-lg hover:bg-canvas/60">
                      <span className="flex flex-wrap gap-1.5"><SeverityBadge severity={t.severity} /><StatusBadge status={t.status} /></span>
                      <span className="mt-1 block text-sm font-medium">{headline(t.issue)}</span>
                      <span className="block text-xs text-ink-soft" suppressHydrationWarning>{ago(Date.parse(t.created_at))}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
      <p className="mt-8 text-xs text-ink-soft">Unlocked until {unlockedUntil}. Opening this account was logged.</p>
    </>
  );
}
