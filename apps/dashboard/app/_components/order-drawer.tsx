"use client";

import Link from "next/link";
import { useRef } from "react";
import {
  ago, type AttentionRow, atStake, type FindingRow, OUTCOME_LABEL, OUTCOME_MARK, OUTCOME_TONE, railLabel, sameCurrency,
} from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import { mcpCall, restCall } from "@/lib/reproduce";
import { MoneyTrail } from "./money-trail";
import { Badge } from "./ui/badge";
import { BrandMark } from "./ui/brand-mark";
import { Button, buttonClass } from "./ui/button";
import { CodeBlock, CopyButton } from "./ui/copy-button";
import { SlideOver } from "./ui/dialog";

export type DrawerRow = FindingRow & { merchantId: string; merchantName: string; at?: number; partial?: boolean };

/**
 * One order's proof, where the operator already is: the verdict, what is at
 * stake, the money trail, each source's evidence, and how to reproduce it.
 * Built from rows the page holds (a stored run), so opening it reads
 * nothing and runs nothing. Previous and Next step through the list it was
 * opened from. A person makes any refund on the rail; Okwan only prepares
 * the facts.
 */
export function OrderDrawer({ rows, index, onIndex, onClose, apiBase }: {
  rows: (DrawerRow | AttentionRow)[];
  index: number | null;
  onIndex: (i: number) => void;
  onClose: () => void;
  apiBase: string;
}) {
  const r = index === null ? null : rows[index] ?? null;
  const stake = r ? atStake(r) : null;
  const prev = useRef<HTMLButtonElement>(null);
  const next = useRef<HTMLButtonElement>(null);
  // At either end the pressed arrow disables itself; hand focus to the
  // other one rather than drop it on <body>.
  function step(i: number) {
    onIndex(i);
    if (i === 0) requestAnimationFrame(() => next.current?.focus());
    else if (i === rows.length - 1) requestAnimationFrame(() => prev.current?.focus());
  }
  return (
    <SlideOver
      open={r !== null}
      onClose={onClose}
      title={r ? `Order ${r.order}` : ""}
      description={r ? (
        <>
          {r.merchantName}
          {r.at ? <> · <span suppressHydrationWarning>run {ago(r.at)}</span></> : null}
        </>
      ) : null}
    >
      {r && index !== null && (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Badge tone={OUTCOME_TONE[r.outcome]} symbol={OUTCOME_MARK[r.outcome]}>{OUTCOME_LABEL[r.outcome] ?? r.outcome}</Badge>
            <div className="flex items-center gap-1">
              <span role="status" className="mr-1 text-xs text-ink-soft tabular-nums">
                {index + 1} of {rows.length}<span className="sr-only">: order {r.order}, {OUTCOME_LABEL[r.outcome] ?? r.outcome}</span>
              </span>
              <Button ref={prev} variant="ghost" aria-label="Previous finding" disabled={index === 0} onClick={() => step(index - 1)}>←</Button>
              <Button ref={next} variant="ghost" aria-label="Next finding" disabled={index === rows.length - 1} onClick={() => step(index + 1)}>→</Button>
            </div>
          </div>

          {stake && (
            <div>
              <p className="text-xs text-ink-soft">At stake</p>
              <p className="text-3xl font-semibold tracking-tight tabular-nums">{formatMinor(stake.minor, r.currency)}</p>
              <p className="text-sm text-ink-soft">{stake.label}</p>
            </div>
          )}

          <section aria-labelledby="drawer-trail">
            <h3 id="drawer-trail" className="mb-2 text-sm font-semibold">Order against what was taken</h3>
            <MoneyTrail r={r} />
          </section>

          <section aria-labelledby="drawer-evidence">
            <h3 id="drawer-evidence" className="mb-2 text-sm font-semibold">Evidence by source</h3>
            <dl className="divide-y divide-line overflow-hidden rounded-xl border border-line">
              <Fact mark="shopify" label="Shopify (ledger)" value={`Order total ${formatMinor(r.total_minor, r.currency)}`} />
              {r.paid.map((p, i) => (
                <Fact key={`${p.rail}-${i}`} mark={p.rail} label={railLabel(p.rail)} value={`Matched · took ${formatMinor(p.minor, p.currency ?? r.currency)}`} />
              ))}
              {r.unverified.map((u) => (
                <Fact key={u} mark={u} label={railLabel(u)} value="Couldn't rule out a payment" muted />
              ))}
              {r.paid.length === 0 && r.unverified.length === 0 && (
                <Fact label="PayPal and Stripe" value="No matching payment" muted />
              )}
            </dl>
            {r.reason && (r.outcome === "unverifiable" || r.paid.length === 0 || r.total_minor === null || r.collected_minor === null || !sameCurrency(r)) && (
              <p className="mt-2 text-xs break-words text-ink-soft">Reason: {r.reason}</p>
            )}
            {r.partial && (
              <p className="mt-2 text-xs text-ink">
                <span aria-hidden className="mr-1 font-mono">?</span>This merchant&apos;s result was cut short, so other orders may
                have findings this list doesn&apos;t show.
              </p>
            )}
          </section>

          <section aria-labelledby="drawer-repro" className="space-y-2">
            <h3 id="drawer-repro" className="text-sm font-semibold">Reproduce this verdict</h3>
            <CodeBlock label="Hosted MCP" code={mcpCall(r)} />
            <CodeBlock label="REST · curl + jq" code={restCall(apiBase, r)} />
            <p className="text-xs text-ink-soft">With a key issued for {r.merchantName}. Each call is a fresh, metered check.</p>
          </section>

          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
            <Link
              href={`/merchants/${encodeURIComponent(r.merchantId)}?order=${encodeURIComponent(r.order)}`}
              className={buttonClass("secondary")}
            >
              Open on {r.merchantName}
            </Link>
            <CopyButton value={summary(r)} label="Copy summary" text="Copy summary" />
            <CopyButton value={prompt(r)} label="Copy agent prompt" text="Agent prompt" />
          </div>
          <p className="text-xs text-ink-soft">
            Okwan is read-only: it never refunds or writes to a rail. Make any refund on the rail itself.
          </p>
        </div>
      )}
    </SlideOver>
  );
}

function Fact({ label, value, mark, muted = false }: { label: string; value: string; mark?: string; muted?: boolean }) {
  return (
    <div className="grid grid-cols-[130px_1fr] gap-3 px-4 py-2.5 text-sm">
      <dt className="flex items-center gap-1.5 text-ink-soft">{mark && <BrandMark name={mark} label={label} size={16} />}{label}</dt>
      <dd className={`tabular-nums ${muted ? "text-ink-soft" : ""}`}>{value}</dd>
    </div>
  );
}

/** Plain text for a ticket or a message to the merchant. */
function summary(r: DrawerRow): string {
  const stake = atStake(r);
  const takes = r.paid.map((p) => `${railLabel(p.rail)} ${formatMinor(p.minor, p.currency ?? r.currency)}`).join(", ");
  return [
    `${r.merchantName} · order ${r.order}: ${OUTCOME_LABEL[r.outcome] ?? r.outcome}`,
    `Order total ${formatMinor(r.total_minor, r.currency)}${takes ? `; taken: ${takes}` : "; no matching payment"}.`,
    stake ? `${formatMinor(stake.minor, r.currency)} ${stake.label}.` : "",
    r.at ? `Run ${new Date(r.at).toISOString()}.` : "",
    "Source: Okwan reconciliation (rails), read-only.",
  ].filter(Boolean).join("\n");
}

/** A prompt that hands this one order to an agent on the hosted MCP. */
function prompt(r: DrawerRow): string {
  return [
    `Use the Okwan MCP server with ${r.merchantName}'s key. Call okwan_reconcile with name "rails" and status "${r.outcome}".`,
    `Find order ${r.order}. Report the order total, each rail that took payment and how much, and what is at stake.`,
    "Draft a short note to the merchant explaining it. Read only: do not attempt a refund or any write to a payment rail.",
  ].join("\n");
}
