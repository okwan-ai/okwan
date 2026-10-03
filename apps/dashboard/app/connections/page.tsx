import Link from "next/link";
import { requireTenant } from "@/lib/guard";
import { configured, connectors } from "@/lib/merchants";
import { ConnectionsGrid } from "../_components/connections-grid";
import { PageHeader } from "../_components/ui/page-header";

/** The signed-in workspace's own rails. A merchant's live on its own page. */
export default async function ConnectionsPage() {
  const tenant = await requireTenant();
  const [catalog, stored] = await Promise.all([connectors(), configured(tenant.id)]);
  if (!catalog.ok || !stored.ok) {
    return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  }
  return (
    <>
      <PageHeader
        title="Your own rails"
        description={
          <>
            Rails connected to your workspace itself. A merchant you serve keeps its rails on its own page under{" "}
            <Link href="/merchants" className="underline underline-offset-4 hover:text-ink">Merchants</Link>.
            Credentials go to an encrypted vault and are never shown again.
          </>
        }
      />
      <ConnectionsGrid
        tenantKey="self"
        connectors={catalog.data.map((c) => ({ ...c, stored: stored.data.configured[c.name] ?? [] }))}
      />
    </>
  );
}
