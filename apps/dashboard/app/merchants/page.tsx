import Link from "next/link";
import { requireTenant } from "@/lib/guard";
import { merchantsWithRails } from "@/lib/merchants";
import { AddMerchant } from "../_components/add-merchant";
import { RailChips } from "../_components/rail-chips";

export default async function MerchantsPage() {
  await requireTenant();
  const merchants = await merchantsWithRails();
  if (!merchants) {
    return <p className="text-ink-soft">The Okwan API did not answer. Try again in a moment.</p>;
  }

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
          {merchants.map((m) => (
            <li key={m.tenant.id}>
              <Link href={`/merchants/${encodeURIComponent(m.tenant.id)}`} className="card block p-6 hover:border-ink">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 className="font-display text-2xl">{m.tenant.name}</h2>
                  <span className="text-xs text-ink-soft">
                    Added {new Date(m.tenant.created_at).toLocaleDateString("en-US", { dateStyle: "medium" })}
                  </span>
                </div>
                <code className="mt-1 block font-mono text-xs text-ink-soft">{m.tenant.id}</code>
                <div className="mt-4"><RailChips m={m} /></div>
              </Link>
            </li>
          ))}
        </ul>
      )}

      <AddMerchant />
    </>
  );
}
