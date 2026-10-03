import Link from "next/link";
import { api, session, type Tenant } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { AddMerchant } from "../_components/add-merchant";

type Connector = { name: string; credential_fields: string[] };
type Configured = { configured: Record<string, string[]> };

export default async function MerchantsPage() {
  await requireTenant();
  const token = await session();
  const [tenants, connectors] = await Promise.all([
    api<{ self: Tenant; children: Tenant[] }>("/v1/tenants", { session: token }),
    api<Connector[]>("/v1/connectors"),
  ]);
  if (!tenants.ok || !connectors.ok) {
    return <p className="text-ink-soft">The Okwan API did not answer. Try again in a moment.</p>;
  }
  const merchants = tenants.data.children;
  const stored = await Promise.all(
    merchants.map((m) =>
      api<Configured>(`/v1/tenants/${encodeURIComponent(m.id)}/credentials`, { session: token }),
    ),
  );

  return (
    <>
      <h1 className="font-display text-5xl font-light tracking-tight">Merchants</h1>
      <p className="mt-4 mb-10 max-w-2xl text-ink-soft">
        Each merchant holds its own rail credentials and its own API keys. Your agents query a
        merchant with that merchant&apos;s key, and nothing crosses between merchants.
      </p>

      {merchants.length === 0 ? (
        <div className="card mb-6 p-8">
          <p className="font-display text-2xl">No merchants yet.</p>
          <p className="mt-3 max-w-xl text-ink-soft">
            Add one for each business you serve. Its PayPal, Shopify or Stripe credentials are stored
            against that merchant, not against your account, so your own Connections page stays
            empty until you connect rails of your own.
          </p>
        </div>
      ) : (
        <ul className="mb-6 grid gap-4">
          {merchants.map((m, i) => {
            const s = stored[i];
            const fields = s.ok ? s.data.configured : {};
            const ready = connectors.data.filter((c) =>
              c.credential_fields.every((f) => fields[c.name]?.includes(f)),
            );
            const partial = connectors.data.filter(
              (c) => fields[c.name]?.length && !ready.includes(c),
            );
            return (
              <li key={m.id}>
                <Link href={`/merchants/${encodeURIComponent(m.id)}`} className="card block p-6 hover:border-ink">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <h2 className="font-display text-2xl">{m.name}</h2>
                    <span className="text-xs text-ink-soft">
                      Added {new Date(m.created_at).toLocaleDateString("en-US", { dateStyle: "medium" })}
                    </span>
                  </div>
                  <code className="mt-1 block font-mono text-xs text-ink-soft">{m.id}</code>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {!s.ok && <span className="text-xs text-ink-soft">Connections unavailable</span>}
                    {s.ok && ready.length + partial.length === 0 && (
                      <span className="text-xs text-ink-soft">No connectors configured</span>
                    )}
                    {ready.map((c) => (
                      <span key={c.name} className="rounded-full bg-ink px-3 py-1 text-xs font-medium capitalize text-canvas">
                        {c.name}
                      </span>
                    ))}
                    {partial.map((c) => (
                      <span key={c.name} className="rounded-full border border-line px-3 py-1 text-xs capitalize text-ink-soft">
                        {c.name} · partial
                      </span>
                    ))}
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      <AddMerchant />
    </>
  );
}
