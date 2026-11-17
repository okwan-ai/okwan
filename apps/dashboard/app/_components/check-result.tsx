"use client";

import type { CheckLine, Result, Tone } from "@/lib/file-checks";
import { Badge } from "./ui/badge";
import { Card } from "./ui/card";

const HEADLINE: Record<Tone, string> = { danger: "text-danger", warn: "text-ink", ok: "text-ok", neutral: "text-ink-soft" };

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
 * A client component for a reason an attacker's pass found. A page reaches
 * the browser in pieces. As server markup, a long result was many of them,
 * and the browser took the card's element over before its inside had
 * arrived, took it again when it had, and React threw the server's page
 * away and drew it again. Given its words as data, the card is one piece.
 * The tab around it (files-panel.tsx) is boundaries for the same reason.
 */
export function CheckResult({ result: r }: { result: Result }) {
  return (
    <Card flush>
      <div className="border-b border-line px-5 py-4">
        <p className={`text-lg font-bold ${HEADLINE[r.tone]}`}>{r.headline}</p>
        {r.counts && <p className="mt-1 text-sm text-ink-soft">{r.counts}</p>}
        <p className="mt-1 text-xs text-ink-soft">{r.checked}</p>
        {r.cut && <p className="mt-2 text-sm font-semibold text-danger">{r.cut}</p>}
        {r.notes.map((note) => <p key={note} className="mt-2 text-sm font-semibold">{note}</p>)}
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
