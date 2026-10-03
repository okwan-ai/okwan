"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { buttonClass } from "./ui/button";
import { IconCheck, IconChevron } from "./ui/icons";

/** `done: null` marks a step the API can't confirm (it lists no keys and
 *  sees no MCP clients), so the viewer ticks it off; that tick is kept in
 *  this browser only. */
export type Step = { id: string; label: string; hint?: string; href: string; done: boolean | null };

const STORAGE = "okwan.setup.ticked";

function readTicked(): string[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE) ?? "[]");
  } catch {
    return [];
  }
}

/**
 * Two shapes. `prominent` (nothing checked yet): the page's first block,
 * with the next step as its one primary button. Otherwise one compact row,
 * progress and the next step, with the full list behind a disclosure.
 * Hidden once all steps are done.
 */
export function SetupChecklist({ steps, prominent = false }: { steps: Step[]; prominent?: boolean }) {
  // Rendered after mount: the ticks live in this browser, and a list that
  // flashed and vanished on hydration would be worse than one that appears.
  const [ticked, setTicked] = useState<string[] | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => setTicked(readTicked()), []);
  if (ticked === null) return null;

  const isDone = (s: Step) => s.done ?? ticked.includes(s.id);
  const done = steps.filter(isDone).length;
  const next = steps.find((s) => !isDone(s));
  if (!next) return null;

  function tick(id: string) {
    const nextTicked = [...new Set([...(ticked ?? []), id])];
    setTicked(nextTicked);
    try {
      localStorage.setItem(STORAGE, JSON.stringify(nextTicked));
    } catch {
      // Storage blocked: the tick lasts for this view only.
    }
  }

  const progress = (
    <div className="flex items-center gap-3">
      <div
        role="progressbar"
        aria-label="Setup progress"
        aria-valuemin={0}
        aria-valuemax={steps.length}
        aria-valuenow={done}
        className="h-1.5 w-24 overflow-hidden rounded-full bg-line"
      >
        <div className="h-full rounded-full bg-ok" style={{ width: `${(done / steps.length) * 100}%` }} />
      </div>
      <span className="text-xs text-ink-soft tabular-nums">{done} of {steps.length}</span>
    </div>
  );

  const list = (
    <ol className="divide-y divide-line">
      {steps.map((s, i) => {
        const d = isDone(s);
        return (
          <li key={s.id} className="flex min-h-12 items-center gap-3 px-5 py-1">
            <span
              aria-hidden
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs ${
                d ? "border-ok bg-ok text-surface" : "border-line text-ink-soft"
              }`}
            >
              {d ? <IconCheck className="h-3.5 w-3.5" /> : i + 1}
            </span>
            <span className={`flex-1 text-sm ${d ? "text-ink-soft" : ""}`}>
              {s.label}
              <span className="sr-only">{d ? " (done)" : " (to do)"}</span>
              {!d && s.hint && <span className="block text-xs text-ink-soft">{s.hint}</span>}
            </span>
            {!d && s.done === null && (
              <button type="button" aria-label={`Mark "${s.label}" done`} onClick={() => tick(s.id)} className="min-h-11 rounded-lg px-2 text-xs text-ink-soft hover:bg-ink/5 hover:text-ink">
                Mark done
              </button>
            )}
            {!d && (
              <Link href={s.href} aria-label={`${s.label}: go`} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-ink-soft hover:bg-ink/5 hover:text-ink">
                <IconChevron />
              </Link>
            )}
          </li>
        );
      })}
    </ol>
  );

  if (prominent) {
    return (
      <section aria-labelledby="setup-title" className="rounded-xl border border-line bg-surface">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line px-5 py-4">
          <div>
            <h2 id="setup-title" className="text-base font-semibold">Get set up</h2>
            <p className="mt-0.5 text-sm text-ink-soft">Three steps to the first check of a merchant&apos;s money.</p>
          </div>
          <div className="flex flex-wrap items-center gap-4">
            {progress}
            <Link href={next.href} className={buttonClass("primary")}>{next.label}</Link>
          </div>
        </div>
        {list}
      </section>
    );
  }

  return (
    <section aria-labelledby="setup-title" className="mt-10 rounded-xl border border-line bg-surface">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-2">
        <h2 id="setup-title" className="text-sm font-semibold">Setup</h2>
        {progress}
        <p className="min-w-0 flex-1 text-sm sm:truncate">
          <span className="text-ink-soft">Next: </span>
          <Link href={next.href} className="font-medium underline-offset-4 hover:underline">{next.label}</Link>
        </p>
        <button
          type="button"
          aria-expanded={open}
          aria-controls="setup-steps"
          onClick={() => setOpen((v) => !v)}
          className="min-h-11 rounded-lg px-2 text-sm text-ink-soft hover:bg-ink/5 hover:text-ink"
        >
          {open ? "Hide steps" : "All steps"}
        </button>
      </div>
      {open && <div id="setup-steps" className="border-t border-line">{list}</div>}
    </section>
  );
}
