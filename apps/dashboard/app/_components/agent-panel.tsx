"use client";

import { useState } from "react";
import { CopyButton } from "./ui/copy-button";

type View = "mcp" | "rest" | "prompt" | "config";

const LABEL: Record<View, string> = {
  mcp: "MCP call",
  rest: "REST",
  prompt: "Agent prompt",
  config: "Client config",
};

/**
 * The same result on the surfaces that exist hosted: the MCP tool call
 * (okwan_reconcile with name "rails"; the hosted server has no
 * reconcile_across_rails), the REST route, a prompt that hands the work to
 * an agent, and the MCP client config. The page's one navy panel (§2).
 *
 * The key is always a placeholder: a key is shown once at issue, and this
 * page never holds one. Read-only by construction: the prompt says so, and
 * no Okwan surface can write to a rail.
 */
export function AgentPanel({ apiBase, outcome, views = ["mcp", "rest", "prompt"], server = "okwan" }: {
  apiBase: string;
  /** An outcome to filter on (e.g. "collected_twice"); all when absent. */
  outcome?: string;
  views?: View[];
  server?: string;
}) {
  const [view, setView] = useState<View>(views[0]);
  const status = outcome ?? "all";
  const code: Record<View, string> = {
    mcp: `okwan_reconcile ${JSON.stringify({ name: "rails", status, limit: 200 })}`,
    rest: `curl "${apiBase}/v1/reconciliations/across/rails?outcome=${status}" \\
  -H "Authorization: Bearer okw_…"`,
    prompt: [
      `Use the ${server} MCP server. Call okwan_reconcile with name "rails"${outcome ? ` and status "${outcome}"` : ""}.`,
      "For each order returned, list the order, the rails that took payment, what each took, and the order total.",
      outcome === "collected_twice" ? "Total what was taken beyond the order totals: that is owed back to customers." : "Say which orders need a person to look, and why.",
      "Read only: do not attempt refunds or any write to a payment rail. Okwan cannot make one.",
    ].join("\n"),
    config: JSON.stringify(
      {
        mcpServers: {
          [server]: {
            command: "npx",
            // mcp-remote drops a --header not in exact Name:Value form (§11).
            args: ["mcp-remote", `${apiBase}/mcp/`, "--header", "Authorization:Bearer ${OKWAN_KEY}"],
            env: { OKWAN_KEY: "okw_…" },
          },
        },
      },
      null,
      2,
    ),
  };

  return (
    <section aria-label="Same result for agents" className="min-w-0 overflow-hidden rounded-xl bg-navy text-canvas">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-canvas/15 py-1.5 pr-1.5 pl-4">
        <p className="text-xs font-medium text-canvas/80">Same result for your agents</p>
        <div className="flex flex-wrap items-center gap-1">
          <div role="group" aria-label="Surface" className="flex rounded-lg bg-canvas/10 p-0.5">
            {views.map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => setView(v)}
                className={`min-h-9 rounded-md px-2.5 text-xs font-medium focus-visible:outline-canvas ${view === v ? "bg-canvas text-ink" : "text-canvas/80 hover:text-canvas"}`}
              >
                {LABEL[v]}
              </button>
            ))}
          </div>
          <span className="[&_button]:text-canvas/80 [&_button:focus-visible]:outline-canvas [&_button:hover]:bg-canvas/10 [&_button:hover]:text-canvas">
            <CopyButton value={code[view]} label={`Copy ${LABEL[view]}`} text="Copy" />
          </span>
        </div>
      </div>
      {/* Focusable so a keyboard can scroll a long line; the ring is light on navy. */}
      <pre tabIndex={0} aria-label={LABEL[view]} className="overflow-x-auto px-4 py-3 font-mono text-xs leading-relaxed whitespace-pre text-sky focus-visible:outline-canvas focus-visible:-outline-offset-2"><code>{code[view]}</code></pre>
      <p className="border-t border-canvas/15 px-4 py-2 text-[11px] text-canvas/70">
        Use a key issued for this merchant in place of <code className="font-mono">okw_…</code>. Every Okwan surface is read-only.
      </p>
    </section>
  );
}
