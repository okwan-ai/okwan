"use client";

import { useState } from "react";
import { Button } from "../ui/button";

/**
 * Revoke by id. The API lists no keys (a key is a hash and a prefix), so
 * the id comes from where it was stored at issue. The API guards by
 * subtree: the workspace's keys and every merchant's can be revoked here.
 * Effective on the next request; a key outside the subtree reads as
 * nonexistent. Borderless: the Card around it names it.
 */
export function RevokeKey() {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const el = e.currentTarget;
    const id = String(new FormData(el).get("key_id") ?? "").trim();
    if (!id) return;
    setBusy(true);
    setError(null);
    setDone(null);
    const res = await fetch(`/api/keys/${encodeURIComponent(id)}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setError(data.detail ?? "something went wrong");
    el.reset();
    setDone(id);
  }

  return (
    <form onSubmit={submit}>
      <p className="max-w-xl text-sm text-ink-soft">
        Paste the key id shown at issue (<code className="font-mono">key_…</code>). Works for your workspace&apos;s keys and
        every merchant&apos;s. To rotate, issue a new key first.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 text-sm">
          <span className="mb-1 block font-medium">Key id</span>
          <input name="key_id" required pattern="key_[A-Za-z0-9]+" title="A key id starts with key_ followed by letters and digits" className="field font-mono" placeholder="key_…" autoComplete="off" spellCheck={false} />
        </label>
        <Button type="submit" variant="secondary" disabled={busy}>{busy ? "Revoking…" : "Revoke"}</Button>
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
      {done && (
        <p role="status" className="mt-3 text-sm text-ok">
          <span aria-hidden className="mr-1 font-mono">✓</span>Revoked <code className="font-mono">{done}</code>. Its next request is refused.
        </p>
      )}
    </form>
  );
}
