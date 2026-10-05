import { tenantTree } from "@/lib/api";
import { type Declaration, defineOnce, groups, plural } from "@/lib/connector-meta";
import { requireTenant } from "@/lib/guard";
import { HOSTED_TOOLS } from "@/lib/hosted-tools";
import { configured, connectors, merchantsWithRails, railState } from "@/lib/merchants";
import { ConnectorTile } from "../_components/connector-tile";
import { GRID } from "../_components/ui/card";
import { PageHeader, Section } from "../_components/ui/page-header";
import { ReadOnlyChip } from "../_components/ui/read-only-chip";

export const metadata = { title: "Integrations" };

const DESCRIPTION = "Each connector is declared once. Its REST routes, SQL tables and MCP tools come from that one declaration.";

/**
 * Every connector as a logo tile: the three a check reads, then the rest.
 * Each tile links to the connector's home (/integrations/[name]). The
 * counts are derived from GET /v1/connectors, which the API builds from the
 * same declarations its routes are built from, so this page cannot drift
 * from what is served. Coverage comes from each merchant's stored
 * credential field names (never values) and the workspace's own.
 */
export default async function IntegrationsPage() {
  const tenant = await requireTenant();
  const [r, merchants, tree, own] = await Promise.all([connectors(), merchantsWithRails(), tenantTree(), configured(tenant.id)]);
  if (!r.ok) {
    return (
      <>
        <PageHeader title="Integrations" description={DESCRIPTION} />
        <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>
      </>
    );
  }

  const list = r.data;
  const { check, more } = groups(list);
  const totals = list.map(defineOnce).reduce(
    (t, d) => ({ routes: t.routes + d.routes, tables: t.tables + d.tables, writes: t.writes + d.writes }),
    { routes: 0, tables: 0, writes: 0 },
  );
  const selfReady = own.ok ? railState(list, own.data.configured).ready : [];
  const total = merchants?.length ?? tree?.children.length ?? 0;

  /** "Connected for 3 of 5 merchants and your workspace". */
  const coverage = (name: string): string => {
    const self = selfReady.includes(name);
    if (!merchants) return self ? "Connected for your workspace" : "";
    const n = merchants.filter((m) => m.ready.includes(name)).length;
    if (n === 0) return self ? "Connected for your workspace" : "Not connected yet";
    return `Connected for ${n} of ${plural(total, "merchant")}${self ? " and your workspace" : ""}`;
  };
  const tile = (c: Declaration) => <ConnectorTile key={c.name} mode="catalog" c={c} coverage={coverage(c.name)} />;

  return (
    <>
      <PageHeader
        title="Integrations"
        description={DESCRIPTION}
        meta={
          <>
            <span className="tabular-nums">
              {plural(list.length, "declaration")} <span aria-hidden>→</span><span className="sr-only">generate</span>{" "}
              {plural(totals.routes, "REST route")} · {plural(totals.tables, "SQL table")} · {plural(HOSTED_TOOLS.length, "hosted MCP tool")}
            </span>
            <ReadOnlyChip />
            {totals.writes > 0 && (
              <span>
                <span aria-hidden className="font-mono">✎</span> {plural(totals.writes, "write operation")}, marked
              </span>
            )}
          </>
        }
      />

      {check.length > 0 && (
        <Section title="In the check" description="A check reads these three.">
          <ul className={GRID.tiles}>{check.map(tile)}</ul>
        </Section>
      )}
      {more.length > 0 && (
        <Section title="More connectors" description="Readable over REST, SQL and MCP. Not in the check yet.">
          <ul className={GRID.tiles}>{more.map(tile)}</ul>
        </Section>
      )}
    </>
  );
}
