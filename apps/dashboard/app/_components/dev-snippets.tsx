import { apiUrl } from "@/lib/api";
import { AgentPanel } from "./agent-panel";

/** Ready-to-paste MCP and REST setup for a merchant's key, on the public
 *  API base the dashboard itself calls. */
export function DevSnippets({ server = "okwan", scope = "merchant" }: { server?: string; scope?: "merchant" | "workspace" }) {
  return <AgentPanel apiBase={apiUrl()} server={server} scope={scope} views={["config", "mcp", "rest", "prompt"]} />;
}
