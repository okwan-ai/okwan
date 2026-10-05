import { byChannel, byTenant, dailyTotals, today, type Usage, weeklyTotals } from "@/lib/usage-shape";
import { Breakdown } from "../usage/breakdown";
import { PlanMeter } from "../usage/plan-meter";
import { UsageChart } from "../usage/usage-chart";
import { Badge } from "../ui/badge";
import { Card, CardBody, CardFooter, CardHeader, GRID } from "../ui/card";
import { HowItWorks } from "../ui/how-it-works";
import { Segmented } from "../ui/segmented";
import { StatTile } from "../ui/stat-tile";

/** Plans the API knows (okwan_vault/usage.py PLANS). Mirrored here only to
 *  show what exists; nothing sets a plan self-serve yet (§10 item 3). */
const PLANS: [string, string][] = [
  ["Free", "5,000 requests / month"],
  ["Pro", "100,000 requests / month"],
  ["Team", "1,000,000 requests / month"],
  ["Enterprise", "Unmetered"],
];

/** True of every tier: nothing reads the plan but the quota gate
 *  (okwan_api/auth.py check_quota), and the plans table holds only a name
 *  and a monthly number. The day a feature gates by tier, this list and
 *  that gate change together. */
const INCLUDED = [
  "Every connector",
  "REST, SQL and hosted MCP, all read-only",
  "Unlimited merchants and keys",
  "Reading results and usage is free",
];

const RANGES = [7, 30, 90] as const;

const POLICY =
  "Every call that reads a connection counts once: REST, SQL, the hosted MCP, and checks run from this dashboard " +
  "(counted to the merchant they run as). Listing tables or reconciliations is free. Connection tests count but are " +
  "never refused for quota. The month resets on the 1st, UTC.";

const n = (v: number) => v.toLocaleString("en-US");

/**
 * Plan & usage, for the whole workspace or one merchant (`scope`). The plan
 * is always the workspace's: a merchant's requests count against it. The
 * merchant scope shows that merchant's own chart and channels, with the
 * shared allowance named in its plan card.
 */
export function PlanUsage({ usage, names, selfId, rangeHref, tenantHref, scope = "workspace" }: {
  usage: Usage;
  names: Record<string, string>;
  selfId: string;
  rangeHref: (days: number) => string;
  /** Workspace scope: where a merchant's row in "By merchant" links. */
  tenantHref?: (id: string) => string;
  scope?: "workspace" | "merchant";
}) {
  const days = dailyTotals(usage);
  // Past a month, a day is too thin a column to hit or read; weeks instead.
  const cols = days.length > 31 ? weeklyTotals(days) : days;
  const windowTotal = days.reduce((t, d) => t + d.total, 0);
  const channels = byChannel(usage);
  const tenants = byTenant(usage, names, selfId);
  const merchantsWithRequests = tenants.filter((t) => t.key !== selfId).length;
  const plan = usage.plan;
  const title = `Requests per ${cols === days ? "day" : "week"} · last ${usage.window.days} days`;
  // Set OKWAN_CONTACT_EMAIL on the dashboard to turn the sentence into a
  // mailto; there is no sales desk to promise hours for.
  const contact = process.env.OKWAN_CONTACT_EMAIL?.trim() || null;
  const lastN = (
    <StatTile
      label={`Last ${usage.window.days} days`}
      value={n(windowTotal)}
      sub={scope === "workspace" ? `${merchantsWithRequests} merchant${merchantsWithRequests === 1 ? "" : "s"} with requests` : "this merchant's requests"}
    />
  );
  const todayTile = <StatTile label="Today" value={n(today(usage))} sub="requests since 00:00 UTC" />;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Plan"
          description={scope === "merchant"
            ? `Workspace plan: ${plan.unmetered ? "unmetered" : `${n(plan.used)} of ${n(plan.limit)} used`} · shared by every merchant`
            : undefined}
        />
        <CardBody><PlanMeter plan={plan} /></CardBody>
        {scope === "workspace" && (
          <CardFooter>
            <HowItWorks label="What counts as a request" items={[POLICY]} />
          </CardFooter>
        )}
      </Card>

      {scope === "workspace" ? (
        <div className={GRID.stats}>
          <StatTile label="This month" value={n(plan.used)} sub="requests, month to date" />
          <StatTile
            label="Remaining"
            value={plan.unmetered ? "∞" : n(plan.remaining ?? 0)}
            sub={plan.unmetered ? "unmetered plan" : `of ${n(plan.limit)}`}
          />
          {todayTile}
          {lastN}
        </div>
      ) : (
        <div className={GRID.stats}>{todayTile}{lastN}</div>
      )}

      <Card>
        <CardHeader
          title={title}
          actions={
            <Segmented
              label="Window"
              value={String(usage.window.days)}
              items={RANGES.map((d) => ({ key: String(d), label: `${d} days`, href: rangeHref(d) }))}
            />
          }
        />
        <CardBody><UsageChart days={cols} title={title} captionHidden /></CardBody>
      </Card>

      <div className={`grid gap-6${scope === "workspace" ? " md:grid-cols-2" : ""}`}>
        <Card>
          <CardHeader title="By channel" />
          <CardBody><Breakdown title="By channel" rows={channels} empty="No requests in this window." /></CardBody>
        </Card>
        {scope === "workspace" && (
          <Card>
            <CardHeader title="By merchant" />
            <CardBody>
              <Breakdown
                title="By merchant"
                rows={tenants}
                empty="No requests in this window."
                hrefs={tenantHref ? Object.fromEntries(tenants.filter((t) => t.key !== selfId).map((t) => [t.key, tenantHref(t.key)])) : undefined}
              />
            </CardBody>
          </Card>
        )}
      </div>

      {scope === "workspace" && (
        <Card>
          <CardHeader title="Plans" />
          <dl className="divide-y divide-line">
            {PLANS.map(([name, quota]) => (
              <div key={name} className="flex items-center justify-between gap-4 px-5 py-2.5 text-sm">
                <dt className="flex items-center gap-2 font-medium">
                  {name}
                  {name.toLowerCase() === plan.name && <Badge tone="ok" symbol="✓">Current</Badge>}
                </dt>
                <dd className="text-ink-soft tabular-nums">{quota}</dd>
              </div>
            ))}
          </dl>
          <div className="border-t border-line px-5 py-4">
            <h3 className="text-xs font-medium">What every plan includes</h3>
            <ul className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
              {INCLUDED.map((line) => (
                <li key={line} className="flex gap-2"><span aria-hidden className="font-mono text-ok">✓</span>{line}</li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-ink-soft">
              Plans differ only in the monthly request number.{" "}
              {contact ? (
                <>To change plan, email <a href={`mailto:${contact}?subject=Okwan%20plan`} className="underline underline-offset-4 hover:text-ink">{contact}</a>.</>
              ) : (
                <>Changing plan isn&apos;t self-serve yet; new workspaces start on Free.</>
              )}
            </p>
          </div>
        </Card>
      )}
    </div>
  );
}
