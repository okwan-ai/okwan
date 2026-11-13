"use client";

import { useState } from "react";
import { Button, buttonClass } from "../ui/button";

type Props = {
  enabled: boolean;
  plan: string;
  price: number;
  canManage: boolean;
  periodEnd: string | null;
  cancelling: boolean;
  pastDue: boolean;
  upgraded: boolean;
};

const CALL = "mailto:info@globaltechstartup.com?subject=Okwan%20%E2%80%94%20plan";
const REFUNDS = "https://okwan.ai/refunds";
const TERMS = "https://okwan.ai/terms";

/** Upgrade to Pro, or manage what is already paid for. Card details go to
 *  Stripe's own page and never reach Okwan. */
export function BillingCard({ enabled, plan, price, canManage, periodEnd, cancelling, pastDue, upgraded }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go(kind: "checkout" | "portal") {
    setBusy(true);
    setError(null);
    const res = await fetch(`/api/billing/${kind}`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.url) {
      setBusy(false);
      return setError(data.detail ?? "something went wrong; try again in a moment");
    }
    window.location.href = data.url;
  }

  const until = periodEnd ? new Date(periodEnd).toLocaleDateString("en-US", { dateStyle: "long", timeZone: "UTC" }) : null;
  const planName = plan === "pro" ? "Pro" : plan.charAt(0).toUpperCase() + plan.slice(1);

  return (
    <section aria-labelledby="billing-title" className="mb-6 rounded-xl border border-line bg-surface p-5 sm:p-6">
      {upgraded && plan === "free" && (
        <p role="status" className="mb-4 rounded-lg bg-ok-soft px-3 py-2 text-sm text-ok">
          Payment received. Pro switches on as soon as Stripe confirms it, usually within a minute; reload to see it.
        </p>
      )}
      {plan === "free" ? (
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-xl">
            <h2 id="billing-title" className="text-base font-semibold">Upgrade to Pro</h2>
            <p className="mt-1 text-sm text-ink-soft">
              100,000 requests a month instead of 5,000, for <span className="font-medium text-ink">${price} a month</span>.
              Cancel any time; Pro stays on to the end of the month you paid for.
            </p>
            {/* Ahead of the button (beside it on a wide screen, above it on a
                phone), so it is read before any card is asked for.
                okwan_api/policy.py says the same on Stripe's page. */}
            <p className="mt-2 text-sm text-ink-soft">
              It renews each month until you cancel, and a payment isn&apos;t refunded once its month has started.{" "}
              <a href={REFUNDS} target="_blank" rel="noopener" className="font-medium text-ink underline underline-offset-2">Refunds and cancellation</a>
              {" · "}
              <a href={TERMS} target="_blank" rel="noopener" className="font-medium text-ink underline underline-offset-2">Terms</a>
            </p>
          </div>
          {enabled ? (
            <Button variant="primary" disabled={busy} onClick={() => go("checkout")}>
              {busy ? "Opening Stripe…" : `Upgrade for $${price}/month`}
            </Button>
          ) : (
            <a href={CALL} className={buttonClass("secondary")}>Talk to us about Pro</a>
          )}
        </div>
      ) : (
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-xl">
            <h2 id="billing-title" className="text-base font-semibold">You&apos;re on {planName}</h2>
            <p className="mt-1 text-sm text-ink-soft">
              {pastDue ? "Your last payment didn't go through. Stripe will try again; update your card to keep Pro."
                : cancelling && until ? `Cancelled: Pro stays on until ${until}, then the account returns to Free.`
                  : until ? `Renews on ${until}.`
                    : canManage ? "Billed monthly by card through Stripe." : "Your plan was set up with Okwan directly. Email us to change it."}
            </p>
          </div>
          {canManage && enabled && (
            <Button variant="secondary" disabled={busy} onClick={() => go("portal")}>
              {busy ? "Opening Stripe…" : "Manage billing"}
            </Button>
          )}
        </div>
      )}
      {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
      <p className="mt-4 text-xs text-ink-soft">
        Card payments are handled by Stripe; Okwan never sees your card. Need more than 100,000 requests?{" "}
        <a href={CALL} className="underline underline-offset-2 hover:text-ink">Talk to us</a>.
      </p>
    </section>
  );
}
