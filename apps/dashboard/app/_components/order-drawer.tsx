"use client";

import Link from "next/link";
import { atStake, type FindingRow, OUTCOME_LABEL, OUTCOME_MARK, OUTCOME_TONE, railLabel, sameCurrency } from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import { MoneyTrail } from "./money-trail";
import { Badge } from "./ui/badge";
import { buttonClass } from "./ui/button";
import { CopyButton } from "./ui/copy-button";
import { SlideOver } from "./ui/dialog";

export type DrawerRow = FindingRow & { merchantId: string; merchantName: string };

/**
 * One order's proof, where the operator already is: the verdict, what is at
 * stake, the money trail, and each rail's evidence. Built from the row the
 * page holds, so opening it reads nothing and runs nothing. A person makes
 * any refund on the rail; Okwan only prepares the facts.
 */
export function OrderDrawer({ row, onClose }: { row: DrawerRow | null; onClose: () => void }) {
  const r = row;
  const stake = r ? atStake(r) : null;
  return (
    <SlideOver
      open={r !== null}
      onClose={onClose}
      title={r ? `Order ${r.order}` : ""}
      description={r?.merchantName}
    >
      {r && (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Badge tone={OUTCOME_TONE[r.outcome]} symbol={OUTCOME_MARK[r.outcome]}>{OUTCOME_LABEL[r.outcome] ?? r.outcome}</Badge>
            <code className="font-mono text-xs text-ink-soft">{r.outcome}</code>
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
              <Fact label="Shopify (ledger)" value={`Order total ${formatMinor(r.total_minor, r.currency)}`} />
              {r.paid.map((p, i) => (
                <Fact key={`${p.rail}-${i}`} label={railLabel(p.rail)} value={`Matched · took ${formatMinor(p.minor, p.currency ?? r.currency)}`} />
              ))}
              {r.unverified.map((u) => (
                <Fact key={u} label={railLabel(u)} value="Couldn't rule out a payment" muted />
              ))}
              {r.paid.length === 0 && r.unverified.length === 0 && (
                <Fact label="PayPal and Stripe" value="No matching payment" muted />
              )}
            </dl>
            {r.reason && (r.outcome === "unverifiable" || r.paid.length === 0 || !sameCurrency(r)) && (
              <p className="mt-2 text-xs break-words text-ink-soft">Reason: {r.reason}</p>
            )}
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

function Fact({ label, value, muted = false }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="grid grid-cols-[130px_1fr] gap-3 px-4 py-2.5 text-sm">
      <dt className="text-ink-soft">{label}</dt>
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
