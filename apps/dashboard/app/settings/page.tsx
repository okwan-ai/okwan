import { tenantTree } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { SETTINGS_TABS, settingsTabOf } from "@/lib/settings-tabs";
import { myUsage } from "@/lib/usage";
import { DevSnippets } from "../_components/dev-snippets";
import { IssueKey } from "../_components/issue-key";
import { PlanUsage } from "../_components/settings/plan-usage";
import { RevokeKey } from "../_components/settings/revoke-key";
import { CopyButton } from "../_components/ui/copy-button";
import { EmptyState } from "../_components/ui/empty-state";
import { PageHeader, Section } from "../_components/ui/page-header";
import { Tabs } from "../_components/ui/tabs";

export const metadata = { title: "Settings" };

/** What the API holds about the workspace, and nothing it doesn't: there
 *  is no profile, no team, no self-serve plan change. Each tab says so
 *  where it matters rather than draw an empty form. */
export default async function SettingsPage({ searchParams }: { searchParams: Promise<{ tab?: string; days?: string }> }) {
  await requireTenant();
  const [{ tab, days: daysParam }, tree] = await Promise.all([searchParams, tenantTree()]);
  const days = [7, 30, 90].includes(Number(daysParam)) ? Number(daysParam) : 30;
  const active = settingsTabOf(tab);
  if (!tree) return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;

  return (
    <>
      <PageHeader title="Settings" description="Your workspace, its plan and usage, its keys, and how Okwan holds what you connect." />
      <Tabs
        label="Settings sections"
        items={SETTINGS_TABS.map((t) => ({
          href: t.id === "workspace" ? "/settings" : `/settings?tab=${t.id}`,
          label: t.label,
          active: t.id === active,
        }))}
      />
      <div className="pt-6">
        {active === "workspace" && (
          <dl className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
            <Row label="Name">
              {tree.self.name}
              {tree.self.name.includes("@") && <span className="block text-xs text-ink-soft">A self-serve workspace is named by its verified address.</span>}
            </Row>
            <Row label="Workspace id">
              <span className="inline-flex items-center gap-1">
                <code className="font-mono text-[13px]">{tree.self.id}</code>
                <CopyButton value={tree.self.id} label="Copy workspace id" />
              </span>
            </Row>
            <Row label="Created">
              <span suppressHydrationWarning>{new Date(tree.self.created_at).toLocaleDateString("en-US", { dateStyle: "long", timeZone: "UTC" })}</span>
            </Row>
            <Row label="Merchants">{tree.children.length}</Row>
            <Row label="Sign-in">Email and password. Sessions last 7 days; Sign out in the sidebar ends this one.</Row>
          </dl>
        )}

        {active === "plan" && <PlanTab names={Object.fromEntries(tree.children.map((c) => [c.id, c.name]))} selfId={tree.self.id} days={days} />}

        {active === "keys" && (
          <div className="space-y-6">
            <p className="max-w-2xl text-sm text-ink-soft">
              The workspace&apos;s own key reads only rails connected to the workspace itself. A key for a merchant is issued
              from that merchant&apos;s API keys tab and reads only that merchant.
            </p>
            <IssueKey />
            <RevokeKey />
            <Section title="Use a key">
              <DevSnippets scope="workspace" />
            </Section>
          </div>
        )}

        {active === "security" && (
          <dl className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
            <Row label="Credentials">
              Each value is sealed with its own key, wrapped by a master key, and bound to its tenant, connector and field. It is
              written once, never shown again, and a value moved between tenants cannot be opened.
            </Row>
            <Row label="API keys">Stored as a hash and a public prefix. Shown once at issue. Revoked by id, effective on the next request.</Row>
            <Row label="Read-only">
              A reconciliation can only be declared over read operations, the SQL guard refuses anything but a read, and the hosted
              MCP&apos;s tools are read-only. Connector REST mounts each declared operation; the catalog marks the ones that write
              (today, WhatsApp&apos;s two send operations). No surface can move money on a payment rail. A test holds the gate:
              every route outside a named public list must take a key or a session.
            </Row>
            {/* Changes the day results are persisted (OKWAN_PROJECT.md §10). */}
            <Row label="Results">
              A check is read, shown and forgotten. A result is reused for ten minutes in the dashboard server&apos;s memory and
              never written down. Rows are trimmed on the server, so a customer&apos;s fields on a rail record never reach a
              browser.
            </Row>
            <Row label="Errors">
              A failed connection test or run shows the upstream message with every stored value redacted first. A request the API
              rejects as malformed is answered with where and why, never with the value sent.
            </Row>
            <Row label="Metering">
              One count per tenant, hour and surface. No query text, no row content, no per-call log: the meter is the record.
            </Row>
            <Row label="Egress">
              A host you name (a Postgres connection string) must resolve to a public address; loopback, private and cloud-metadata
              addresses are refused before a socket opens, and the checked address is the one dialled. Shopify is reached only at
              its own <code className="font-mono text-[13px]">&lt;store&gt;.myshopify.com</code>.
            </Row>
            <Row label="Isolation">
              A merchant is its own tenant: its vault, its keys, its plan gate. Anything outside your subtree answers as if it did
              not exist.
            </Row>
            <Row label="Sessions">
              A dashboard session administers a tenant and never acts as an API key: data routes and the hosted MCP take only a key
              you chose to issue.
            </Row>
            <Row label="Rate limits">Sign-in, sign-up, verification and connection tests are limited per address and per subject.</Row>
          </dl>
        )}
      </div>
    </>
  );
}

async function PlanTab({ names, selfId, days }: { names: Record<string, string>; selfId: string; days: number }) {
  const usage = await myUsage(days);
  if (!usage) {
    return <EmptyState title="Usage isn't available right now">The Okwan API didn&apos;t answer. Try again in a moment.</EmptyState>;
  }
  return <PlanUsage usage={usage} names={names} selfId={selfId} rangeHref={(d) => `/settings?tab=plan&days=${d}`} />;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 px-5 py-3 text-sm sm:grid-cols-[180px_1fr]">
      <dt className="text-ink-soft">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}
