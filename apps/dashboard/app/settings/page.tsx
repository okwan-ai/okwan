import Link from "next/link";
import type { ReactNode } from "react";
import { tenantTree } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { configured, connectors } from "@/lib/merchants";
import { SETTINGS_TABS, settingsTabOf } from "@/lib/settings-tabs";
import { myUsage, usageFor } from "@/lib/usage";
import { ConnectionsGrid } from "../_components/connections-grid";
import { KeyIssuer } from "../_components/issue-key";
import { PlanUsage } from "../_components/settings/plan-usage";
import { RevokeKey } from "../_components/settings/revoke-key";
import { UsageScope } from "../_components/settings/usage-scope";
import { Card, CardBody, CardHeader } from "../_components/ui/card";
import { CopyButton } from "../_components/ui/copy-button";
import { EmptyState } from "../_components/ui/empty-state";
import { KeyValueList, KeyValueRow } from "../_components/ui/key-value";
import { PageHeader, Section } from "../_components/ui/page-header";
import { Tabs } from "../_components/ui/tabs";

export const metadata = { title: "Settings" };

/** What the API holds about the workspace, and nothing it doesn't: there
 *  is no profile, no team, no self-serve plan change. The one home for the
 *  workspace's own connections, the plan (per merchant too), every key in
 *  the subtree, and how Okwan handles data (§9 2026-10-05). /key and
 *  /connections redirect here. */
export default async function SettingsPage({ searchParams }: {
  searchParams: Promise<{ tab?: string; days?: string; merchant?: string }>;
}) {
  await requireTenant();
  const [{ tab, days: daysParam, merchant }, tree] = await Promise.all([searchParams, tenantTree()]);
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
              <KeyValueRow label="Sign-in">Email and password. Sessions last 7 days; Sign out at the bottom of the sidebar ends this one.</KeyValueRow>
            </KeyValueList>
            <Section
              id="own-connections"
              title="Your workspace's own connections"
              description="For accounts your own business collects on. A merchant's connections live on its page."
            >
              <OwnConnections selfId={tree.self.id} />
            </Section>
          </>
        )}

        {active === "plan" && <PlanTab merchants={merchants} selfId={tree.self.id} merchant={merchant ?? null} days={days} />}

        {active === "keys" && (
          <div className="space-y-6">
            <Card>
              <CardHeader
                title="Issue a key"
                description="A key reads one merchant's data, or your workspace's, over REST, SQL and MCP. It's shown once."
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

async function PlanTab({ merchants, selfId, merchant, days }: {
  merchants: { id: string; name: string }[];
  selfId: string;
  merchant: string | null;
  days: number;
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

  const usage = await myUsage(days);
  return (
    <>
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

/** Security, one row per guarantee: a lead anyone can read, the detail
 *  folded. Every ReadOnlyChip and HowItWorks links here; ids are anchors. */
const SECURITY: { id: string; label: string; lead: string; text: ReactNode }[] = [
  {
    id: "credentials",
    label: "Credentials",
    lead: "Encrypted per value, never shown again.",
    text: "Each value is sealed with its own key, wrapped by a master key, and bound to its tenant, connector and field. It is written once, never shown again, and a value moved between tenants cannot be opened.",
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
    text: "A reconciliation can only be declared over read operations, the SQL guard refuses anything but a read, and the hosted MCP's tools are read-only. Connector REST mounts each declared operation; the catalog marks the ones that write (today, WhatsApp's two send operations). No channel can move money on a payment rail.",
  },
  {
    id: "results",
    label: "Results",
    lead: "Every check is saved as verdicts and amounts, never raw payment records; the latest 50 per merchant are kept.",
    text: "Every run is stored: its summary and, per order, the verdict, the order reference, currency, totals and what each rail took. A rail record is never written, so a customer's fields on it reach neither the database nor a browser. The newest 50 runs per merchant and check are kept; older ones are removed as new ones arrive.",
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
    text: "One count per tenant, hour and channel. No query text, no row content, no per-call log: the meter is the record.",
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
    text: "A merchant is its own tenant: its vault and its keys are its own, and a key for it reads nothing else. The plan allowance is the workspace's, shared by its merchants. Anything outside your subtree answers as if it did not exist.",
  },
  {
    id: "sessions",
    label: "Sessions",
    lead: "A dashboard session never acts as an API key.",
    text: "A dashboard session administers a tenant and never acts as an API key: data routes and the hosted MCP take only a key you chose to issue.",
  },
  {
    id: "rate-limits",
    label: "Rate limits",
    lead: "Sign-in, sign-up, tests and verification are rate-limited.",
    text: "Sign-in and sign-up are limited per address and per account, connection tests per address and per tenant, and verification per address.",
  },
];
