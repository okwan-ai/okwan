import Link from "next/link";
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

const RANGES = [7, 30, 90] as const;

/** Presets as links, one row above what they scope; the plan meter above
 *  them is month-to-date and never windowed. `href(days)` builds the link. */
export function RangePicker({ days, href }: { days: number; href: (d: number) => string }) {
  return (
    <nav aria-label="Window" className="flex items-center gap-1 rounded-lg bg-canvas p-0.5 text-xs">
      {RANGES.map((d) => (
        <Link
          key={d}
          href={href(d)}
          aria-current={d === days ? "page" : undefined}
          className={`inline-flex min-h-9 items-center rounded-md px-3 font-medium ${d === days ? "bg-surface text-ink shadow-sm" : "text-ink-soft hover:text-ink"}`}
        >
          {d} days
        </Link>
      ))}
    </nav>
  );
}

export function PlanUsage({ usage, names, selfId, rangeHref, scope = "workspace" }: {
  usage: Usage;
  names: Record<string, string>;
  selfId: string;
  rangeHref: (days: number) => string;
  /** Whose buckets these are; the plan is always the paying account's. */
  scope?: "workspace" | "merchant";
}) {
  const days = dailyTotals(usage);
  const windowTotal = days.reduce((n, d) => n + d.total, 0);
  const channels = byChannel(usage);
  const tenants = byTenant(usage, names, selfId);
  return (
    <div className="space-y-8">
      <section aria-label="Plan" className="rounded-xl border border-line bg-surface p-5">
        {scope === "merchant" && <p className="mb-2 text-xs text-ink-soft">The allowance is the workspace&apos;s, shared by every merchant; the figures below are this merchant&apos;s own.</p>}
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
        <div className="mb-1 flex justify-end"><RangePicker days={usage.window.days} href={rangeHref} /></div>
        <UsageChart days={days} title={`Requests per day · last ${usage.window.days} days`} />
      </section>

      <div className={`grid gap-6 rounded-xl border border-line bg-surface p-5 ${scope === "workspace" ? "md:grid-cols-2" : ""}`}>
        <Breakdown title="By channel" rows={channels} empty="No requests in this window." />
        {scope === "workspace" && <Breakdown title="By merchant" rows={tenants} empty="No requests in this window." />}
      </div>

      {scope === "workspace" && (
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
      )}
    </div>
  );
}
