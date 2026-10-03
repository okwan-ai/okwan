import Link from "next/link";
import { Suspense } from "react";
import { requireTenant } from "@/lib/guard";
import { merchantsWithRails } from "@/lib/merchants";
import { AddMerchant } from "../_components/add-merchant";
import { LastResult } from "../_components/last-result";
import { Readiness } from "../_components/merchant-status";
import { RailChips } from "../_components/rail-chips";
import { EmptyState } from "../_components/ui/empty-state";
import { PageHeader } from "../_components/ui/page-header";
import { Table, Td, Th } from "../_components/ui/table";

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
        <Table label="Merchants" minWidth={760}>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>ID</Th>
              <Th>Rails</Th>
              <Th>Status</Th>
              <Th>Last result</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {merchants.map((m) => (
              <tr key={m.tenant.id} className="hover:bg-canvas/60">
                <Td>
                  <Link href={`/merchants/${encodeURIComponent(m.tenant.id)}`} className="font-medium underline-offset-4 hover:underline">
                    {m.tenant.name}
                  </Link>
                  <span className="block text-xs text-ink-soft">
                    Added {new Date(m.tenant.created_at).toLocaleDateString("en-US", { dateStyle: "medium" })}
                  </span>
                </Td>
                <Td><code className="font-mono text-xs text-ink-soft">{m.tenant.id}</code></Td>
                <Td><RailChips ready={m.ready} partial={m.partial} known={m.known} /></Td>
                <Td><Readiness m={m} /></Td>
                <Td><LastResult id={m.tenant.id} ready={m.ready} known={m.known} /></Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}
