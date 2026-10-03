import { byChannel, byTenant, dailyTotals, today, type Usage } from "@/lib/usage-shape";
import { Breakdown } from "../usage/breakdown";
import { PlanMeter } from "../usage/plan-meter";
import { UsageChart } from "../usage/usage-chart";
import { StatTile } from "../ui/stat-tile";

/** Plans the API knows (okwan_vault/usage.py PLANS). Mirrored here only to
 *  show what exists; nothing sets a plan self-serve yet (§10 item 3). */
const PLANS: [string, string][] = [
  ["Free", "5,000 requests / month"],
  ["Pro", "100,000 requests / month"],
  ["Team", "1,000,000 requests / month"],
  ["Enterprise", "Unmetered"],
];

export function PlanUsage({ usage, names, selfId }: { usage: Usage; names: Record<string, string>; selfId: string }) {
  const days = dailyTotals(usage);
  const windowTotal = days.reduce((n, d) => n + d.total, 0);
  const channels = byChannel(usage);
  const tenants = byTenant(usage, names, selfId);
  return (
    <div className="space-y-8">
      <section aria-label="Plan" className="rounded-xl border border-line bg-surface p-5">
        <PlanMeter plan={usage.plan} />
        <p className="mt-3 text-xs text-ink-soft">
          Every call that reads a rail counts once: REST, SQL, the hosted MCP, and checks run from this dashboard
          (counted to the merchant they run as). Listing tables or reconciliations is free. Connection tests count but are
          never refused. The month resets on the 1st, UTC.
        </p>
      </section>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="This month" value={usage.plan.used.toLocaleString("en-US")} sub="requests, month to date" />
        <StatTile
          label="Remaining"
          value={usage.plan.unmetered ? "∞" : (usage.plan.remaining ?? 0).toLocaleString("en-US")}
          sub={usage.plan.unmetered ? "unmetered plan" : `of ${usage.plan.limit.toLocaleString("en-US")}`}
        />
        <StatTile label="Today" value={today(usage).toLocaleString("en-US")} sub="requests since 00:00 UTC" />
        <StatTile label={`Last ${usage.window.days} days`} value={windowTotal.toLocaleString("en-US")} sub={`${tenants.length} tenant${tenants.length === 1 ? "" : "s"} with traffic`} />
      </div>

      <section className="rounded-xl border border-line bg-surface p-5">
        <UsageChart days={days} title={`Requests per day · last ${usage.window.days} days`} />
      </section>

      <div className="grid gap-6 rounded-xl border border-line bg-surface p-5 md:grid-cols-2">
        <Breakdown title="By channel" rows={channels} empty="No requests in this window." />
        <Breakdown title="By merchant" rows={tenants} empty="No requests in this window." />
      </div>

      <section aria-labelledby="plans-title" className="rounded-xl border border-line bg-surface">
        <div className="border-b border-line px-5 py-3">
          <h3 id="plans-title" className="text-sm font-semibold">Plans</h3>
        </div>
        <dl className="divide-y divide-line">
          {PLANS.map(([name, quota]) => (
            <div key={name} className="flex items-center justify-between gap-4 px-5 py-2.5 text-sm">
              <dt className="flex items-center gap-2 font-medium">
                {name}
                {name.toLowerCase() === usage.plan.name && <span className="rounded-full bg-ink px-2 py-0.5 text-[11px] font-medium text-canvas">Current</span>}
              </dt>
              <dd className="text-ink-soft tabular-nums">{quota}</dd>
            </div>
          ))}
        </dl>
        <p className="px-5 py-3 text-xs text-ink-soft">Changing plan isn&apos;t self-serve yet. Every workspace is on Free until it is.</p>
      </section>
    </div>
  );
}
