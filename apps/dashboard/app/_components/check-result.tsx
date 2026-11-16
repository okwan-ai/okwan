"use client";

import type { CheckLine, Tone } from "@/lib/file-checks";
import { Badge } from "./ui/badge";
import { Card } from "./ui/card";

const HEADLINE: Record<Tone, string> = { danger: "text-danger", warn: "text-ink", ok: "text-ok", neutral: "text-ink-soft" };

/** A stored run as the Files tab draws it. Every string is written on the
 *  server (lib/file-checks.ts); nothing is worked out here. */
export type Result = {
  headline: string;
  tone: Tone;
  counts: string;
  /** "Checked 14 Nov 2026, 12:53 UTC", and what has changed since. */
  checked: string;
  /** Said in the colour of a fault: the run stopped before it had read everything. */
  cut: string;
  /** Said in bold: lines on file the run did not reach. */
  older: string[];
  read: string;
  attention: CheckLine[];
  paid: CheckLine[];
  /** "5,000 deposits matched to their batches", from the run's summary. */
  paidCount: string;
  /** " · the first 100 listed", when the page draws fewer than that. */
  paidShown: string;
  footnotes: string[];
};

/**
 * A reference as the merchant's file has it: its own element, in the type
 * of data and with a rule around it, so it cannot be read as part of the
 * sentence beside it; and its own direction, so nothing in it can turn
 * that sentence around. `value` has been through `asWritten`.
 */
function Ref({ value }: { value: string }) {
  return (
    <bdi dir="ltr" title="As written in the file" data-file-ref
      className="rounded border border-line bg-canvas px-1.5 py-0.5 font-mono text-[13px] font-medium text-ink break-all">
      {value}
    </bdi>
  );
}

function Line({ line }: { line: CheckLine }) {
  return (
    <li className="px-5 py-3.5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
        <Badge tone={line.tone} symbol={line.symbol}>{line.label}</Badge>
        <p className="min-w-0 flex-1 basis-48 font-semibold break-words">
          {line.title}
          {line.refs.map((ref, n) => <span key={n}>{" "}<Ref value={ref} /></span>)}
        </p>
        <p className="font-semibold tabular-nums">{line.amount}</p>
      </div>
      <p className="mt-1.5 text-sm text-ink-soft">{line.detail}</p>
      {line.parts.length > 0 && (
        <ul className="mt-2 space-y-1.5 text-sm text-ink-soft">
          {line.parts.map((p, n) => (
            <li key={n} className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5">
              <span className="min-w-24"><Ref value={p.ref} /></span>
              {p.when && <span className="whitespace-nowrap">closed {p.when}</span>}
              <span className="whitespace-nowrap tabular-nums">{p.amount}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * The result of a file check, drawn from data.
 *
 * Drawn in the browser's own pass for a reason an attacker's pass found:
 * as server-rendered markup, a long result reached the page in pieces, the
 * page began to take it over before the last piece had arrived, and React
 * then threw the server's page away and drew it again ("hydration failed",
 * one load in four of a 1,000-line result). Given the lines as data, as
 * the findings list is, the page waits for all of them and takes over once.
 */
export function CheckResult({ result: r }: { result: Result }) {
  return (
    <Card flush>
      <div className="border-b border-line px-5 py-4">
        <p className={`text-lg font-bold ${HEADLINE[r.tone]}`}>{r.headline}</p>
        {r.counts && <p className="mt-1 text-sm text-ink-soft">{r.counts}</p>}
        <p className="mt-1 text-xs text-ink-soft">{r.checked}</p>
        {r.cut && <p className="mt-2 text-sm font-semibold text-danger">{r.cut}</p>}
        {r.older.map((line) => <p key={line} className="mt-2 text-sm font-semibold">{line}</p>)}
        {r.read && <p className="mt-1 text-xs text-ink-soft">{r.read}</p>}
      </div>
      {r.attention.length > 0 && <ul className="divide-y divide-line">{r.attention.map((l) => <Line key={l.key} line={l} />)}</ul>}
      {r.paid.length > 0 && (
        <details className={r.attention.length > 0 ? "border-t border-line" : ""} open={r.attention.length === 0}>
          <summary className="cursor-pointer px-5 py-3.5 text-sm font-semibold">
            {r.paidCount}
            {r.paidShown && <span className="font-normal text-ink-soft">{r.paidShown}</span>}
          </summary>
          <ul className="divide-y divide-line border-t border-line">{r.paid.map((l) => <Line key={l.key} line={l} />)}</ul>
        </details>
      )}
      {r.footnotes.length > 0 && (
        <div className="space-y-1 border-t border-line px-5 py-3 text-xs text-ink-soft">
          {r.footnotes.map((note) => <p key={note}>{note}</p>)}
        </div>
      )}
    </Card>
  );
}
