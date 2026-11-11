import { apiUrl, tenantTree } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { sampleCheck } from "@/lib/sample";
import { SampleCheck } from "../_components/sample-check";
import { EmptyState } from "../_components/ui/empty-state";
import { PageHeader } from "../_components/ui/page-header";

export const metadata = { title: "Sample check" };

/**
 * A new account's first minute (§9 2026-11-11): a finished check before
 * anything is connected. The API runs its real fold over a made-up store's
 * week; this page draws it as it draws a merchant's own. Nothing of the
 * account's is read, stored or metered.
 */
export default async function SamplePage() {
  const me = await requireTenant();
  const [sample, tree] = await Promise.all([sampleCheck(), tenantTree()]);
  const first = tree?.children?.[0];
  return (
    <>
      <PageHeader
        title="Sample check"
        description="A made-up store's week, checked the way your merchants' orders will be."
      />
      {sample ? (
        <SampleCheck
          finding={sample.finding}
          store={sample.store}
          apiBase={apiUrl()}
          account={me.id}
          nextHref={first ? `/merchants/${encodeURIComponent(first.id)}?tab=connections` : "/merchants?add=1"}
          nextLabel={first ? `Connect ${first.name}` : "Add a merchant"}
        />
      ) : (
        <EmptyState title="The sample didn't load">The Okwan API didn&apos;t answer. Try again in a moment.</EmptyState>
      )}
    </>
  );
}
