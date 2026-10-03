"use client";

import { useState } from "react";
import { AgentPanel } from "./agent-panel";
import { IssueKey } from "./issue-key";

/**
 * An agent reads one merchant with that merchant's key, so setup starts by
 * choosing the merchant. The workspace's own key (API keys page) reads only
 * the workspace's own rails, never a merchant's.
 */
export function McpSetup({ merchants, apiBase }: { merchants: { id: string; name: string }[]; apiBase: string }) {
  const [id, setId] = useState(merchants[0]?.id ?? "");
  const m = merchants.find((x) => x.id === id);
  const server = m ? `okwan-${m.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}` : "okwan";
  return (
    <div className="space-y-5">
      <label className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-medium">Merchant</span>
        <select value={id} onChange={(e) => setId(e.target.value)} className="field w-auto min-w-56 py-2">
          {merchants.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </select>
      </label>
      {m && (
        <>
          <div>
            <h2 className="mb-2 text-sm font-semibold">1 · Issue a key for {m.name}</h2>
            <IssueKey key={m.id} tenantId={m.id} />
          </div>
          <div>
            <h2 className="mb-2 text-sm font-semibold">2 · Add the server to your MCP client, then ask</h2>
            <AgentPanel apiBase={apiBase} server={server} views={["config", "prompt", "mcp", "rest"]} outcome="collected_twice" />
          </div>
        </>
      )}
    </div>
  );
}
