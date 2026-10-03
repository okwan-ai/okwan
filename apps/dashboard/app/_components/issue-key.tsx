"use client";

import { useState } from "react";
import { tenantQuery } from "./connector-form";
import { Button } from "./ui/button";
import { CopyButton } from "./ui/copy-button";

type Issued = { key_id: string; prefix: string; secret: string };

/** `tenantId` issues for a merchant's tenant; without it, the signed-in tenant. */
export function IssueKey({ tenantId }: { tenantId?: string } = {}) {
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<Issued | null>(null);
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
  }

  if (issued) {
    return (
      <div className="rounded-xl border border-ink bg-surface p-5">
        <p className="text-sm font-medium">Copy this key now. Okwan stores only a hash and can&apos;t show it again.</p>
        <div className="mt-3 flex items-center gap-2">
          <code className="field flex-1 overflow-x-auto font-mono text-sm whitespace-nowrap">{issued.secret}</code>
          <CopyButton value={issued.secret} label="Copy key" text="Copy" />
        </div>
        <p className="mt-3 text-xs text-ink-soft">
          Key id <code className="font-mono">{issued.key_id}</code>. Send it as{" "}
          <code className="font-mono">Authorization: Bearer okw_…</code>
        </p>
        <Button variant="primary" className="mt-5" onClick={() => setIssued(null)}>
          I&apos;ve stored it
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-line bg-surface p-5">
      <p className="max-w-xl text-sm text-ink-soft">
        A key authenticates REST, SQL and the hosted MCP at <code className="font-mono">/mcp/</code>. It&apos;s shown
        once at issue. Issuing another doesn&apos;t revoke this one.
      </p>
      <Button variant="primary" onClick={issue} disabled={busy}>
        {busy ? "Issuing…" : "Issue a key"}
      </Button>
      {error && <p role="alert" className="w-full text-sm text-danger">{error}</p>}
    </div>
  );
}
