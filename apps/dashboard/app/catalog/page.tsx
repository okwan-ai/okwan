import { apiUrl } from "@/lib/api";
import { FOLD_READS, railLabel } from "@/lib/finding";
import { requireTenant } from "@/lib/guard";
import { type Connector, connectors } from "@/lib/merchants";
import { Badge } from "../_components/ui/badge";
import { ButtonLink } from "../_components/ui/button";
import { CopyButton } from "../_components/ui/copy-button";
import { PageHeader } from "../_components/ui/page-header";

export const metadata = { title: "Connector catalog" };

/** Where each connector sits in a merchant's picture. */
const CATEGORY: Record<string, string> = {
  shopify: "Order ledger",
  stripe: "Payment rail",
  paypal: "Payment rail",
  paystack: "Payment rail",
  postgres: "Database",
  whatsapp: "Messaging",
};
const ORDER = ["shopify", "paypal", "stripe", "paystack", "postgres", "whatsapp"];

/**
 * Every connector, declared once in the SDK, and the three surfaces that
 * declaration generates: REST routes, SQL tables, MCP tools. All of it
 * comes from GET /v1/connectors, which the API builds from the same
 * declarations the routes are built from, so this page cannot drift from
 * what is served.
 */
export default async function CatalogPage() {
  await requireTenant();
  const r = await connectors();
  if (!r.ok) return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  const list = [...r.data].sort((a, b) => (ORDER.indexOf(a.name) + 99) % 99 - (ORDER.indexOf(b.name) + 99) % 99 || a.name.localeCompare(b.name));
  const base = apiUrl();
  const routes = list.reduce((n, c) => n + Object.values(c.resources ?? {}).reduce((m, ops) => m + ops.length, 0), 0);
  const tables = list.reduce((n, c) => n + (c.sql_tables?.length ?? 0), 0);
  const writes = list.reduce((n, c) => n + (c.writes?.length ?? 0), 0);

  return (
    <>
      <PageHeader
        title="Connector catalog"
        description={
          <>
            Each connector is declared once. From that one declaration Okwan generates its REST routes, its SQL tables and its
            MCP tools, so every surface reads the same rails the same way. {list.length} connectors · {routes} operations ·{" "}
            {tables} SQL tables · {writes} write operation{writes === 1 ? "" : "s"}, marked{" "}
            <span aria-hidden className="font-mono">✎</span>.
          </>
        }
        actions={<ButtonLink href={`${base}/docs`} target="_blank" rel="noopener">OpenAPI reference <span aria-hidden>↗</span><span className="sr-only">(opens in a new tab)</span></ButtonLink>}
      />

      <div className="grid gap-4">
        {list.map((c) => <ConnectorCard key={c.name} c={c} base={base} />)}
      </div>
    </>
  );
}

function ConnectorCard({ c, base }: { c: Connector; base: string }) {
  const resources = Object.entries(c.resources ?? {});
  const inCheck = (FOLD_READS as readonly string[]).includes(c.name);
  const writes = new Set(c.writes ?? []);
  return (
    <article aria-labelledby={`cat-${c.name}`} className="rounded-xl border border-line bg-surface">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <h2 id={`cat-${c.name}`} className="text-lg font-semibold">
            {railLabel(c.name)}
            {c.version && <code className="ml-2 font-mono text-xs text-ink-soft">v{c.version}</code>}
          </h2>
          <p className="mt-0.5 max-w-2xl text-sm text-ink-soft">{c.description}</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge>{CATEGORY[c.name] ?? "Connector"}</Badge>
          {inCheck && <Badge tone="ok" symbol="✓">In the reconciliation</Badge>}
          {writes.size > 0 && <Badge tone="warn" symbol="✎">{writes.size} write operation{writes.size === 1 ? "" : "s"}</Badge>}
        </div>
      </header>

      <div className="grid gap-6 px-5 py-4 lg:grid-cols-3">
        <Surface title="REST" note="POST, with a key. One request each.">
          <ul className="space-y-1">
            {resources.flatMap(([res, ops]) => ops.map((op) => {
              const path = `/v1/${c.name}/${res}/${op}`;
              const write = writes.has(`${res}.${op}`);
              return (
                <li key={path} className="flex items-center gap-1">
                  <code className="min-w-0 truncate font-mono text-[12px]">{path}</code>
                  {write && (
                    <span className="shrink-0 rounded-full border border-ink-soft/50 px-1.5 font-mono text-[11px] text-ink" title="Not read-only: this operation changes something on the rail.">
                      ✎ <span className="font-sans">write</span>
                    </span>
                  )}
                  <CopyButton value={`${base}${path}`} label={`Copy ${path}`} />
                </li>
              );
            }))}
            {resources.length === 0 && <li className="text-xs text-ink-soft">No operations listed.</li>}
          </ul>
        </Surface>

        <Surface title="SQL" note="POST /v1/query, with a key. Read-only; one request per statement.">
          {c.sql_tables?.length ? (
            <ul className="space-y-1">
              {c.sql_tables.map((t) => (
                <li key={t} className="flex items-center gap-1">
                  <code className="font-mono text-[12px]">{t}</code>
                  <CopyButton value={`SELECT * FROM ${t} LIMIT 20`} label={`Copy a query over ${t}`} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-ink-soft">
              {c.name === "postgres"
                ? "Your own tables, through postgres.sql.query; nothing is generated ahead of the call."
                : "No listable resource, so no table."}
            </p>
          )}
        </Surface>

        <Surface title="MCP" note="Hosted: four tools over the tables. SDK: one tool per operation.">
          <ul className="space-y-1 text-[12px]">
            {c.sql_tables?.length ? (
              <li><code className="font-mono">okwan_query</code> <span className="text-ink-soft">over {c.sql_tables.length} table{c.sql_tables.length === 1 ? "" : "s"}</span></li>
            ) : null}
            {resources.flatMap(([res, ops]) => ops.map((op) => (
              <li key={`${res}-${op}`}>
                <code className="font-mono">{`${c.name}_${res}_${op}`}</code> <span className="text-ink-soft">SDK</span>
                {writes.has(`${res}.${op}`) && <span className="ml-1 font-mono text-[11px] text-ink" title="Not read-only: this operation changes something on the rail.">✎ <span className="font-sans">write</span></span>}
              </li>
            )))}
          </ul>
        </Surface>
      </div>

      <footer className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-line px-5 py-2.5 text-xs text-ink-soft">
        <span>
          Credentials: {c.credential_fields.map((f) => <code key={f} className="mr-1.5 font-mono">{f}</code>)}
        </span>
        <span>
          Test: {c.probe ? <code className="font-mono">{c.probe}</code> : "no blind read; saved without a live test"}
        </span>
      </footer>
    </article>
  );
}

function Surface({ title, note, children }: { title: string; note: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="min-w-0">
      <h3 className="text-xs font-medium tracking-wide text-ink-soft uppercase">{title}</h3>
      <p className="mb-2 text-[11px] text-ink-soft">{note}</p>
      {children}
    </section>
  );
}
