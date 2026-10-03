"use client";

import { useState } from "react";
import { buttonClass } from "./ui/button";

/**
 * Asks for the password again on purpose. The API completes a signup only
 * with the password that started it, so a link from a signup someone else
 * made for this address cannot produce an account they can sign into.
 */
export function VerifyForm({ token }: { token: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    const res = await fetch("/api/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, password: form.get("password") }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setError(data.detail ?? "something went wrong");
    window.location.href = "/overview";
  }

  return (
    <form onSubmit={submit} className="card space-y-5 p-6 sm:p-8">
      <label className="block space-y-1.5">
        <span className="text-sm font-medium">Password you signed up with</span>
        <input name="password" type="password" required autoComplete="current-password" className="field" />
      </label>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      <button type="submit" className={buttonClass("primary")} disabled={busy}>
        {busy ? "…" : "Verify and create workspace"}
      </button>
    </form>
  );
}
