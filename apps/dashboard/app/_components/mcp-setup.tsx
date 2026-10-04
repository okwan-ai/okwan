"use client";

import { useState } from "react";
import { AgentPanel } from "./agent-panel";
import { IssueKey } from "./issue-key";
import { McpClients } from "./mcp-clients";

/**
 * An agent reads one merchant with that merchant's key, so setup starts by
 * choosing the merchant. The workspace's own key (API keys page) reads only
 * the workspace's own rails, never a merchant's.
 *
 * Three steps: the key, the client, the question. The client tabs carry
 * the per-client recipe; the navy panel carries what to ask and the same
 * read on the other surfaces.
 */
export function McpSetup({ merchants, apiBase, client }: { merchants: { id: string; name: string }[]; apiBase: string; client?: string | null }) {
  const [id, setId] = useState(merchants[0]?.id ?? "");
  // A key on screen exists nowhere else; switching merchant would discard it.
  const [showing, setShowing] = useState(false);
  const m = merchants.find((x) => x.id === id);
  const server = m ? `okwan-${m.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}` : "okwan";
  return (
    <div className="space-y-6">
      <label className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-medium">Merchant</span>
        <select
          value={id}
          onChange={(e) => setId(e.target.value)}
          disabled={showing}
          aria-describedby={showing ? "mcp-store-first" : undefined}
          className="field w-auto min-w-56 py-2 disabled:opacity-60"
        >
          {merchants.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </select>
        {showing && <span id="mcp-store-first" className="text-xs text-ink-soft">Store the key below first; it can&apos;t be shown again.</span>}
      </label>
      {m && (
        <>
          <div>
            <h2 className="mb-2 text-sm font-semibold">1 · Issue a key for {m.name}</h2>
            <IssueKey key={m.id} tenantId={m.id} onShowing={setShowing} />
          </div>
          <div>
            <h2 className="mb-2 text-sm font-semibold">2 · Connect your client</h2>
            <McpClients apiBase={apiBase} server={server} initial={client} />
          </div>
          <div>
            <h2 className="mb-2 text-sm font-semibold">3 · Ask</h2>
            <AgentPanel apiBase={apiBase} server={server} views={["prompt", "mcp", "rest", "config"]} outcome="collected_twice" />
          </div>
        </>
      )}
    </div>
  );
}
