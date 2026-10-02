"use client";

import Link from "next/link";
import { useState } from "react";

type Mode = "signup" | "signin";

export function AuthForm({ mode }: { mode: Mode }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    const res = await fetch(mode === "signup" ? "/api/signup" : "/api/signin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setError(data.detail ?? "something went wrong");
    if (mode === "signup") return setSent(true);
    window.location.href = "/connections";
  }

  if (sent) {
    return (
      <div className="card p-8">
        <h2 className="font-display text-2xl">Check your inbox</h2>
        <p className="mt-3 text-ink-soft">
          If this address can be registered, a verification link is on its way. Your
          account and workspace are created when you open it. The link expires in 24
          hours.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="card space-y-5 p-8">
      <label className="block space-y-1.5">
        <span className="text-sm font-medium">Work email</span>
        <input name="email" type="email" required autoComplete="email" className="field" />
      </label>
      <label className="block space-y-1.5">
        <span className="text-sm font-medium">Password</span>
        <input
          name="password"
          type="password"
          required
          minLength={mode === "signup" ? 12 : 1}
          autoComplete={mode === "signup" ? "new-password" : "current-password"}
          className="field"
        />
        {mode === "signup" && (
          <span className="block text-xs text-ink-soft">At least 12 characters.</span>
        )}
      </label>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex items-center justify-between gap-4 pt-1">
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "…" : mode === "signup" ? "Create account" : "Sign in"}
        </button>
        <Link
          href={mode === "signup" ? "/signup?mode=signin" : "/signup"}
          className="text-sm text-ink-soft underline-offset-4 hover:underline"
        >
          {mode === "signup" ? "Have an account? Sign in" : "New here? Create an account"}
        </Link>
      </div>
    </form>
  );
}
