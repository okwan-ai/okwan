"use client";

import { useId, useState } from "react";
import type { NotifySettings } from "@/lib/notify";
import { railLabel } from "@/lib/finding";

/**
 * The account's two choices about the daily check (§9 2026-11-11): whether
 * every connected merchant is checked once a day, and whether a new
 * finding is mailed. Each switch saves when it is pressed. A daily check
 * is a check like any other: it counts toward the plan and shows in each
 * merchant's history as "Daily check".
 */
export function DailyChecks({ initial }: { initial: NotifySettings }) {
  const [s, setS] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const checksId = useId();
  const mailId = useId();

  async function save(change: Partial<Pick<NotifySettings, "daily_checks" | "finding_emails">>) {
    // The switch moves when it is pressed, and moves back if the save fails.
    const before = s;
    setS({ ...s, ...change });
    setBusy(true);
    setError(null);
    const res = await fetch("/api/notify", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    }).catch(() => null);
    const data = res ? await res.json().catch(() => ({})) : {};
    setBusy(false);
    if (!res || !res.ok) {
      setS(before);
      return setError(data.detail ?? "That didn't save. Try again.");
    }
    setS(data as NotifySettings);
  }

  const needs = s.needs.map(railLabel);
  const list = needs.length > 1 ? `${needs.slice(0, -1).join(", ")} and ${needs[needs.length - 1]}` : needs[0] ?? "";
  return (
    <div className="space-y-4 rounded-xl border border-line bg-surface p-5">
      <label htmlFor={checksId} className="flex items-start gap-3 text-sm">
        <input id={checksId} type="checkbox" className="mt-1" checked={s.daily_checks} disabled={busy}
          onChange={(e) => save({ daily_checks: e.target.checked })} />
        <span>
          <span className="font-medium">Check every connected merchant once a day</span>
          <span className="block text-xs text-ink-soft">
            A merchant is checked when its {list} are all connected and its last daily check is a day old. Each one counts
            toward your plan like a check you run, and none runs once the plan is used up.
          </span>
        </span>
      </label>
      <label htmlFor={mailId} className="flex items-start gap-3 text-sm">
        <input id={mailId} type="checkbox" className="mt-1" checked={s.finding_emails} disabled={busy || !s.daily_checks}
          onChange={(e) => save({ finding_emails: e.target.checked })} />
        <span>
          <span className="font-medium">Email me when a daily check finds something new</span>
          <span className="block text-xs text-ink-soft">
            One email a day at most{s.email ? <>, to <span className="font-mono">{s.email}</span></> : ""}: each merchant with
            something new, how many orders and how much. Never an order, a customer or a payment record. A finding
            from a check later in the day is in the next day&apos;s email.
          </span>
        </span>
      </label>
      {s.paused_by_operator && (
        <p className="text-xs text-ink">Daily checks are paused on Okwan&apos;s side right now. Your choices are kept.</p>
      )}
      {s.emails_blocked && s.daily_checks && s.finding_emails && (
        <p className="text-xs text-ink">Okwan isn&apos;t sending these emails yet. New findings still show in the dashboard.</p>
      )}
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    </div>
  );
}
