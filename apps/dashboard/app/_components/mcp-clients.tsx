"use client";

import { type ReactNode, useId, useRef, useState } from "react";
import { MCP_CLIENTS, type McpClientId, mcpClientOf } from "@/lib/mcp-clients";
import { CodeBlock } from "./ui/copy-button";

/**
 * The integrations catalog, as four rows: each MCP client and how it
 * reaches the one hosted URL. A real tablist (arrow keys move, the panel
 * is labelled by its tab); the chosen client is kept in `?client=` so a
 * link can land on one, without a server round trip per click.
 *
 * Snippets keep the `okw_…` placeholder even while IssueKey above may be
 * showing a secret: this component never sees a key.
 */
export function McpClients({ apiBase, server, initial }: { apiBase: string; server: string; initial?: string | null }) {
  const [id, setId] = useState<McpClientId>(mcpClientOf(initial));
  const base = useId();
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const client = MCP_CLIENTS.find((c) => c.id === id) ?? MCP_CLIENTS[0];

  function choose(next: McpClientId, focus = false) {
    setId(next);
    const u = new URL(window.location.href);
    u.searchParams.set("client", next);
    window.history.replaceState(null, "", u.toString());
    if (focus) tabs.current[MCP_CLIENTS.findIndex((c) => c.id === next)]?.focus();
  }

  function onKey(e: React.KeyboardEvent, i: number) {
    const n = MCP_CLIENTS.length;
    const to = e.key === "ArrowRight" ? (i + 1) % n : e.key === "ArrowLeft" ? (i - 1 + n) % n : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : null;
    if (to === null) return;
    e.preventDefault();
    choose(MCP_CLIENTS[to].id, true);
  }

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface">
      <div role="tablist" aria-label="MCP client" className="flex flex-wrap gap-1 border-b border-line bg-canvas/60 p-1.5">
        {MCP_CLIENTS.map((c, i) => {
          const active = c.id === id;
          return (
            <button
              key={c.id}
              ref={(el) => { tabs.current[i] = el; }}
              type="button"
              role="tab"
              id={`${base}-tab-${c.id}`}
              aria-selected={active}
              aria-controls={`${base}-panel`}
              tabIndex={active ? 0 : -1}
              onClick={() => choose(c.id)}
              onKeyDown={(e) => onKey(e, i)}
              className={`min-h-9 rounded-md border-b-2 px-3 text-left text-sm font-medium ${active ? "border-ink bg-surface text-ink shadow-sm" : "border-transparent text-ink-soft hover:text-ink"}`}
            >
              {c.label}
              <span className="block text-xs font-normal text-ink-soft">{c.via}</span>
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id={`${base}-panel`} aria-labelledby={`${base}-tab-${client.id}`} className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-4">
          <ol className="space-y-2.5 text-sm">
            {client.steps.map((s, i) => (
              <li key={i} className="flex gap-3">
                <span aria-hidden className="mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-ink text-[11px] font-semibold text-canvas tabular-nums">{i + 1}</span>
                <span className="min-w-0 break-words"><Rich text={s} /></span>
              </li>
            ))}
          </ol>
          <p className="text-xs text-ink-soft"><span className="font-medium text-ink">Needs:</span> {client.needs}</p>
          {client.pitfalls && (
            <div>
              <p className="mb-1 text-xs font-medium">Where it breaks</p>
              <ul className="space-y-1.5 text-xs text-ink-soft">
                {client.pitfalls.map((p, i) => (
                  <li key={i} className="flex gap-2">
                    <span aria-hidden className="font-mono text-ink">!</span>
                    <span className="min-w-0 break-words"><Rich text={p} /></span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <a href={client.docs.href} target="_blank" rel="noopener" className="inline-block text-xs text-ink-soft underline-offset-4 hover:text-ink hover:underline">
            {client.docs.label} <span aria-hidden>↗</span><span className="sr-only">(opens in a new tab)</span>
          </a>
        </div>
        <div className="min-w-0">
          <CodeBlock label={client.snippetLabel} code={client.snippet(apiBase, server)} />
          <p className="mt-2 text-xs text-ink-soft">Use the key from step 1 in place of <code className="font-mono">okw_…</code>. The server reads only that merchant.</p>
        </div>
      </div>
    </div>
  );
}

/** `**strong**` and `` `code` `` only; nothing else is interpreted. */
function Rich({ text }: { text: string }): ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return parts.map((p, i) => {
    if (p.startsWith("**")) return <strong key={i} className="font-semibold text-ink">{p.slice(2, -2)}</strong>;
    if (p.startsWith("`")) return <code key={i} className="rounded bg-canvas px-1 font-mono text-[12px] text-ink">{p.slice(1, -1)}</code>;
    return p;
  });
}
