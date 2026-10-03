import { apiUrl } from "@/lib/api";
import { CodeBlock } from "./ui/copy-button";

/**
 * Ready-to-paste MCP and REST setup. The key is a placeholder: a key is
 * shown once at issue and never again, so the page cannot fill it in.
 * mcp-remote drops a --header not in exact Name:Value form (§11), hence
 * no space after the colon.
 */
export function DevSnippets({ server = "okwan" }: { server?: string }) {
  const base = apiUrl();
  const mcp = JSON.stringify(
    {
      mcpServers: {
        [server]: {
          command: "npx",
          args: ["mcp-remote", `${base}/mcp/`, "--header", "Authorization:Bearer ${OKWAN_KEY}"],
          env: { OKWAN_KEY: "okw_…" },
        },
      },
    },
    null,
    2,
  );
  const curl = `curl "${base}/v1/reconciliations/across/rails?outcome=collected_twice" \\
  -H "Authorization: Bearer okw_…"`;
  return (
    <div className="grid gap-4">
      <CodeBlock label="MCP client config" code={mcp} />
      <CodeBlock label="REST · curl" code={curl} />
      <p className="text-xs text-ink-soft">
        Replace <code className="font-mono">okw_…</code> with the key. Agents call{" "}
        <code className="font-mono">reconcile_across_rails</code> for the same result the dashboard shows.
      </p>
    </div>
  );
}
