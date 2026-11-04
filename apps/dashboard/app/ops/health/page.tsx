import Link from "next/link";
import { ago } from "@/lib/finding";
import { type Activity, type Health, type Switch, headline, health } from "@/lib/ops";
import { SeverityBadge } from "../../_components/ops/badges";
import { ConsoleGate, ConsoleHeader } from "../../_components/ops/console-gate";
import { Badge } from "../../_components/ui/badge";
import { StatTile } from "../../_components/ui/stat-tile";
import { Table, Td, Th } from "../../_components/ui/table";

export const metadata = { title: "Health · Console" };

/** The monitor runs on GitHub every 10 minutes and opens an `incident`
 *  issue when a check fails; the API cannot see it, so Health links to it. */
const REPO = "https://github.com/okwan-ai/okwan";
const MONITOR = `${REPO}/actions/workflows/monitor.yml`;
const INCIDENTS = `${REPO}/issues?q=is%3Aissue+is%3Aopen+label%3Aincident`;

const ACTION: Record<string, string> = {
  unlock: "Unlocked the console", unlock_failed: "Entered a wrong code", lock: "Locked the console",
  list_tickets: "Opened the inbox", view_ticket: "Opened a ticket", reply: "Replied to a customer",
  reply_failed: "Reply not sent", set_status: "Changed a ticket's status", list_accounts: "Opened accounts",
  view_account: "Opened an account", view_health: "Opened health", view_log: "Read the log",
  enroll: "Enrolled an authenticator",
};

export default function HealthPage() {
  return (
    <ConsoleGate header={(op, lock) => <ConsoleHeader op={op} lock={lock} active="health" />}>
      {() => <HealthBody />}
    </ConsoleGate>
  );
}

async function HealthBody() {
  const r = await health();
  if (!r.ok) return <p className="text-ink-soft">The Okwan API didn&apos;t answer ({r.detail}). Try again in a moment.</p>;
  const h = r.data;
  const checked = h.runs.ok + h.runs.failed;
  return (
    <div className="space-y-8">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Accounts" value={h.accounts} sub={`${h.merchants} merchant${h.merchants === 1 ? "" : "s"}`} />
        <StatTile label="Open tickets" value={h.open_tickets} sub={h.not_mailed.length ? `${h.not_mailed.length} not mailed to you` : "every escalation mailed"} />
        <StatTile label={`Checks failed, ${h.runs.days} days`} value={h.runs.failed} sub={checked ? `of ${checked} run` : "none run"} />
        <StatTile label="Near their limit" value={h.near_limit.length} sub="accounts at 80% or more" />
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
        <div className="min-w-0 space-y-8">
          <Failures h={h} />
          {h.not_mailed.length > 0 && (
            <section aria-labelledby="unmailed-title" className="space-y-3">
              <h2 id="unmailed-title" className="text-base font-semibold">Escalations that weren&apos;t mailed</h2>
              <p className="text-sm text-ink-soft">The customer was told a person would follow up, but the email to you didn&apos;t go out. Answer them from the inbox.</p>
              <ul className="space-y-2">
                {h.not_mailed.map((t) => (
                  <li key={t.id}>
                    <Link href={`/ops/tickets/${t.id}`} className="block rounded-xl border border-line bg-surface px-4 py-3 hover:border-ink/40">
                      <SeverityBadge severity={t.severity} />
                      <span className="mt-1.5 block font-medium">{headline(t.issue)}</span>
                      <span className="mt-0.5 block text-xs text-ink-soft">{t.account} · <span suppressHydrationWarning>{ago(Date.parse(t.created_at))}</span></span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {h.near_limit.length > 0 && (
            <section aria-labelledby="near-title" className="space-y-3">
              <h2 id="near-title" className="text-base font-semibold">Near their monthly limit</h2>
              <ul className="space-y-2">
                {h.near_limit.map((a) => (
                  <li key={a.id}>
                    <Link href={`/ops/accounts/${a.id}`} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface px-4 py-3 hover:border-ink/40">
                      <span className="font-medium">{a.name}</span>
                      <span className="text-sm text-ink-soft tabular-nums">{a.plan} · {a.used.toLocaleString("en-US")} of {a.limit.toLocaleString("en-US")} ({Math.round((a.used / a.limit) * 100)}%)</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <ActivityLog entries={h.activity} />
        </div>

        <div className="min-w-0 space-y-6">
          <section aria-labelledby="service-title" className="rounded-xl border border-line bg-surface p-5">
            <h2 id="service-title" className="text-base font-semibold">Service</h2>
            <dl className="mt-3 space-y-2 text-sm">
              <Row label="Deployed commit">{h.service.commit ? <code className="font-mono">{h.service.commit}</code> : <span className="text-ink-soft">not on Render</span>}</Row>
              <Row label="Running since"><span suppressHydrationWarning>{ago(Date.parse(h.service.started_at))}</span></Row>
              <Row label="Vault">{h.service.vault.kind === "postgres" ? `Postgres · ${h.service.vault.ms} ms` : "in memory (dev)"}</Row>
            </dl>
            <div className="mt-4 space-y-1 border-t border-line pt-3 text-sm">
              <p className="text-ink-soft">The outside monitor checks every 10 minutes and opens an incident when something fails.</p>
              <a href={MONITOR} target="_blank" rel="noreferrer" className="block font-medium underline underline-offset-4">Monitor runs ↗</a>
              <a href={INCIDENTS} target="_blank" rel="noreferrer" className="block font-medium underline underline-offset-4">Open incidents ↗</a>
            </div>
          </section>
          <section aria-labelledby="switches-title" className="rounded-xl border border-line bg-surface p-5">
            <h2 id="switches-title" className="text-base font-semibold">Switches</h2>
            <p className="mt-1 text-xs text-ink-soft">Whether each setting is on. Values are never shown.</p>
            <ul className="mt-3 space-y-3">
              {h.switches.map((s) => <SwitchRow key={s.name} s={s} />)}
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-ink-soft">{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

function SwitchRow({ s }: { s: Switch }) {
  const badge = s.ok === true ? <Badge tone="ok" symbol="✓">{s.state}</Badge>
    : s.ok === false ? <Badge tone="danger" symbol="!">{s.state}</Badge>
      : <Badge>{s.state}</Badge>;
  return (
    <li className="text-sm">
      <span className="flex items-center justify-between gap-3"><span>{s.name}</span>{badge}</span>
      {s.ok !== true && <span className="mt-0.5 block font-mono text-xs text-ink-soft">{s.needs}</span>}
    </li>
  );
}

function Failures({ h }: { h: Health }) {
  return (
    <section aria-labelledby="failures-title" className="space-y-3">
      <h2 id="failures-title" className="text-base font-semibold">Failed checks, last {h.runs.days} days</h2>
      {h.runs.failures.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line bg-surface px-5 py-6 text-sm text-ink-soft">
          {h.runs.ok ? `None. All ${h.runs.ok} checks ran.` : "No checks have run in this time."}
        </p>
      ) : (
        <Table label="Failed checks" minWidth={600}>
          <thead>
            <tr><Th>Account</Th><Th>Merchant and check</Th><Th>Error</Th><Th className="text-right">When</Th></tr>
          </thead>
          <tbody className="divide-y divide-line">
            {h.runs.failures.map((f) => (
              <tr key={f.id}>
                <Td><Link href={`/ops/accounts/${f.account_id}`} className="font-medium hover:underline">{f.account}</Link></Td>
                <Td>
                  {f.tenant_id === f.account_id ? <span className="text-ink-soft">the account</span> : f.tenant}
                  <span className="block font-mono text-xs text-ink-soft">{f.kind}:{f.name}</span>
                </Td>
                <Td className="min-w-48 text-xs text-danger">{f.error ?? "failed"}</Td>
                <Td className="text-right whitespace-nowrap text-ink-soft"><span suppressHydrationWarning>{ago(Date.parse(f.finished_at))}</span></Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}

function ActivityLog({ entries }: { entries: Activity[] }) {
  return (
    <section aria-labelledby="activity-title" className="space-y-3">
      <h2 id="activity-title" className="text-base font-semibold">Console activity</h2>
      <p className="text-sm text-ink-soft">Everything done in the console, newest first. The database keeps this log and refuses to change it.</p>
      <ol className="divide-y divide-line rounded-xl border border-line bg-surface">
        {entries.map((e, i) => (
          <li key={i} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 px-4 py-2.5 text-sm">
            <span className="min-w-0">
              {ACTION[e.action] ?? e.action}
              {e.target && <span className="ml-2 font-mono text-xs text-ink-soft">{e.target}</span>}
            </span>
            <span className="text-xs text-ink-soft">{e.operator} · <span suppressHydrationWarning>{ago(Date.parse(e.at))}</span></span>
          </li>
        ))}
      </ol>
    </section>
  );
}
