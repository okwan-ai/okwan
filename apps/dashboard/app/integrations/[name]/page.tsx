import Link from "next/link";
import { notFound } from "next/navigation";
import { Fragment, type ReactNode } from "react";
import { apiUrl } from "@/lib/api";
import { defineOnce, firstSentence, inCheck, plural, ROLE, writeLabel } from "@/lib/connector-meta";
import { railLabel } from "@/lib/finding";
import { requireTenant } from "@/lib/guard";
import { type Connector, configured, connectors, merchantsWithRails, railState } from "@/lib/merchants";
import { Badge } from "../../_components/ui/badge";
import { BrandMark } from "../../_components/ui/brand-mark";
import { Card, CardBody } from "../../_components/ui/card";
import { CopyButton } from "../../_components/ui/copy-button";
import { KeyValueList, KeyValueRow } from "../../_components/ui/key-value";
import { PageHeader, Section } from "../../_components/ui/page-header";

/** The declaration for `name`, or null when the catalog has none. */
async function declaration(name: string): Promise<Connector | null> {
  const r = await connectors();
  return r.ok ? (r.data.find((c) => c.name === name) ?? null) : null;
}

export async function generateMetadata({ params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const c = await declaration(name);
  return { title: c ? railLabel(c.name) : "Integrations" };
}

type State = "connected" | "partial" | "missing" | "unknown";

const STATUS: Record<State, ReactNode> = {
  connected: <Badge tone="ok" symbol="●">Connected</Badge>,
  partial: <Badge tone="warn" symbol="◐">Partial</Badge>,
  missing: <Badge tone="neutral" symbol="○">Not connected</Badge>,
  unknown: <Badge tone="neutral" symbol="?">Unknown</Badge>,
};

function stateOf(name: string, m: { ready: string[]; partial: string[]; known: boolean }): State {
  if (!m.known) return "unknown";
  if (m.ready.includes(name)) return "connected";
  return m.partial.includes(name) ? "partial" : "missing";
}

/**
 * One connector's home: what its one declaration produces (REST routes, SQL
 * tables, MCP tools), who has it connected, and what it needs and can
 * change. Everything about the connector comes from GET /v1/connectors, the
 * same declarations the API mounts its routes from; the role and whether a
 * check reads it are the dashboard mirror in lib/connector-meta.ts.
 */
export default async function ConnectorPage({ params }: { params: Promise<{ name: string }> }) {
  const tenant = await requireTenant();
  const { name } = await params;
  const r = await connectors();
  if (!r.ok) return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  const c = r.data.find((x) => x.name === name);
  if (!c) notFound();

  const [merchants, own] = await Promise.all([merchantsWithRails(), configured(tenant.id)]);
  const label = railLabel(c.name);
  const base = apiUrl();
  const resources = Object.entries(c.resources ?? {});
  const writes = new Set(c.writes ?? []);
  const tables = c.sql_tables ?? [];
  const d = defineOnce(c);
  const connectedFor = merchants?.filter((m) => m.ready.includes(c.name)).length ?? 0;
  const self: State = own.ok
    ? stateOf(c.name, { ...railState(r.data, own.data.configured), known: true })
    : "unknown";

  return (
    <>
      <PageHeader
        breadcrumb={[{ href: "/integrations", label: "Integrations" }]}
        icon={<BrandMark name={c.name} label={label} tile size={40} />}
        title={label}
        description={firstSentence(c.description)}
        meta={
          <>
            <Badge>{ROLE[c.name] ?? "Connector"}</Badge>
            {inCheck(c.name) && <Badge tone="ok" symbol="✓">In the check</Badge>}
            {d.writes > 0 && <Badge tone="warn" symbol="✎">{writeLabel(c)}</Badge>}
            {c.version && <code className="font-mono">v{c.version}</code>}
          </>
        }
      />

      <Section
        title="From one declaration"
        description="REST: POST with a key, one request each · SQL: POST /v1/query, read-only · MCP: hosted okwan_query over the tables; the SDK adds one tool per operation."
      >
        <Card as="div" className="grid gap-6 px-5 py-4 lg:grid-cols-3">
          <Surface title="REST">
            <ul className="space-y-1">
              {resources.flatMap(([res, ops]) => ops.map((op) => {
                const path = `/v1/${c.name}/${res}/${op}`;
                return (
                  <li key={path} className="flex items-center gap-1">
                    <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-1">
                      <code className="min-w-0 font-mono text-xs break-words"><Slashed path={path} /></code>
                      {writes.has(`${res}.${op}`) && <WriteMark />}
                    </span>
                    <CopyButton value={`${base}${path}`} label={`Copy ${path}`} />
                  </li>
                );
              }))}
              {d.routes === 0 && <li className="text-xs text-ink-soft">No operations listed.</li>}
            </ul>
          </Surface>

          <Surface title="SQL">
            {tables.length ? (
              <ul className="space-y-1">
                {tables.map((t) => (
                  <li key={t} className="flex items-center gap-1">
                    <code className="min-w-0 flex-1 font-mono text-xs break-words">{t}</code>
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

          <Surface title="MCP">
            <ul className="space-y-1.5 text-xs">
              {tables.length > 0 && (
                <li>
                  <code className="font-mono">okwan_query</code>{" "}
                  <span className="text-ink-soft">over {plural(tables.length, "table")} · hosted</span>
                </li>
              )}
              {resources.flatMap(([res, ops]) => ops.map((op) => (
                <li key={`${res}-${op}`} className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
                  <code className="min-w-0 font-mono break-words">{`${c.name}_${res}_${op}`}</code>
                  <span className="text-ink-soft">SDK</span>
                  {writes.has(`${res}.${op}`) && <WriteMark />}
                </li>
              )))}
            </ul>
          </Surface>
        </Card>
      </Section>

      <Section
        title="Connections"
        description={
          // No claim about merchants when their listing didn't load; the row below says so.
          !merchants
            ? undefined
            : connectedFor > 0
              ? `Connected for ${connectedFor} of ${plural(merchants.length, "merchant")}`
              : `No merchant has ${label} connected yet.`
        }
      >
        <Card flush>
          <CardBody list>
            {!merchants ? (
              <li className="flex min-h-14 items-center px-5 py-2 text-sm text-ink-soft">Merchants&apos; connections aren&apos;t available right now.</li>
            ) : merchants.length === 0 ? (
              <li className="flex min-h-14 flex-wrap items-center gap-x-2 px-5 py-2 text-sm">
                <span className="text-ink-soft">No merchants yet ·</span>
                <Link href="/merchants?add=1" className="inline-flex min-h-11 items-center font-medium underline underline-offset-4">
                  Add a merchant →
                </Link>
              </li>
            ) : (
              merchants.map((m) => {
                const id = encodeURIComponent(m.tenant.id);
                return (
                  <ConnectionRow
                    key={m.tenant.id}
                    name={m.tenant.name}
                    href={`/merchants/${id}`}
                    state={stateOf(c.name, m)}
                    action={`/merchants/${id}?tab=connections&connect=${encodeURIComponent(c.name)}`}
                    label={label}
                  />
                );
              })
            )}
            <ConnectionRow
              name="Your workspace"
              href="/connections"
              state={self}
              action={`/connections?connect=${encodeURIComponent(c.name)}`}
              label={label}
            />
          </CardBody>
        </Card>
      </Section>

      <Section title="Details">
        <KeyValueList>
          <KeyValueRow label="Full description">{c.description}</KeyValueRow>
          <KeyValueRow label="Credentials it needs">
            <span className="flex flex-wrap gap-x-3 gap-y-1">
              {c.credential_fields.map((f) => <code key={f} className="font-mono text-[13px]">{f}</code>)}
            </span>
          </KeyValueRow>
          <KeyValueRow label="Connection test">
            {c.probe ? <code className="font-mono text-[13px]">{c.probe}</code> : "No blind read; saved without a live test"}
          </KeyValueRow>
          <KeyValueRow label="What it can change">
            {writes.size === 0 ? (
              "Nothing: read-only"
            ) : (
              <ul className="space-y-1">
                {[...writes].map((w) => (
                  <li key={w}>
                    <span aria-hidden className="mr-1.5 font-mono text-xs">✎</span>
                    <code className="font-mono text-[13px]">{w}</code>
                  </li>
                ))}
              </ul>
            )}
          </KeyValueRow>
        </KeyValueList>
      </Section>
    </>
  );
}

/** One tenant's connection to this connector: its name, status, and the
 *  link that opens its connect sheet. */
function ConnectionRow({ name, href, state, action, label }: {
  name: string;
  href: string;
  state: State;
  action: string;
  label: string;
}) {
  const verb = state === "connected" || state === "unknown" ? "Manage" : "Connect";
  return (
    <li className="flex min-h-14 items-center justify-between gap-3 px-5 py-2">
      <Link href={href} className="inline-flex min-h-11 min-w-0 items-center text-sm font-medium underline-offset-4 hover:underline">
        <span className="truncate">{name}</span>
      </Link>
      <span className="flex shrink-0 items-center gap-3">
        {STATUS[state]}
        <Link
          href={action}
          aria-label={`${verb} ${label} for ${name === "Your workspace" ? "your workspace" : name}`}
          className="inline-flex min-h-11 items-center text-sm underline underline-offset-4"
        >
          {verb}
        </Link>
      </span>
    </li>
  );
}

/** A route that wraps after a slash, never inside a word. */
function Slashed({ path }: { path: string }) {
  return path.split("/").map((part, i) => (
    <Fragment key={i}>
      {i > 0 && <>/<wbr /></>}
      {part}
    </Fragment>
  ));
}

function WriteMark() {
  return (
    <span className="shrink-0" title="Not read-only: this operation changes something on the rail.">
      <Badge tone="warn" symbol="✎">write</Badge>
    </span>
  );
}

function Surface({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="min-w-0">
      <h3 className="mb-2 text-xs font-medium tracking-wide text-ink-soft uppercase">{title}</h3>
      {children}
    </section>
  );
}
