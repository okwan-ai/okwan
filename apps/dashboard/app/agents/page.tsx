import Link from "next/link";
import { Fragment } from "react";
import { apiUrl, tenantTree } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { HOSTED_TOOLS } from "@/lib/hosted-tools";
import { mcpClientOf } from "@/lib/mcp-clients";
import { McpSetup } from "../_components/mcp-setup";
import { PromptLibrary } from "../_components/prompt-library";
import { CopyButton } from "../_components/ui/copy-button";
import { KeyValueList, KeyValueRow } from "../_components/ui/key-value";
import { PageHeader, Section } from "../_components/ui/page-header";
import { ReadOnlyChip } from "../_components/ui/read-only-chip";

export const metadata = { title: "Agents" };

/**
 * Agent setup's one home (§9 2026-10-05): the key, the client, the
 * question, then the prompts and every endpoint a client might need.
 * /mcp redirects here with its query (?client=) and the browser keeps a
 * #fragment, so /mcp#prompts still lands on the prompts.
 */
export default async function AgentsPage({ searchParams }: { searchParams: Promise<{ client?: string; merchant?: string }> }) {
  await requireTenant();
  const [{ client, merchant }, tree] = await Promise.all([searchParams, tenantTree()]);
  const merchants = (tree?.children ?? []).map((c) => ({ id: c.id, name: c.name }));
  const apiBase = apiUrl();
  return (
    <>
      <PageHeader
        title="Agents"
        description="Give an agent read-only access to one merchant: issue its key, connect your client, ask."
        meta={<ReadOnlyChip />}
      />
      <McpSetup merchants={merchants} apiBase={apiBase} client={mcpClientOf(client)} initialFor={merchant} />
      <Section id="prompts" title="Prompts your agent can run" description="Each prompt matches a Findings filter, so you can check the answer.">
        <PromptLibrary apiBase={apiBase} />
      </Section>
      <Section id="endpoints" title="Endpoints and tools">
        <Endpoints apiBase={apiBase} />
      </Section>
      <p className="mt-10 text-xs text-ink-soft">
        Agents can only read. No channel can refund, charge or move money.{" "}
        <Link href="/settings?tab=security#read-only" className="underline underline-offset-4 hover:text-ink">How Okwan handles your data →</Link>
      </p>
    </>
  );
}

/**
 * Every endpoint a client might need, for a client this page has no recipe
 * for. All exist hosted (apps/api): the MCP server at /mcp/ (streamable
 * HTTP, bearer key), connector REST at /v1/{connector}/{resource}/{operation},
 * SQL at /v1/query, OpenAPI at /docs. A client that can't send a header
 * bridges with mcp-remote, as the Claude Desktop recipe above shows.
 */
function Endpoints({ apiBase }: { apiBase: string }) {
  const rows: { label: string; value: string; copy?: boolean }[] = [
    { label: "MCP server (streamable HTTP)", value: `${apiBase}/mcp/` },
    { label: "Header", value: "Authorization: Bearer okw_…" },
    { label: "REST base", value: apiBase },
    { label: "Connector operations", value: "POST /v1/{connector}/{resource}/{operation}", copy: false },
    { label: "The check over REST", value: `GET ${apiBase}/v1/reconciliations/across/rails?outcome=collected_twice` },
    { label: "SQL query", value: `POST ${apiBase}/v1/query` },
    { label: "SQL tables", value: `GET ${apiBase}/v1/query/tables` },
    { label: "SQL request body", value: JSON.stringify({ sql: "SELECT name, net_payment_minor FROM shopify.orders LIMIT 20" }) },
    { label: "OpenAPI reference", value: `${apiBase}/docs` },
    { label: "OpenAPI document", value: `${apiBase}/openapi.json` },
  ];
  return (
    <KeyValueList label="Endpoints and tools">
      {rows.map(({ label, value, copy = true }) => (
        <KeyValueRow key={label} label={label}>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 font-mono text-[13px] break-all">{value}</code>
            {/* The 44px target overlaps the row's padding, so rows stay compact. */}
            {copy && <span className="-my-2.5 shrink-0"><CopyButton value={value} label={`Copy ${label}`} /></span>}
          </div>
        </KeyValueRow>
      ))}
      {HOSTED_TOOLS.map(({ name, what }) => (
        <KeyValueRow key={name} label={<code className="font-mono text-[13px]">{breakable(name)}</code>}>
          {what}
        </KeyValueRow>
      ))}
    </KeyValueList>
  );
}

/** A long tool name may wrap in the label column, but only after an underscore. */
function breakable(name: string) {
  return name.split("_").map((part, i, all) => (
    <Fragment key={i}>{part}{i < all.length - 1 && <>_<wbr /></>}</Fragment>
  ));
}
