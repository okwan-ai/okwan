"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { reportTest, type TestResult } from "@/lib/tab-results";
import { Button } from "./ui/button";

export type ConnectorView = {
  name: string;
  description: string;
  credential_fields: string[];
  probe: string | null;
  stored: string[];
};

// Identifiers, not secrets: shown as typed. Everything else is masked.
const PLAIN = /_domain$/;

/** Test and Save for one connector. `tenantId` targets a merchant's tenant;
 *  without it, the signed-in tenant. */
export function ConnectorForm({ c, tenantId, tenantKey }: { c: ConnectorView; tenantId?: string; tenantKey: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const el = e.currentTarget;
    const fields = Object.fromEntries(
      c.credential_fields.map((f) => [f, String(new FormData(el).get(f) ?? "")]),
    );
    // Values leave the page now; nothing on the client keeps them.
    el.reset();
    setBusy(true);
    setError(null);
    setResult(null);
    const res = await fetch(`/api/connections/${c.name}${tenantQuery(tenantId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setError(data.detail ?? "something went wrong");
    setResult(data as TestResult);
    reportTest(tenantKey, c.name, data as TestResult);
    router.refresh();
  }

  return (
    <div>
      <form onSubmit={submit} className="space-y-4" autoComplete="off">
        {c.credential_fields.map((f) => (
          <label key={f} className="block space-y-1.5">
            <span className="flex flex-wrap items-center justify-between gap-x-2 text-sm">
              <code className="font-mono text-[13px]">{f}</code>
              {c.stored.includes(f) && (
                <span className="text-xs text-ink-soft">stored · leave blank to keep</span>
              )}
            </span>
            <input
              name={f}
              type={PLAIN.test(f) ? "text" : "password"}
              autoComplete="off"
              spellCheck={false}
              className="field font-mono"
              placeholder={c.stored.includes(f) ? "••••••••" : ""}
            />
          </label>
        ))}

        <Button type="submit" variant="primary" disabled={busy} className="w-full">
          {busy ? "Testing…" : "Test and Save"}
        </Button>
        <p className="text-xs text-ink-soft">
          {c.probe ? (
            <>Saves to the vault, then makes one real read: <code className="font-mono">{c.probe}</code>.</>
          ) : (
            "Saves to the vault. No list call can run without your own IDs, so there's no live test."
          )}
        </p>
      </form>

      {error && <p role="alert" className="mt-4 rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
      {result && <TestOutcome result={result} />}
    </div>
  );
}

export const TEST_LABEL: Record<TestResult["status"], { symbol: string; label: string; tone: "ok" | "warn" | "danger" | "neutral" }> = {
  rows: { symbol: "✓", label: "Read works", tone: "ok" },
  empty: { symbol: "✓", label: "Works · list empty", tone: "warn" },
  failed: { symbol: "!", label: "Read failed", tone: "danger" },
  missing: { symbol: "○", label: "Fields missing", tone: "neutral" },
  untestable: { symbol: "–", label: "Saved, not tested", tone: "neutral" },
};

const TONE_BOX = {
  ok: "border-ok/30 bg-ok-soft text-ok",
  warn: "border-ink-soft/50 bg-surface text-ink",
  danger: "border-danger/30 bg-danger-soft text-danger",
  neutral: "border-line bg-canvas text-ink-soft",
};

function TestOutcome({ result }: { result: TestResult }) {
  const t = TEST_LABEL[result.status];
  return (
    <div role="status" className={`mt-4 rounded-lg border px-3 py-2.5 text-sm ${TONE_BOX[t.tone]}`}>
      <p className="font-medium">
        <span aria-hidden className="mr-1.5 font-mono">{t.symbol}</span>
        {result.status === "rows" ? `${result.rows} real row${result.rows === 1 ? "" : "s"} came back` : t.label}
      </p>
      <p className="mt-0.5 break-words text-ink-soft">
        {result.operation && <code className="font-mono">{result.operation} · </code>}
        {result.detail}
      </p>
    </div>
  );
}

export function tenantQuery(tenantId?: string): string {
  return tenantId ? `?tenant=${encodeURIComponent(tenantId)}` : "";
}
