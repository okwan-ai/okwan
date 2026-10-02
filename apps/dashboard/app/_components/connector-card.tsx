"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

export type ConnectorView = {
  name: string;
  description: string;
  credential_fields: string[];
  probe: string | null;
  stored: string[];
};

type TestResult = {
  status: "rows" | "empty" | "failed" | "missing" | "untestable";
  operation?: string;
  rows?: number;
  detail: string;
};

// Identifiers, not secrets: shown as typed. Everything else is masked.
const PLAIN = /_domain$/;

const TONE: Record<TestResult["status"], string> = {
  rows: "border-emerald-700/30 bg-emerald-50 text-emerald-900",
  empty: "border-volt-deep/50 bg-volt/15 text-ink",
  failed: "border-red-700/30 bg-red-50 text-red-900",
  missing: "border-line bg-canvas text-ink-soft",
  untestable: "border-line bg-canvas text-ink-soft",
};

export function ConnectorCard({ c }: { c: ConnectorView }) {
  const router = useRouter();
  const form = useRef<HTMLFormElement>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const complete = c.credential_fields.every((f) => c.stored.includes(f));
  const state = complete ? "Configured" : c.stored.length ? "Partly configured" : "Not configured";

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
    const res = await fetch(`/api/connections/${c.name}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setError(data.detail ?? "something went wrong");
    setResult(data as TestResult);
    router.refresh();
  }

  return (
    <article className="card p-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-display text-2xl capitalize">{c.name}</h2>
          <p className="mt-1 text-sm text-ink-soft">{c.description}</p>
        </div>
        <span
          className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium ${
            complete ? "bg-ink text-canvas" : "border border-line text-ink-soft"
          }`}
        >
          {state}
        </span>
      </header>

      <form ref={form} onSubmit={submit} className="mt-6 space-y-4" autoComplete="off">
        {c.credential_fields.map((f) => (
          <label key={f} className="block space-y-1.5">
            <span className="flex items-center justify-between text-sm">
              <code className="font-mono">{f}</code>
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

        <div className="flex flex-wrap items-center gap-3 pt-1">
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Testing…" : "Test and Save"}
          </button>
          <span className="text-xs text-ink-soft">
            {c.probe ? (
              <>Saves to the vault, then makes one real read: <code className="font-mono">{c.probe}</code>.</>
            ) : (
              "Saves to the vault. No list call can run without your own IDs, so there is no live test."
            )}
          </span>
        </div>
      </form>

      {error && <p role="alert" className="mt-4 text-sm text-red-700">{error}</p>}
      {result && (
        <div role="status" className={`mt-4 rounded-lg border px-4 py-3 text-sm ${TONE[result.status]}`}>
          <p className="font-medium">
            {result.status === "rows" && `✓ ${result.rows} real row${result.rows === 1 ? "" : "s"} came back`}
            {result.status === "empty" && "Credentials work — the list came back empty"}
            {result.status === "failed" && "The read failed"}
            {result.status === "missing" && "Not everything is stored yet"}
            {result.status === "untestable" && "Saved, not tested"}
          </p>
          <p className="mt-0.5 opacity-80">
            {result.operation && <code className="font-mono">{result.operation} · </code>}
            {result.detail}
          </p>
        </div>
      )}
    </article>
  );
}
