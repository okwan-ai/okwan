"use client";

import { useId, useState } from "react";
import { CHANNELS, type Day } from "@/lib/usage-shape";

/**
 * Requests per day for the window: one series, one hue, so no legend. Each
 * day is a button (the hit target is the whole column, not the bar), with
 * the breakdown on hover and focus; the largest day is labelled; a table
 * view below carries every number without hovering.
 */
export function UsageChart({ days, title }: { days: Day[]; title: string }) {
  const id = useId();
  const [active, setActive] = useState<number | null>(null);
  const max = Math.max(...days.map((d) => d.total), 1);
  const peak = days.reduce((p, d, i) => (d.total > days[p].total ? i : p), 0);
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];
  const cols = CHANNELS.filter((c) => days.some((d) => d.byChannel[c.key]));
  const fmt = (d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

  return (
    <figure aria-labelledby={`${id}-title`} className="min-w-0">
      <figcaption id={`${id}-title`} className="mb-3 text-sm font-semibold">{title}</figcaption>
      <div className="relative">
        {/* Recessive gridlines with their tick values. */}
        <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 bottom-6">
          {ticks.map((t) => (
            <div key={t} className="absolute inset-x-0 flex items-end" style={{ bottom: `${(t / top) * 100}%` }}>
              <span className="w-10 pr-2 text-right text-[10px] leading-none text-ink-soft tabular-nums">{t.toLocaleString("en-US")}</span>
              <span className="h-px flex-1 bg-line" />
            </div>
          ))}
        </div>
        <div className="ml-10 flex h-40 items-end gap-0.5 pb-6">
          {days.map((d, i) => {
            const h = (d.total / top) * 100;
            return (
              <button
                key={d.day}
                type="button"
                aria-label={`${fmt(d.day)}: ${d.total.toLocaleString("en-US")} request${d.total === 1 ? "" : "s"}`}
                onPointerEnter={() => setActive(i)}
                onPointerLeave={() => setActive((a) => (a === i ? null : a))}
                onFocus={() => setActive(i)}
                onBlur={() => setActive((a) => (a === i ? null : a))}
                className="group relative flex h-full min-w-0 flex-1 items-end justify-center rounded-sm focus-visible:bg-ink/5"
              >
                <span
                  aria-hidden
                  className={`block w-full max-w-6 rounded-t-[4px] ${active === i ? "bg-ink" : "bg-navy"} ${d.total === 0 ? "opacity-0" : ""}`}
                  style={{ height: `${Math.max(h, d.total ? 2 : 0)}%` }}
                />
                {i === peak && d.total > 0 && active !== i && (
                  <span aria-hidden className="absolute -top-4 text-[10px] font-medium text-ink tabular-nums" style={{ bottom: `${h}%` }}>
                    {d.total.toLocaleString("en-US")}
                  </span>
                )}
                {active === i && (
                  <span
                    role="tooltip"
                    className={`pointer-events-none absolute bottom-[calc(100%+4px)] z-10 w-48 rounded-lg border border-line bg-surface px-3 py-2 text-left text-xs shadow-md ${
                      // Clamped to the chart's edges, so an end bar's readout stays on screen.
                      i < days.length * 0.25 ? "left-0" : i > days.length * 0.75 ? "right-0" : "left-1/2 -translate-x-1/2"
                    }`}
                  >
                    <span className="block text-[11px] text-ink-soft">{fmt(d.day)}</span>
                    <span className="block text-base font-semibold tabular-nums">{d.total.toLocaleString("en-US")}</span>
                    {CHANNELS.filter((c) => d.byChannel[c.key]).map((c) => (
                      <span key={c.key} className="flex justify-between gap-2 text-ink-soft">
                        <span>{c.label}</span>
                        <span className="font-medium text-ink tabular-nums">{d.byChannel[c.key].toLocaleString("en-US")}</span>
                      </span>
                    ))}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <div aria-hidden className="ml-10 flex justify-between text-[10px] text-ink-soft">
          <span>{fmt(days[0].day)}</span>
          <span>{fmt(days[Math.floor(days.length / 2)].day)}</span>
          <span>{fmt(days[days.length - 1].day)}</span>
        </div>
      </div>
      <details className="mt-3 text-xs">
        <summary className="inline-flex min-h-11 cursor-pointer items-center text-ink-soft underline-offset-4 hover:underline">Table view</summary>
        <table className="mt-2 w-full text-left">
          <thead>
            <tr className="text-ink-soft">
              <th scope="col" className="py-1 font-medium">Day</th>
              <th scope="col" className="py-1 text-right font-medium">Requests</th>
              {cols.map((c) => <th key={c.key} scope="col" className="py-1 pl-3 text-right font-medium">{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {days.filter((d) => d.total).map((d) => (
              <tr key={d.day} className="border-t border-line">
                <td className="py-1">{fmt(d.day)}</td>
                <td className="py-1 text-right tabular-nums">{d.total.toLocaleString("en-US")}</td>
                {cols.map((c) => <td key={c.key} className="py-1 pl-3 text-right text-ink-soft tabular-nums">{(d.byChannel[c.key] ?? 0).toLocaleString("en-US")}</td>)}
              </tr>
            ))}
            {!days.some((d) => d.total) && <tr><td colSpan={2 + cols.length} className="py-2 text-ink-soft">No requests in this window.</td></tr>}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/** Clean axis steps (0, 1, 2 · 0, 5, 10 · 0, 100, 200 …) up to the max. */
function niceTicks(max: number): number[] {
  const raw = max / 3;
  const pow = 10 ** Math.floor(Math.log10(Math.max(raw, 1)));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? pow * 10;
  const top = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let t = 0; t <= top; t += step) out.push(t);
  return out;
}
