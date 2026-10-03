import { apiUrl } from "@/lib/api";
import { configured, connectors } from "@/lib/merchants";
import { tabOf } from "@/lib/merchant-tabs";
import { ConnectionsGrid } from "../../_components/connections-grid";
import { DevSnippets } from "../../_components/dev-snippets";
import { FindingsPanel } from "../../_components/findings-panel";
import { IssueKey } from "../../_components/issue-key";
import { Section } from "../../_components/ui/page-header";

/** The active tab's content. The layout has already checked access and
 * fetched what this reads; React's per-render cache answers here. */
export default async function MerchantPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const [{ id }, { tab }] = await Promise.all([params, searchParams]);
  const active = tabOf(tab);

  if (active === "connections") {
    const [catalog, stored] = await Promise.all([connectors(), configured(id)]);
    if (!catalog.ok || !stored.ok) return null;
    return (
      <>
        <p className="mb-6 max-w-2xl text-sm text-ink-soft">
          Credentials go straight to an encrypted vault under this merchant and are never shown again. Each test makes one real
          read from the rail.
        </p>
        <ConnectionsGrid
          fold
          tenantId={id}
          tenantKey={id}
          connectors={catalog.data.map((c) => ({ ...c, stored: stored.data.configured[c.name] ?? [] }))}
        />
      </>
    );
  }

  if (active === "keys") {
    return (
      <>
        <p className="mb-4 max-w-2xl text-sm text-ink-soft">
          A key for this merchant reads only this merchant&apos;s rails. Issued once, shown once.
        </p>
        <IssueKey tenantId={id} />
        <Section title="Use the key">
          <DevSnippets />
        </Section>
      </>
    );
  }

  return <FindingsPanel apiBase={apiUrl()} />;
}
