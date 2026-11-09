"use client";

import { useEffect, useId, useRef, useState } from "react";
import { tenantQuery } from "./connector-form";
import { Button } from "./ui/button";
import { CopyButton } from "./ui/copy-button";

type Issued = { key_id: string; prefix: string; scope?: string; secret: string };

/** `tenantId` issues for a merchant's tenant; without it, the signed-in tenant.
 *  `bare` drops the idle box's own border when a Card already frames it.
 *  A key is read-only: it reads data over REST, SQL and MCP. `allowAdmin`
 *  offers an unticked box for a key that can also administer (merchants,
 *  keys, connections, files), for an ISV's own backend. MCP setup never
 *  passes it: an AI client's key is always read-only. */
export function IssueKey({ tenantId, onShowing, bare = false, allowAdmin = false }: { tenantId?: string; onShowing?: (showing: boolean) => void; bare?: boolean; allowAdmin?: boolean } = {}) {
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<Issued | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [admin, setAdmin] = useState(false);
  const adminId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  // The key appears where the button was: move focus to it, and back after.
  useEffect(() => {
    if (issued) panel.current?.focus();
    onShowing?.(issued !== null);
  }, [issued, onShowing]);

  async function issue() {
    setBusy(true);
    setError(null);
    const res = await fetch(`/api/key${tenantQuery(tenantId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: allowAdmin && admin ? "admin" : "read" }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setError(data.detail ?? "something went wrong");
    setIssued(data as Issued);
  }

  if (issued) {
    return (
      <div ref={panel} tabIndex={-1} role="region" aria-label="Your new API key" className="rounded-xl border border-ink bg-surface p-5 outline-none">
        <p className="text-sm font-medium">Copy this key now. Okwan stores only a hash and can&apos;t show it again.</p>
        <div className="mt-3 flex items-center gap-2">
          <code className="field flex-1 overflow-x-auto font-mono text-sm whitespace-nowrap">{issued.secret}</code>
          <CopyButton value={issued.secret} label="Copy key" text="Copy" />
        </div>
        <p className="mt-3 text-xs text-ink-soft">
          {issued.scope === "admin"
            ? "Admin key: it reads data and can also manage merchants, keys, connections and files. Keep it on your own servers."
            : "Read-only key: it reads data over REST, SQL and MCP, and can't change anything."}{" "}
          Key id <code className="font-mono">{issued.key_id}</code>. Send it as{" "}
          <code className="font-mono">Authorization: Bearer okw_…</code>
        </p>
        <Button variant="primary" className="mt-5" onClick={() => { setIssued(null); requestAnimationFrame(() => trigger.current?.focus()); }}>
          I&apos;ve stored it
        </Button>
      </div>
    );
  }

  return (
    <div className={`flex flex-wrap items-center justify-between gap-4${bare ? "" : " rounded-xl border border-line bg-surface p-5"}`}>
      <p className="max-w-xl text-sm text-ink-soft">Issuing another key never revokes this one.</p>
      <Button ref={trigger} variant="primary" onClick={issue} disabled={busy}>
        {busy ? "Issuing…" : allowAdmin && admin ? "Issue an admin key" : "Issue a key"}
      </Button>
      {allowAdmin && (
        <label htmlFor={adminId} className="flex w-full items-start gap-2 text-sm">
          <input id={adminId} type="checkbox" className="mt-1" checked={admin} onChange={(e) => setAdmin(e.target.checked)} />
          <span>
            <span className="font-medium">Admin access</span>
            <span className="block text-xs text-ink-soft">
              Can also add merchants, issue and revoke keys, and change connections and files. Only for your own
              backend; never paste an admin key into an AI client.
            </span>
          </span>
        </label>
      )}
      {error && <p role="alert" className="w-full text-sm text-danger">{error}</p>}
    </div>
  );
}

/**
 * Issue for any tenant in the subtree: the workspace itself or one merchant.
 * The choice is locked while a key is on screen, because switching would
 * discard a secret that exists nowhere else.
 */
export function KeyIssuer({ tenants }: { tenants: { id: string | null; name: string }[] }) {
  const [id, setId] = useState<string | null>(tenants[0]?.id ?? null);
  const [showing, setShowing] = useState(false);
  const note = useId();
  return (
    <div className="space-y-4">
      <label className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-medium">For</span>
        <select
          value={id ?? ""}
          onChange={(e) => setId(e.target.value || null)}
          disabled={showing}
          aria-describedby={showing ? note : undefined}
          className="field w-auto min-w-56 py-2 disabled:opacity-60 max-sm:min-w-0 max-sm:flex-1"
        >
          {tenants.map((t) => <option key={t.id ?? "self"} value={t.id ?? ""}>{t.name}</option>)}
        </select>
        {showing && <span id={note} className="text-xs text-ink-soft">Store the key below first; it can&apos;t be shown again.</span>}
      </label>
      <IssueKey key={id ?? "self"} tenantId={id ?? undefined} onShowing={setShowing} bare allowAdmin />
    </div>
  );
}
