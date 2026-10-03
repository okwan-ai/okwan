import Link from "next/link";
import { notFound } from "next/navigation";
import { api, session, type Tenant } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { ConnectionsGrid } from "../../_components/connections-grid";
import type { ConnectorView } from "../../_components/connector-form";
import { IssueKey } from "../../_components/issue-key";
import { RunReconciliation } from "../../_components/run-reconciliation";

type Connector = Omit<ConnectorView, "stored">;

/**
 * One merchant's Connections and API key. The id goes to the API as given:
 * whether the caller may administer it is the subtree guard's answer, and
 * its 404 (foreign or unknown) becomes this page's 404.
 */
export default async function MerchantPage({ params }: { params: Promise<{ id: string }> }) {
  await requireTenant();
  const { id } = await params;
  const token = await session();
  const [connectors, configured, tenants] = await Promise.all([
    api<Connector[]>("/v1/connectors"),
    api<{ configured: Record<string, string[]> }>(
      `/v1/tenants/${encodeURIComponent(id)}/credentials`,
      { session: token },
    ),
    api<{ children: Tenant[] }>("/v1/tenants", { session: token }),
  ]);
  if (!configured.ok && configured.status === 404) notFound();
  if (!connectors.ok || !configured.ok) {
    return <p className="text-ink-soft">The Okwan API did not answer. Try again in a moment.</p>;
  }
  // Display only: a grandchild is not in the direct list, so it shows its id.
  const name = (tenants.ok && tenants.data.children.find((c) => c.id === id)?.name) || id;

  return (
    <>
      <Link href="/merchants" className="text-sm text-ink-soft hover:text-ink">← Merchants</Link>
      <h1 className="mt-4 font-display text-5xl font-light tracking-tight">{name}</h1>
      <code className="mt-2 block font-mono text-xs text-ink-soft">{id}</code>
      <div className="mt-10">
        <RunReconciliation tenantId={id} />
      </div>

      <h2 className="mt-16 font-display text-3xl font-light tracking-tight">Connections</h2>
      <p className="mt-3 mb-6 max-w-2xl text-ink-soft">
        This merchant&apos;s rail credentials. They go straight to an encrypted vault under this
        merchant and are never shown again. Each test makes one real read from the rail.
      </p>
      <ConnectionsGrid
        tenantId={id}
        tenantKey={id}
        connectors={connectors.data.map((c) => ({ ...c, stored: configured.data.configured[c.name] ?? [] }))}
      />

      <h2 className="mt-16 font-display text-3xl font-light tracking-tight">API key</h2>
      <p className="mt-3 mb-6 max-w-2xl text-ink-soft">
        A key for this merchant reads only this merchant&apos;s rails. Issued once, shown once.
      </p>
      <IssueKey tenantId={id} />
    </>
  );
}
