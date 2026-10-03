import Link from "next/link";
import { api, session } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { ConnectorCard, type ConnectorView } from "../_components/connector-card";

type Connector = Omit<ConnectorView, "stored">;

export default async function ConnectionsPage() {
  const tenant = await requireTenant();
  const token = await session();
  const [connectors, configured] = await Promise.all([
    api<Connector[]>("/v1/connectors"),
    api<{ configured: Record<string, string[]> }>(
      `/v1/tenants/${tenant.id}/credentials`,
      { session: token },
    ),
  ]);
  if (!connectors.ok || !configured.ok) {
    return <p className="text-ink-soft">The Okwan API did not answer. Try again in a moment.</p>;
  }

  return (
    <>
      <h1 className="font-display text-5xl font-light tracking-tight">Connections</h1>
      <p className="mt-4 mb-10 max-w-2xl text-ink-soft">
        Credentials go straight to an encrypted vault and are never shown again. Each test makes one
        real read from the rail, so you see rows rather than a promise. These are your own rails; a
        merchant you serve keeps its rails under{" "}
        <Link href="/merchants" className="underline decoration-volt-deep underline-offset-4 hover:text-ink">
          Merchants
        </Link>
        .
      </p>
      <div className="grid gap-6">
        {connectors.data.map((c) => (
          <ConnectorCard key={c.name} c={{ ...c, stored: configured.data.configured[c.name] ?? [] }} />
        ))}
      </div>
    </>
  );
}
