import { requireTenant } from "@/lib/guard";
import { DevSnippets } from "../_components/dev-snippets";
import { ButtonLink } from "../_components/ui/button";
import { PageHeader, Section } from "../_components/ui/page-header";

export default async function McpPage() {
  await requireTenant();
  return (
    <>
      <PageHeader
        title="MCP for agents"
        description="Every connector and reconciliation is a tool on the hosted MCP server. An agent connects with a key, and the key decides whose rails it reads."
        actions={<ButtonLink href="/key" variant="secondary">Issue a key</ButtonLink>}
      />
      <Section title="Connect a client">
        <DevSnippets />
      </Section>
      <Section title="Per merchant">
        <p className="max-w-2xl text-sm text-ink-soft">
          A key issued from a merchant&apos;s API keys tab reads only that merchant. Give each merchant&apos;s agent its own
          key; nothing crosses between merchants.
        </p>
      </Section>
    </>
  );
}
