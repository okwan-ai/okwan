import Link from "next/link";
import { Suspense } from "react";
import { requireTenant } from "@/lib/guard";
import { digest, storedRuns } from "@/lib/runs";
import { AddMerchant } from "../_components/add-merchant";
import { MerchantsTable } from "../_components/merchants-table";
import { ButtonLink } from "../_components/ui/button";
import { EmptyState } from "../_components/ui/empty-state";
import { IconStore } from "../_components/ui/icons";
import { PageHeader } from "../_components/ui/page-header";

export const metadata = { title: "Merchants" };

export default async function MerchantsPage() {
  await requireTenant();
  // Each merchant with its newest stored run: a read, never a run.
  const merchants = await storedRuns();

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
        actions={<Suspense><AddMerchant variant={merchants && merchants.length === 0 ? "secondary" : "primary"} /></Suspense>}
      />

      {!merchants ? (
        <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>
      ) : merchants.length === 0 ? (
        <EmptyState
          icon={<IconStore />}
          title="No merchants yet"
          benefits={[
            "Each merchant keeps its own rails, keys and plan gate",
            "Connect Shopify, PayPal and Stripe once; every surface reads them",
            "One check says whether each order was paid once, twice or not at all",
          ]}
          action={<ButtonLink href="/merchants?add=1" variant="primary">Add your first merchant</ButtonLink>}
        >
          Add one for each business you serve.
        </EmptyState>
      ) : (
        <MerchantsTable
          rows={merchants.map((r) => ({
            id: r.merchant.tenant.id,
            name: r.merchant.tenant.name,
            createdAt: r.merchant.tenant.created_at,
            ready: r.merchant.ready,
            partial: r.merchant.partial,
            known: r.merchant.known,
            digest: digest(r),
          }))}
        />
      )}
    </>
  );
}
