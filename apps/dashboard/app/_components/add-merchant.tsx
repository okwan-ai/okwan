"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function AddMerchant() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const el = e.currentTarget;
    const name = String(new FormData(el).get("name") ?? "").trim();
    if (!name) return;
    setBusy(true);
    setError(null);
    const res = await fetch("/api/merchants", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setError(data.detail ?? "something went wrong");
    el.reset();
    router.refresh();
  }

  return (
    <form onSubmit={submit} className="card p-6">
      <label className="block space-y-1.5">
        <span className="text-sm font-medium">Add merchant</span>
        <div className="flex flex-wrap gap-3">
          <input name="name" required maxLength={200} className="field flex-1" placeholder="Merchant name" />
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Adding…" : "Add merchant"}
          </button>
        </div>
      </label>
      {error && <p role="alert" className="mt-4 text-sm text-red-700">{error}</p>}
    </form>
  );
}
