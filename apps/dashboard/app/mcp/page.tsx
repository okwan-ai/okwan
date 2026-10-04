import { apiUrl, tenantTree } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { HOSTED_TOOLS } from "@/lib/hosted-tools";
import { mcpClientOf } from "@/lib/mcp-clients";
import { Endpoints } from "../_components/endpoints";
import { McpSetup } from "../_components/mcp-setup";
import { PromptLibrary } from "../_components/prompt-library";
import { ButtonLink } from "../_components/ui/button";
import { EmptyState } from "../_components/ui/empty-state";
import { PageHeader, Section } from "../_components/ui/page-header";

export const metadata = { title: "MCP for agents" };

export default async function McpPage({ searchParams }: { searchParams: Promise<{ client?: string }> }) {
  await requireTenant();
  const [{ client }, tree] = await Promise.all([searchParams, tenantTree()]);
  const merchants = (tree?.children ?? []).map((c) => ({ id: c.id, name: c.name }));
  const apiBase = apiUrl();
  return (
    <>
      <PageHeader
        title="MCP for agents"
        description="The hosted MCP server gives an agent four read-only tools. The key decides whose rails it reads: issue one per merchant."
        actions={<Endpoints apiBase={apiBase} />}
      />
      {merchants.length ? (
        <McpSetup merchants={merchants} apiBase={apiBase} client={mcpClientOf(client)} />
      ) : (
        <EmptyState title="Add a merchant first" action={<ButtonLink href="/merchants?add=1" variant="primary">Add merchant</ButtonLink>}>
          An agent reads one merchant at a time, with a key issued for that merchant.
        </EmptyState>
      )}
      <div id="prompts">
        <Section title="Prompts your agent can run" aside="One question per verdict that needs a look, and one in SQL, each bound to its exact call">
          <PromptLibrary apiBase={apiBase} />
        </Section>
      </div>
      <Section title="Tools on the hosted server">
        <dl className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
          {HOSTED_TOOLS.map(({ name, what }) => (
            <div key={name} className="grid gap-1 px-5 py-3 sm:grid-cols-[240px_1fr]">
              <dt><code className="font-mono text-[13px]">{name}</code></dt>
              <dd className="text-sm text-ink-soft">{what}</dd>
            </div>
          ))}
        </dl>
      </Section>
    </>
  );
}
