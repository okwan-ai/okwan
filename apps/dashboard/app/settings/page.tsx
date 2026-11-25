import Link from "next/link";
import type { ReactNode } from "react";
import { apiUrl, tenantTree, testMode } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { configured, connectors } from "@/lib/merchants";
import { SETTINGS_TABS, settingsTabOf } from "@/lib/settings-tabs";
import { accessFor } from "@/lib/access";
import { notifySettings } from "@/lib/notify";
import { webhookAddresses, type WebhookAddress } from "@/lib/webhooks";
import { billing } from "@/lib/billing";
import { myUsage, usageFor } from "@/lib/usage";
import { ConnectionsGrid } from "../_components/connections-grid";
import { KeyIssuer } from "../_components/issue-key";
import { AccessLog } from "../_components/settings/access-log";
import { BillingCard } from "../_components/settings/billing-card";
import { DailyChecks } from "../_components/settings/daily-checks";
import { PlanUsage } from "../_components/settings/plan-usage";
import { RevokeKey } from "../_components/settings/revoke-key";
import { UsageScope } from "../_components/settings/usage-scope";
import { AddWebhook, WebhookActions } from "../_components/settings/webhooks";
import { Badge } from "../_components/ui/badge";
import { Card, CardBody, CardHeader } from "../_components/ui/card";
import { CopyButton } from "../_components/ui/copy-button";
import { EmptyState } from "../_components/ui/empty-state";
import { KeyValueList, KeyValueRow } from "../_components/ui/key-value";
import { PageHeader, Section } from "../_components/ui/page-header";
import { Tabs } from "../_components/ui/tabs";

export const metadata = { title: "Settings" };

/** What the API holds about the workspace, and nothing it doesn't: there
 *  is no profile and no team; the plan changes through Stripe (Plan tab). The one home for the
 *  workspace's own connections, the plan (per merchant too), every key in
 *  the subtree, and how Okwan handles data (§9 2026-10-05). /key and
 *  /connections redirect here. */
export default async function SettingsPage({ searchParams }: {
  searchParams: Promise<{ tab?: string; days?: string; merchant?: string; upgraded?: string }>;
}) {
  await requireTenant();
  const [{ tab, days: daysParam, merchant, upgraded }, tree, testing] = await Promise.all([searchParams, tenantTree(), testMode()]);
  const days = [7, 30, 90].includes(Number(daysParam)) ? Number(daysParam) : 30;
  const active = settingsTabOf(tab);
  if (!tree) return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  const merchants = tree.children.map((c) => ({ id: c.id, name: c.name }));

  return (
    <>
      <PageHeader title="Settings" description="Your workspace, plan, keys, and how Okwan handles your data." />
      <Tabs
        label="Settings sections"
        items={SETTINGS_TABS.map((t) => ({
          href: t.id === "workspace" ? "/settings" : `/settings?tab=${t.id}`,
          label: t.label,
          short: t.short,
          active: t.id === active,
        }))}
      />
      <div className="pt-6">
        {active === "workspace" && (
          <>
            <KeyValueList label="Workspace">
              <KeyValueRow label="Name">
                {tree.self.name}
                {tree.self.name.includes("@") && <span className="block text-xs text-ink-soft">A self-serve workspace is named by its verified address.</span>}
              </KeyValueRow>
              <KeyValueRow label="Workspace id">
                <span className="-my-2.5 inline-flex items-center gap-1">
                  <code className="font-mono text-[13px]">{tree.self.id}</code>
                  <CopyButton value={tree.self.id} label="Copy workspace id" />
                </span>
              </KeyValueRow>
              <KeyValueRow label="Created">
                <span suppressHydrationWarning>{new Date(tree.self.created_at).toLocaleDateString("en-US", { dateStyle: "long", timeZone: "UTC" })}</span>
              </KeyValueRow>
              <KeyValueRow label="Merchants">{tree.children.length}</KeyValueRow>
              <KeyValueRow label="Sign-in">Email and password. You are signed out after 30 minutes without activity, and 12 hours after signing in; Sign out in the account menu ends it now.</KeyValueRow>
            </KeyValueList>
            {testing ? (
              <Section id="sandbox" title="Your sandbox" description="What Test mode holds, and what it doesn't.">
                <p className="max-w-2xl text-sm text-ink-soft">
                  The sandbox and each merchant you add in it start with a sample café&apos;s week, its batch report and its
                  bank statement, so the check has something to find. It gets no daily check or daily mail, and it
                  connects no rails yet: connect them on your live account. Its requests count against its own allowance,
                  never your plan.
                </p>
              </Section>
            ) : (
              <>
                <Section
                  id="daily-checks"
                  title="Daily checks"
                  description="Okwan checks your connected merchants once a day, and emails you what is new on Open items."
                >
                  <DailyChecksCard accountId={tree.self.id} />
                </Section>
                <Section
                  id="own-connections"
                  title="Your workspace's own connections"
                  description="For accounts your own business collects on. A merchant's connections live on its page."
                >
                  <OwnConnections selfId={tree.self.id} />
                </Section>
              </>
            )}
          </>
        )}

        {active === "plan" && <PlanTab merchants={merchants} selfId={tree.self.id} merchant={merchant ?? null} days={days} upgraded={upgraded === "1"} testing={testing} />}

        {active === "keys" && (
          <div className="space-y-6">
            <Card>
              <CardHeader
                title="Issue a key"
                description={testing
                  ? "In Test mode a key starts okw_test_ and reaches your sandbox and nothing live. It reads over REST, SQL and MCP, and can't change anything unless you give it admin access. It's shown once."
                  : "A key reads one merchant's data, or your workspace's, over REST, SQL and MCP, and can't change anything unless you give it admin access. It's shown once."}
              />
              <CardBody>
                <KeyIssuer tenants={[{ id: null, name: "Your workspace" }, ...merchants]} />
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Revoke a key" />
              <CardBody><RevokeKey /></CardBody>
            </Card>
            <p className="text-xs text-ink-soft">
              Okwan stores only a hash, so keys can&apos;t be listed here. Keep the key id from when you issued it. To use a key
              with an agent, go to <Link href="/agents" className="underline underline-offset-4 hover:text-ink">Agents →</Link>
            </p>
          </div>
        )}

        {active === "webhooks" && <WebhooksTab />}

        {active === "access" && <AccessTab merchants={merchants} selfId={tree.self.id} merchant={merchant ?? null} days={days} />}

        {active === "security" && (
          <KeyValueList label="How Okwan handles your data">
            {SECURITY.map((r) => (
              <KeyValueRow key={r.id} id={r.id} label={r.label}>
                <p className="font-medium">{r.lead}</p>
                <details className="mt-1">
                  <summary className="inline-flex min-h-11 cursor-pointer items-center text-xs text-ink-soft underline-offset-4 hover:underline">Details</summary>
                  <p className="text-ink-soft">{r.text}</p>
                </details>
              </KeyValueRow>
            ))}
          </KeyValueList>
        )}
      </div>
    </>
  );
}

/** The account's webhook addresses (okwan_api/webhooks.py): each with whether
 *  Okwan is sending to it, and what can be done to it; and a form to add
 *  one, which shows the new address's secret once. */
async function WebhooksTab() {
  const addresses = await webhookAddresses();
  if (addresses === null) {
    return <p className="text-sm text-ink-soft">Webhooks aren&apos;t available right now. Try again in a moment.</p>;
  }
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Add an address"
          description="Okwan sends it an event each time an open item of yours or a merchant's opens, has been open your number of days, is cleared by a later run, or is closed or opened again."
        />
        <CardBody>
          <AddWebhook full={addresses.length >= 5} />
        </CardBody>
      </Card>
      {addresses.length === 0 ? (
        <EmptyState title="No webhook addresses yet">
          Add one above, and your system hears of a change on your open items without asking for it.
        </EmptyState>
      ) : (
        <ul className="space-y-4" aria-label="Webhook addresses">
          {addresses.map((a) => <WebhookCard key={a.id} address={a} />)}
        </ul>
      )}
      <p className="text-xs text-ink-soft">
        Each event is signed as Standard Webhooks signs (<code className="font-mono">webhook-id</code>,{" "}
        <code className="font-mono">webhook-timestamp</code>, <code className="font-mono">webhook-signature</code>):
        check the signature with the address&apos;s secret before you trust an event. Its{" "}
        <code className="font-mono">data</code> is the open item, as <code className="font-mono">GET /v1/exceptions</code>{" "}
        lists it. An event that fails is sent again for three days; the last 30 days are at{" "}
        <code className="font-mono">GET /v1/events</code> with any of your keys.{" "}
        <a href={`${apiUrl()}/docs`} target="_blank" rel="noopener" className="underline underline-offset-4 hover:text-ink">
          API reference<span className="sr-only"> (opens in a new tab)</span>
        </a>
      </p>
    </div>
  );
}

function day(iso: string): string {
  return new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) + " UTC";
}

function WebhookCard({ address: a }: { address: WebhookAddress }) {
  const state = !a.enabled
    ? <Badge tone="danger" symbol="■">Off</Badge>
    : a.failing_since
      ? <Badge tone="warn" symbol="!">Failing</Badge>
      : <Badge tone="ok" symbol="●">On</Badge>;
  return (
    <Card as="li">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-5">
        <p className="min-w-0 flex-1 break-all font-mono text-sm">{a.url}</p>
        {state}
      </div>
      <div className="px-5 pt-4">
      <KeyValueList label={`Webhook ${a.id}`}>
        {!a.enabled && (
          <KeyValueRow label="Turned off">
            <span suppressHydrationWarning>{a.disabled_at ? day(a.disabled_at) : "—"}</span>
            {a.disabled_why && <span className="block text-xs text-ink-soft">{a.disabled_why}</span>}
            <span className="block text-xs text-ink-soft">Nothing is sent to it until it is turned back on; what was made meanwhile is at GET /v1/events.</span>
          </KeyValueRow>
        )}
        {a.enabled && a.failing_since && (
          <KeyValueRow label="Failing since">
            <span suppressHydrationWarning>{day(a.failing_since)}</span>
            <span className="block text-xs text-ink-soft">Okwan keeps trying, less and less often; after three days of failures it turns the address off and emails you.</span>
          </KeyValueRow>
        )}
        <KeyValueRow label="Last delivered">
          {a.last_ok_at ? <span suppressHydrationWarning>{day(a.last_ok_at)}</span> : <span className="text-ink-soft">Nothing yet</span>}
        </KeyValueRow>
        <KeyValueRow label="Waiting to be sent">{a.pending}</KeyValueRow>
        <KeyValueRow label="API version">
          <code className="font-mono text-[13px]">{a.api_version}</code>
          <span className="block text-xs text-ink-soft">Each event is in this version, and its body says so in api_version.</span>
        </KeyValueRow>
        <KeyValueRow label="Secret">
          <code className="font-mono text-[13px]">{a.secret_hint}</code>
          <span className="block text-xs text-ink-soft">Only its first characters: it was shown once, when the address was added.</span>
        </KeyValueRow>
        <KeyValueRow label="Added">
          <span suppressHydrationWarning>{day(a.created_at)}</span>
          <span className="block text-xs text-ink-soft">by {a.created_by === "session" ? "a dashboard sign-in" : <code className="font-mono">{a.created_by}</code>}</span>
        </KeyValueRow>
      </KeyValueList>
      </div>
      <div className="px-5 py-4">
        <WebhookActions id={a.id} enabled={a.enabled} />
      </div>
    </Card>
  );
}

/** The same tiles, sheet and pop as a merchant's Connections, for the
 *  workspace itself; ?connect=<name> opens that sheet. No CheckReadiness. */
async function OwnConnections({ selfId }: { selfId: string }) {
  const [catalog, stored] = await Promise.all([connectors(), configured(selfId)]);
  if (!catalog.ok || !stored.ok) {
    return <p className="text-sm text-ink-soft">Connections aren&apos;t available right now. Try again in a moment.</p>;
  }
  return (
    <ConnectionsGrid
      tenantKey="self"
      connectors={catalog.data.map((c) => ({ ...c, stored: stored.data.configured[c.name] ?? [] }))}
    />
  );
}

async function PlanTab({ merchants, selfId, merchant, days, upgraded, testing }: {
  merchants: { id: string; name: string }[];
  selfId: string;
  merchant: string | null;
  days: number;
  upgraded: boolean;
  /** Test mode: the sandbox's allowance; billing is the live account's. */
  testing: boolean;
}) {
  const names = Object.fromEntries(merchants.map((m) => [m.id, m.name]));
  const withDays = (q: string) => (days === 30 ? q : `${q}&days=${days}`);
  // Only a child of this workspace; anything else is not ours to show.
  const known = merchant ? merchants.find((m) => m.id === merchant) : undefined;
  const scope = <div className="mb-6"><UsageScope merchants={merchants} value={known ? known.id : null} days={days} /></div>;

  if (merchant && !known) {
    return (
      <>
        {scope}
        <EmptyState
          title="Usage isn't available for this merchant"
          action={<Link href={withDays("/settings?tab=plan")} className="text-sm font-medium underline underline-offset-4">Whole workspace →</Link>}
        />
      </>
    );
  }

  if (known) {
    const r = await usageFor(known.id, days);
    return (
      <>
        {scope}
        {r.ok ? (
          <PlanUsage
            usage={r.data}
            names={{}}
            selfId={known.id}
            scope="merchant"
            rangeHref={(d) => `/settings?tab=plan&merchant=${encodeURIComponent(known.id)}&days=${d}`}
          />
        ) : (
          <EmptyState title="Usage isn't available right now">The Okwan API didn&apos;t answer. Try again in a moment.</EmptyState>
        )}
      </>
    );
  }

  const [usage, bill] = await Promise.all([myUsage(days), billing()]);
  return (
    <>
      {testing && (
        <p className="mb-6 max-w-2xl text-sm text-ink-soft">
          Your plan and billing are your live account&apos;s: leave Test mode to change them. The sandbox has its own
          allowance, below, which no plan changes.
        </p>
      )}
      {bill && (
        <BillingCard
          enabled={bill.enabled}
          plan={bill.plan}
          price={bill.pro_price_usd}
          notice={bill.notice}
          refundsUrl={bill.refunds_url}
          termsUrl={bill.terms_url}
          canManage={bill.can_manage}
          periodEnd={bill.subscription?.current_period_end ?? null}
          cancelling={bill.subscription?.cancel_at_period_end ?? false}
          pastDue={bill.subscription?.status === "past_due"}
          upgraded={upgraded}
        />
      )}
      {scope}
      {usage ? (
        <PlanUsage
          usage={usage}
          names={names}
          selfId={selfId}
          rangeHref={(d) => `/settings?tab=plan&days=${d}`}
          tenantHref={(id) => `/settings?tab=plan&merchant=${encodeURIComponent(id)}&days=${usage.window.days}`}
        />
      ) : (
        <EmptyState title="Usage isn't available right now">The Okwan API didn&apos;t answer. Try again in a moment.</EmptyState>
      )}
    </>
  );
}

/** Settings › Access log, for the whole workspace or one merchant, like
 *  Plan & usage. Only a child of this workspace may be named; the API's
 *  subtree guard would refuse anything else anyway. */
async function AccessTab({ merchants, selfId, merchant, days }: {
  merchants: { id: string; name: string }[];
  selfId: string;
  merchant: string | null;
  days: number;
}) {
  const known = merchant ? merchants.find((m) => m.id === merchant) : undefined;
  const scope = <div className="mb-6"><UsageScope merchants={merchants} value={known ? known.id : null} days={days} tab="access" /></div>;
  const withDays = (q: string) => (days === 30 ? q : `${q}&days=${days}`);
  if (merchant && !known) {
    return (
      <>
        {scope}
        <EmptyState
          title="The access log isn't available for this merchant"
          action={<Link href={withDays("/settings?tab=access")} className="text-sm font-medium underline underline-offset-4">Whole workspace →</Link>}
        />
      </>
    );
  }
  const r = await accessFor(known ? known.id : selfId, days);
  const base = known ? `/settings?tab=access&merchant=${encodeURIComponent(known.id)}` : "/settings?tab=access";
  return (
    <>
      {scope}
      {r.ok ? (
        <AccessLog
          access={r.data}
          names={known ? {} : Object.fromEntries(merchants.map((m) => [m.id, m.name]))}
          selfId={known ? known.id : selfId}
          rangeHref={(d) => `${base}&days=${d}`}
        />
      ) : (
        <EmptyState title="The access log isn't available right now">The Okwan API didn&apos;t answer. Try again in a moment.</EmptyState>
      )}
    </>
  );
}

/** Security, one row per guarantee: a lead anyone can read, the detail
 *  folded. Every ReadOnlyChip and HowItWorks links here; ids are anchors. */
const SECURITY: { id: string; label: string; lead: string; text: ReactNode }[] = [
  {
    id: "credentials",
    label: "Credentials",
    lead: "Encrypted per value, never shown again.",
    text: "Each value is sealed with its own key, wrapped by a master key, and bound to its merchant (or workspace), connector and field. It is written once, never shown again, and a value moved to another merchant cannot be opened.",
  },
  {
    id: "api-keys",
    label: "API keys",
    lead: "Stored as a hash and a prefix, shown once, revoked by id.",
    text: "Stored as a hash and a public prefix. Shown once at issue. Revoked by id, effective on the next request.",
  },
  {
    id: "read-only",
    label: "Read-only",
    lead: "Okwan can't move money on PayPal, Stripe or Paystack. WhatsApp's two send operations are the only writes, and they're marked.",
    text: "A reconciliation can only be declared over read operations, the SQL guard refuses anything but a read, and the hosted MCP's tools are read-only. Connector REST mounts each declared operation; the catalog marks the ones that write (today, WhatsApp's two send operations). No channel can move money at a payment provider.",
  },
  {
    id: "results",
    label: "Results",
    lead: "Every check is saved as verdicts and amounts, never raw payment records; the latest 50 per merchant are kept.",
    text: "Every run is stored: its summary and, per order, the verdict, the order reference, currency, totals and what each payment provider took. A provider's record is never written, so a customer's fields on it reach neither the database nor a browser. The newest 50 runs per merchant and check are kept; older ones are removed as new ones arrive.",
  },
  {
    id: "errors",
    label: "Errors",
    lead: "Errors are shown with your secrets removed.",
    text: "A failed connection test or run shows the upstream message with the stored values that call could have sent redacted first, cut to 300 characters. A request the API rejects as malformed is answered with where and why, never with the value sent.",
  },
  {
    id: "metering",
    label: "Metering",
    lead: "We count requests, never their contents.",
    text: "One count per merchant (or workspace), hour and channel, for your plan. Who read what is the access log's record, below; neither keeps query text or row content.",
  },
  {
    id: "access-log",
    label: "Access log",
    lead: "Every read is recorded with the key and address it came from, for 90 days.",
    text: (
      <>
        Each read through REST, SQL, the hosted MCP or this dashboard records when, which key (by its prefix) or a dashboard
        session, the IP address and its network, and what was read. Never the key, the query or its results. A key used from a
        network it hasn&apos;t used in 30 days, or reading far more than its busiest hour of the week before, emails your
        account. See <Link href="/settings?tab=access" className="underline underline-offset-4">Access log</Link>.
      </>
    ),
  },
  {
    id: "egress",
    label: "Egress",
    lead: "Okwan only dials public addresses you name, and Shopify only at its own domain.",
    text: (
      <>
        A host you name (a Postgres connection string) must resolve to a public address; loopback, private and cloud-metadata
        addresses are refused before a socket opens, and the checked address is the one dialled. Shopify is reached only at its
        own <code className="font-mono text-[13px]">&lt;store&gt;.myshopify.com</code>.
      </>
    ),
  },
  {
    id: "isolation",
    label: "Isolation",
    lead: "A merchant's key reads only that merchant.",
    text: "A merchant stands on its own: its vault and its keys are its own, and a key for it reads nothing else. The plan allowance is the workspace's, shared by its merchants. Anything outside your subtree answers as if it did not exist.",
  },
  {
    id: "sessions",
    label: "Sessions",
    lead: "A dashboard session never acts as an API key.",
    text: "A dashboard session administers your workspace and never acts as an API key: data routes and the hosted MCP take only a key you chose to issue.",
  },
  {
    id: "rate-limits",
    label: "Rate limits",
    lead: "Sign-in, sign-up, tests and verification are rate-limited.",
    text: "Sign-in and sign-up are limited per address and per account, connection tests per address and per merchant, and verification per address.",
  },
];


/** The account's daily-check choices, read with the page. */
async function DailyChecksCard({ accountId }: { accountId: string }) {
  const settings = await notifySettings(accountId);
  if (!settings) return <p className="text-sm text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  return <DailyChecks initial={settings} />;
}
