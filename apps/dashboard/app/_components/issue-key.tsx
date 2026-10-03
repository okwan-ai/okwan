"use client";

import { useState } from "react";
import { tenantQuery } from "./connector-card";

type Issued = { key_id: string; prefix: string; secret: string };

/** `tenantId` issues for a merchant's tenant; without it, the signed-in tenant. */
export function IssueKey({ tenantId }: { tenantId?: string } = {}) {
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<Issued | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function issue() {
    setBusy(true);
    setError(null);
    const res = await fetch(`/api/key${tenantQuery(tenantId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setError(data.detail ?? "something went wrong");
    setIssued(data as Issued);
    setCopied(false);
  }

  if (issued) {
    return (
      <div className="card p-6">
        <p className="text-sm font-medium">Your key. Copy it now; Okwan stores only a hash and cannot show it again.</p>
        <div className="mt-4 flex items-center gap-3">
          <code className="field flex-1 overflow-x-auto whitespace-nowrap font-mono text-sm">{issued.secret}</code>
          <button
            className="btn btn-secondary"
            onClick={async () => {
              await navigator.clipboard.writeText(issued.secret);
              setCopied(true);
            }}
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <p className="mt-3 text-xs text-ink-soft">
          Key id <code className="font-mono">{issued.key_id}</code>. Send it as{" "}
          <code className="font-mono">Authorization: Bearer okw_…</code>
        </p>
        <button className="btn btn-primary mt-6" onClick={() => setIssued(null)}>
          I&apos;ve stored it
        </button>
      </div>
    );
  }

  return (
    <div className="card p-6">
      <p className="text-ink-soft">
        A key authenticates REST, SQL and the hosted MCP at <code className="font-mono">/mcp/</code>. It is shown
        once at issue. Issuing another does not revoke this one.
      </p>
      {error && <p role="alert" className="mt-4 text-sm text-red-700">{error}</p>}
      <button className="btn btn-primary mt-6" onClick={issue} disabled={busy}>
        {busy ? "Issuing…" : "Issue a key"}
      </button>
    </div>
  );
}
