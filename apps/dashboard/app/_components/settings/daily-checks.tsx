"use client";

import { useId, useState } from "react";
import type { NotifySettings } from "@/lib/notify";
import { railLabel } from "@/lib/finding";

/** The range the API takes for each number (okwan_api/notify.py). */
const DAYS = { min: 1, max: 365 };
const AMOUNT = { min: 0, max: 1_000_000_000_000 };

/** A whole number within the range, from what was typed, or null. */
function whole(text: string, range: { min: number; max: number }): number | null {
  const t = text.replace(/[,\s]/g, "");
  if (!/^\d{1,13}$/.test(t)) return null;
  const n = Number(t);
  return n >= range.min && n <= range.max ? n : null;
}

/**
 * The account's choices about the daily check and its mail (§9 2026-11-11,
 * 2026-11-23): whether every connected merchant is checked once a day,
 * whether what is new on Open items is mailed, after how many days an
 * item still open is mailed once more, and from what amount an open item
 * is named in every mail. Each switch saves when it is pressed, each
 * number when it is left. A daily check is a check like any other: it
 * counts toward the plan and shows in each merchant's history as "Daily
 * check".
 */
export function DailyChecks({ initial }: { initial: NotifySettings }) {
  const [s, setS] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const checksId = useId();
  const mailId = useId();
  const daysId = useId();
  const amountId = useId();
  const [daysText, setDaysText] = useState(String(initial.aging_days));
  const [amountText, setAmountText] = useState(initial.large_amount.toLocaleString("en-US"));

  type Change = Partial<Pick<NotifySettings, "daily_checks" | "finding_emails" | "aging_days" | "large_amount">>;

  async function save(change: Change) {
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
      setDaysText(String(before.aging_days));
      setAmountText(before.large_amount.toLocaleString("en-US"));
      return setError(typeof data.detail === "string" ? data.detail : "That didn't save. Try again.");
    }
    const saved = data as NotifySettings;
    setS(saved);
    setDaysText(String(saved.aging_days));
    setAmountText(saved.large_amount.toLocaleString("en-US"));
  }

  function saveDays() {
    const n = whole(daysText, DAYS);
    if (n === null) return setError(`Days are a whole number from ${DAYS.min} to ${DAYS.max}.`);
    if (n !== s.aging_days) save({ aging_days: n });
  }

  function saveAmount() {
    const n = whole(amountText, AMOUNT);
    if (n === null) return setError("The amount is a whole number, 0 or more, with no currency sign.");
    if (n !== s.large_amount) save({ large_amount: n });
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
        <input id={mailId} type="checkbox" className="mt-1" checked={s.finding_emails} disabled={busy}
          onChange={(e) => save({ finding_emails: e.target.checked })} />
        <span>
          <span className="font-medium">Email me what&apos;s new on Open items</span>
          <span className="block text-xs text-ink-soft">
            One email a day at most{s.email ? <>, to <span className="font-mono">{s.email}</span></> : ""}, and only when
            something is new: each merchant with an item that is new, or now open {s.aging_days} days or more, by
            check, with how many and how much, whichever check found it, files included. Never a reference, a
            customer or a payment record, and nothing about an item someone has explained or resolved.
          </span>
        </span>
      </label>
      <div className="grid gap-3 pl-7 text-sm sm:grid-cols-2">
        <label htmlFor={daysId} className="block">
          <span className="font-medium">Email an item again once it&apos;s open</span>
          <span className="mt-1 flex items-center gap-2">
            <input id={daysId} type="text" inputMode="numeric" autoComplete="off" maxLength={3}
              className="w-20 rounded-md border border-line bg-surface px-2 py-1 font-mono" value={daysText}
              disabled={busy || !s.finding_emails} onChange={(e) => setDaysText(e.target.value)} onBlur={saveDays}
              onKeyDown={(e) => { if (e.key === "Enter") saveDays(); }} />
            <span className="text-ink-soft">days</span>
          </span>
        </label>
        <label htmlFor={amountId} className="block">
          <span className="font-medium">Name in every email while open, from</span>
          <span className="mt-1 flex items-center gap-2">
            <input id={amountId} type="text" inputMode="numeric" autoComplete="off" maxLength={17}
              className="w-32 rounded-md border border-line bg-surface px-2 py-1 font-mono" value={amountText}
              disabled={busy || !s.finding_emails} onChange={(e) => setAmountText(e.target.value)} onBlur={saveAmount}
              onKeyDown={(e) => { if (e.key === "Enter") saveAmount(); }} />
            <span className="text-ink-soft">of its own currency (0: none)</span>
          </span>
        </label>
      </div>
      {s.paused_by_operator && (
        <p className="text-xs text-ink">Daily checks are paused on Okwan&apos;s side right now. Your choices are kept.</p>
      )}
      {s.emails_blocked && s.finding_emails && (
        <p className="text-xs text-ink">Okwan isn&apos;t sending these emails yet. Open items still show in the dashboard.</p>
      )}
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    </div>
  );
}
