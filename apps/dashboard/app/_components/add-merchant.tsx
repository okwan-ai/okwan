"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Button } from "./ui/button";
import { Modal } from "./ui/dialog";
import { IconPlus } from "./ui/icons";

/** "Add merchant" and its modal. `?add=1` opens it on arrival, so other
 *  pages can link straight to it. A new merchant opens on its Connections. */
export function AddMerchant({ variant = "primary" }: { variant?: "primary" | "secondary" } = {}) {
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  const [open, setOpen] = useState(params.get("add") === "1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function close() {
    setOpen(false);
    setError(null);
    if (params.get("add")) router.replace(path, { scroll: false });
  }

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const name = String(new FormData(e.currentTarget).get("name") ?? "").trim();
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
    setOpen(false);
    router.push(`/merchants/${encodeURIComponent(data.id)}?tab=connections`);
    router.refresh();
  }

  return (
    <>
      <Button variant={variant} onClick={() => setOpen(true)}>
        <IconPlus /> Add merchant
      </Button>
      <Modal open={open} onClose={close} title="Add merchant">
        <form onSubmit={submit} className="space-y-4">
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Merchant name</span>
            <input name="name" required maxLength={200} className="field" data-autofocus autoComplete="off" />
          </label>
          <p className="text-xs text-ink-soft">Its rails and API keys are kept under this merchant, apart from your own.</p>
          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={busy}>{busy ? "Adding…" : "Add merchant"}</Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
