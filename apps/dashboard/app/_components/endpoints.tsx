"use client";

import { useState } from "react";
import { HOSTED_TOOL_NAMES } from "@/lib/hosted-tools";
import { Button } from "./ui/button";
import { CodeBlock, CopyButton } from "./ui/copy-button";
import { Modal } from "./ui/dialog";

type Tab = "mcp" | "rest" | "sql" | "openapi";
const TABS: { id: Tab; label: string }[] = [
  { id: "mcp", label: "MCP" },
  { id: "rest", label: "REST" },
  { id: "sql", label: "SQL" },
  { id: "openapi", label: "OpenAPI" },
];

/**
 * Every endpoint a client might need, with what to send it, for a client
 * this dashboard has no recipe for. All four exist hosted (apps/api): the
 * MCP server at /mcp/ (streamable HTTP, bearer key), connector REST at
 * /v1/{connector}/{resource}/{operation}, SQL at /v1/query, OpenAPI at /docs.
 */
export function Endpoints({ apiBase }: { apiBase: string }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("mcp");
  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>Endpoints</Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Endpoints">
        <p className="text-sm text-ink-soft">What a client needs to reach Okwan directly. Every call takes a key for one merchant.</p>
        <div role="group" aria-label="Endpoint" className="mt-3 flex gap-1 rounded-lg bg-canvas p-0.5 text-xs">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-pressed={tab === t.id}
              onClick={() => setTab(t.id)}
              className={`min-h-9 flex-1 rounded-md border-b-2 px-2 font-medium ${tab === t.id ? "border-ink bg-surface text-ink shadow-sm" : "border-transparent text-ink-soft hover:text-ink"}`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="mt-4 space-y-3 text-sm">
          {tab === "mcp" && (
            <>
              <Field label="Remote MCP server (streamable HTTP)" value={`${apiBase}/mcp/`} />
              <Field label="Header" value="Authorization: Bearer okw_…" />
              <p className="text-xs text-ink-soft">
                Four read-only tools: {HOSTED_TOOL_NAMES.map((n, i) => <span key={n}>{i > 0 && ", "}<code className="font-mono">{n}</code></span>)}. A client
                that can&apos;t send a header can bridge with <code className="font-mono">mcp-remote</code>; the Claude Desktop recipe on MCP for agents shows how.
              </p>
            </>
          )}
          {tab === "rest" && (
            <>
              <Field label="Base URL" value={apiBase} />
              <Field label="Connector operations" value="POST /v1/{connector}/{resource}/{operation}" copy={false} />
              <Field label="The reconciliation" value={`GET ${apiBase}/v1/reconciliations/across/rails?outcome=collected_twice`} />
              <p className="text-xs text-ink-soft">JSON in, JSON out, <code className="font-mono">Authorization: Bearer okw_…</code>. Each call that reads a rail is one request against the plan.</p>
            </>
          )}
          {tab === "sql" && (
            <>
              <Field label="Query" value={`POST ${apiBase}/v1/query`} />
              <Field label="Tables" value={`GET ${apiBase}/v1/query/tables`} />
              <CodeBlock label="Body" code={JSON.stringify({ sql: "SELECT name, net_payment_minor FROM shopify.orders LIMIT 20" }, null, 2)} />
              <p className="text-xs text-ink-soft">Read-only: one SELECT per request; tables are named connector.resource (the catalog lists them).</p>
            </>
          )}
          {tab === "openapi" && (
            <>
              <Field label="Interactive reference" value={`${apiBase}/docs`} />
              <Field label="OpenAPI document" value={`${apiBase}/openapi.json`} />
              <p className="text-xs text-ink-soft">Generated from the same declarations the routes are built from.</p>
            </>
          )}
        </div>
      </Modal>
    </>
  );
}

function Field({ label, value, copy = true }: { label: string; value: string; copy?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="text-xs text-ink-soft">{label}</p>
      <div className="flex items-center gap-1">
        <code className="min-w-0 flex-1 rounded-md border border-line bg-canvas px-2 py-1.5 font-mono text-[12px] break-all">{value}</code>
        {copy && <CopyButton value={value} label={`Copy ${label}`} />}
      </div>
    </div>
  );
}
