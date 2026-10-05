"use client";

import Link from "next/link";
import { useState } from "react";
import { AgentPanel } from "./agent-panel";
import { IssueKey } from "./issue-key";
import { McpClients } from "./mcp-clients";
import { Card, CardBody, CardHeader } from "./ui/card";

const WORKSPACE = "workspace";

/**
 * An agent reads with the key it is given, so setup starts by choosing
 * whose data: one merchant, or the workspace's own. A merchant's key reads
 * only that merchant; the workspace's key reads only the workspace's own
 * connections, never a merchant's.
 *
 * Three steps: the key, the client, the question. The client tabs carry
 * the per-client recipe; the navy panel carries what to ask and the same
 * read on the other channels. The choice is kept in `?merchant=` (an id,
 * or "workspace"), so a merchant's "Agent setup" link lands on it.
 */
export function McpSetup({ merchants, apiBase, client, initialFor }: {
  merchants: { id: string; name: string }[];
  apiBase: string;
  client?: string | null;
  /** From `?merchant=`: a merchant id or "workspace"; anything else falls back. */
  initialFor?: string | null;
}) {
  const [id, setId] = useState(() =>
    initialFor === WORKSPACE || merchants.some((m) => m.id === initialFor) ? (initialFor as string) : (merchants[0]?.id ?? WORKSPACE),
  );
  // A key on screen exists nowhere else; switching would discard it.
  const [showing, setShowing] = useState(false);
  const m = merchants.find((x) => x.id === id);
  const server = m ? `okwan-${m.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}` : "okwan";

  function choose(next: string) {
    setId(next);
    const u = new URL(window.location.href);
    u.searchParams.set("merchant", next);
    window.history.replaceState(null, "", u.toString());
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <label className="flex flex-wrap items-center gap-3 text-sm">
          <span className="font-medium">For</span>
          <select
            value={id}
            onChange={(e) => choose(e.target.value)}
            disabled={showing}
            aria-describedby={showing ? "mcp-store-first" : undefined}
            className="field w-auto min-w-56 py-2 disabled:opacity-60 max-sm:min-w-0 max-sm:flex-1"
          >
            {merchants.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            <option value={WORKSPACE}>Your workspace</option>
          </select>
          {showing && <span id="mcp-store-first" className="text-xs text-ink-soft">Store the key below first; it can&apos;t be shown again.</span>}
        </label>
        {merchants.length === 0 && (
          <p className="text-xs text-ink-soft">
            Add a merchant to give an agent one merchant&apos;s data.{" "}
            <Link href="/merchants?add=1" className="underline underline-offset-4 hover:text-ink">Add a merchant →</Link>
          </p>
        )}
      </div>
      <Card>
        <CardHeader title={`1 · Issue a key for ${m ? m.name : "your workspace"}`} />
        <CardBody>
          <IssueKey key={id} tenantId={m?.id} onShowing={setShowing} bare />
        </CardBody>
      </Card>
      <Card flush>
        <CardHeader title="2 · Connect your client" />
        <McpClients apiBase={apiBase} server={server} initial={client} />
      </Card>
      <div>
        <h2 className="mb-2 text-sm font-semibold">3 · Ask</h2>
        <AgentPanel
          apiBase={apiBase}
          server={server}
          views={["prompt", "mcp", "rest", "config"]}
          outcome="collected_twice"
          keyFor={m ? m.name : "your workspace"}
        />
      </div>
    </div>
  );
}
