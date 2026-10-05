"use client";

import { useRouter } from "next/navigation";

/**
 * Whose usage Plan & usage shows: the whole workspace, or one merchant.
 * A navigation, so the server reads that tenant's usage; the window
 * (?days=) is kept.
 */
export function UsageScope({ merchants, value, days }: { merchants: { id: string; name: string }[]; value: string | null; days: number }) {
  const router = useRouter();
  function go(next: string) {
    const q = new URLSearchParams({ tab: "plan" });
    if (next) q.set("merchant", next);
    if (days !== 30) q.set("days", String(days));
    router.push(`/settings?${q.toString()}`, { scroll: false });
  }
  return (
    <label className="flex flex-wrap items-center gap-3 text-sm">
      <span className="font-medium">Showing</span>
      <select
        value={value ?? ""}
        onChange={(e) => go(e.target.value)}
        className="field w-auto min-w-56 py-2 max-sm:min-w-0 max-sm:flex-1"
      >
        <option value="">Whole workspace</option>
        {merchants.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
      </select>
    </label>
  );
}
