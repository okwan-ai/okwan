"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { IconCheck, IconChevron } from "./ui/icons";

/** `done: null` marks a step the API can't confirm (it lists no keys and
 *  sees no MCP clients), so the viewer ticks it off; that tick is kept in
 *  this browser only. */
export type Step = { id: string; label: string; href: string; done: boolean | null };

const STORAGE = "okwan.setup.ticked";

function readTicked(): string[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE) ?? "[]");
  } catch {
    return [];
  }
}

export function SetupChecklist({ steps }: { steps: Step[] }) {
  // Rendered after mount: the ticks live in this browser, and a list that
  // flashed and vanished on hydration would be worse than one that appears.
  const [ticked, setTicked] = useState<string[] | null>(null);
  useEffect(() => setTicked(readTicked()), []);
  if (ticked === null) return null;

  const isDone = (s: Step) => s.done ?? ticked.includes(s.id);
  const left = steps.filter((s) => !isDone(s)).length;
  if (left === 0) return null;

  function tick(id: string) {
    const next = [...new Set([...(ticked ?? []), id])];
    setTicked(next);
    try {
      localStorage.setItem(STORAGE, JSON.stringify(next));
    } catch {
      // Storage blocked: the tick lasts for this view only.
    }
  }

  return (
    <section aria-labelledby="setup-title" className="mt-10 rounded-xl border border-line bg-surface">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-5 py-3">
        <h2 id="setup-title" className="text-base font-semibold">Get set up</h2>
        <p className="text-sm text-ink-soft">{steps.length - left} of {steps.length} done</p>
      </div>
      <ol className="divide-y divide-line">
        {steps.map((s, i) => {
          const done = isDone(s);
          return (
            <li key={s.id} className="flex min-h-14 items-center gap-3 px-5 py-1.5">
              <span
                aria-hidden
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs ${
                  done ? "border-ok bg-ok text-surface" : "border-line text-ink-soft"
                }`}
              >
                {done ? <IconCheck className="h-3.5 w-3.5" /> : i + 1}
              </span>
              <span className={`flex-1 text-sm ${done ? "text-ink-soft line-through" : ""}`}>
                {s.label}
                <span className="sr-only">{done ? " (done)" : " (to do)"}</span>
              </span>
              {!done && s.done === null && (
                <button
                  type="button"
                  onClick={() => tick(s.id)}
                  className="min-h-11 rounded-lg px-2 text-xs text-ink-soft hover:bg-ink/5 hover:text-ink"
                >
                  Mark done
                </button>
              )}
              {!done && (
                <Link
                  href={s.href}
                  aria-label={`${s.label}: go`}
                  className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-ink-soft hover:bg-ink/5 hover:text-ink"
                >
                  <IconChevron />
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
