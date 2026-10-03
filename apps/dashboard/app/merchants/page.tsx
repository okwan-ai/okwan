import Link from "next/link";
import { Suspense } from "react";
import { requireTenant } from "@/lib/guard";
import { digestOf } from "@/lib/finding";
import { merchantsWithRails } from "@/lib/merchants";
import { cachedRun } from "@/lib/runs";
import { AddMerchant } from "../_components/add-merchant";
import { MerchantsTable } from "../_components/merchants-table";
import { EmptyState } from "../_components/ui/empty-state";
import { PageHeader } from "../_components/ui/page-header";

export const metadata = { title: "Merchants" };

export default async function MerchantsPage() {
  await requireTenant();
  const merchants = await merchantsWithRails();

  return (
    <>
      <PageHeader
        title="Merchants"
        description={
          <>
            Each merchant holds its own rails and API keys; nothing crosses between merchants. Rails on your own
            workspace are under <Link href="/connections" className="underline underline-offset-4 hover:text-ink">your own rails</Link>.
          </>
        }
        actions={<Suspense><AddMerchant /></Suspense>}
      />

      {!merchants ? (
        <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>
      ) : merchants.length === 0 ? (
        <EmptyState title="No merchants yet">
          Add one for each business you serve. Its Shopify, Stripe or PayPal credentials are stored against that
          merchant, not your account.
        </EmptyState>
      ) : (
        <MerchantsTable
          serverNow={Date.now()}
          rows={merchants.map((m) => {
            // Listed under this session, so the caller may see it; memory only, never a run.
            const c = cachedRun(m.tenant.id, m.ready);
            return {
              id: m.tenant.id,
              name: m.tenant.name,
              createdAt: m.tenant.created_at,
              ready: m.ready,
              partial: m.partial,
              known: m.known,
              cached: c ? digestOf(m.tenant.id, c.finding, c.at) : null,
            };
          })}
        />
      )}
    </>
  );
}
