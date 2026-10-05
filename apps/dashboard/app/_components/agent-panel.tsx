"use client";

import Link from "next/link";
import { useState } from "react";
import { claudeDesktopConfig } from "@/lib/mcp-clients";
import { CopyButton } from "./ui/copy-button";
import { ReadOnlyChip } from "./ui/read-only-chip";
import { Segmented } from "./ui/segmented";

type View = "mcp" | "rest" | "prompt" | "config";

const LABEL: Record<View, string> = {
  mcp: "MCP call",
  rest: "REST",
  prompt: "Agent prompt",
  config: "Client config",
};

/**
 * The same result on the channels that exist hosted: the MCP tool call
 * (okwan_reconcile with name "rails"; the hosted server has no
 * reconcile_across_rails), the REST route, a prompt that hands the work to
 * an agent, and the MCP client config. The page's one navy panel (§2).
 *
 * The key is always a placeholder: a key is shown once at issue, and this
 * panel never holds one; the footer names whose key goes in its place.
 * Read-only by construction: the chip says so, the prompt says so, and no
 * channel an agent reaches can write to a payment rail.
 */
export function AgentPanel({ apiBase, outcome, views = ["mcp", "rest", "prompt"], server = "okwan", scope = "merchant", keyFor, setupHref, id }: {
  apiBase: string;
  /** An outcome to filter on (e.g. "collected_twice"); all when absent. */
  outcome?: string;
  views?: View[];
  server?: string;
  /** Whose key the footer asks for when `keyFor` is absent: a merchant's, or the workspace's own. */
  scope?: "merchant" | "workspace";
  /** Whose key goes in place of okw_…, by name ("Kofi's Store", "your workspace"). */
  keyFor?: string;
  /** Where to set up an agent for this data (Agents, preselected). */
  setupHref?: string;
  id?: string;
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
    // Claude Desktop's form, shared with the client tabs (lib/mcp-clients.ts).
    config: claudeDesktopConfig(apiBase, server),
  };

  return (
    <section id={id} aria-label="Same result for agents" className="min-w-0 scroll-mt-6 overflow-hidden rounded-xl bg-navy text-canvas">
      <div className="flex items-center gap-2 border-b border-canvas/15 py-1.5 pr-1.5 pl-4">
        <p className="hidden shrink-0 text-xs font-medium text-canvas/80 sm:block">Same result for your agents</p>
        <ReadOnlyChip tone="navy" />
        {/* Scrolls sideways on a phone; the padding keeps the focus ring inside the clip. */}
        <div className="-m-1 min-w-0 flex-1 overflow-x-auto p-1 [scrollbar-width:none]">
          <Segmented tone="navy" label="Channel" value={view} onChange={(k) => setView(k as View)} items={views.map((v) => ({ key: v, label: LABEL[v] }))} />
        </div>
        <span className="shrink-0 [&_button]:text-canvas/80 [&_button:focus-visible]:outline-canvas [&_button:hover]:bg-canvas/10 [&_button:hover]:text-canvas">
          <CopyButton value={code[view]} label={`Copy ${LABEL[view]}`} />
        </span>
      </div>
      {/* Focusable so a keyboard can scroll a long line; the ring is light on navy.
          A prompt wraps; code keeps its lines and fades at the edge on a phone. */}
      <pre
        tabIndex={0}
        aria-label={LABEL[view]}
        className={`overflow-x-auto px-4 py-3 font-mono text-xs leading-relaxed text-sky focus-visible:outline-canvas focus-visible:-outline-offset-2 ${
          view === "prompt" ? "whitespace-pre-wrap break-words" : "whitespace-pre [mask-image:linear-gradient(to_right,black_90%,transparent)] sm:[mask-image:none]"
        }`}
      >
        <code>{code[view]}</code>
      </pre>
      <p className="border-t border-canvas/15 px-4 py-2 text-xs text-canvas/70">
        Replace <code className="font-mono">okw_…</code> with {keyFor ?? (scope === "workspace" ? "your workspace" : "this merchant")}&apos;s key.
        {setupHref && (
          <>
            {" · "}
            <Link href={setupHref} className="underline underline-offset-4 hover:text-canvas focus-visible:outline-canvas">Set up an agent →</Link>
          </>
        )}
      </p>
    </section>
  );
}
