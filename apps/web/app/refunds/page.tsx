import type { Metadata } from "next";
import { COMPANY, LegalPage, Mail, type Part } from "../_components/legal";

export const metadata: Metadata = {
  title: "Refunds and cancellation — Okwan",
  description: "What happens when you cancel Okwan Pro, when a payment is refunded and when it is not.",
  alternates: { canonical: "/refunds" },
};

/* The sentences here are the policy. okwan_api/policy.py holds the same
   ones, for Stripe Checkout and for answering a disputed payment, and
   tests/test_refund_policy.py fails if the two differ. Change both, and
   the date below and VERSION there, together. */
const parts: Part[] = [
  {
    id: "what-you-pay-for",
    title: "What you pay for",
    body: (
      <>
        <p>
          Pro is 99 US dollars a month, billed monthly in advance by card through Stripe. Each payment covers one month
          of Pro, starting the day it is taken, and it renews each month until you cancel.
        </p>
        <p>The free plan costs nothing and needs no card.</p>
        <p>
          Before you pay, the plan page in the dashboard and Stripe&rsquo;s payment page both show the price, that it
          renews, how to cancel, and a link to this page.
        </p>
      </>
    ),
  },
  {
    id: "cancelling",
    title: "Cancelling",
    body: (
      <>
        <p>
          You can cancel at any time from <strong>Manage billing</strong> in the dashboard, under Settings, Plan &amp;
          usage.
        </p>
        <p>
          Cancelling takes effect at the end of the month you have paid for: Pro stays on until then, no further
          payment is taken, and the account then returns to the free plan. Nothing in your account is deleted when it
          does.
        </p>
      </>
    ),
  },
  {
    id: "refunds",
    title: "Refunds",
    body: (
      <>
        <p>
          A payment for Pro is not refunded, in whole or in part, once its month has started. Cancelling stops the next
          payment; it does not return the last one.
        </p>
        <ul>
          <li>
            If we took a payment by mistake, such as charging you twice for the same month or charging you after a
            cancellation had taken effect, tell us and we refund that payment in full.
          </li>
          <li>Where the law that applies to you gives you a right to a refund, you have it.</li>
          <li>A plan larger than Pro is refunded as its order form or agreement says.</li>
        </ul>
      </>
    ),
  },
  {
    id: "how-to-ask",
    title: "How to ask",
    body: (
      <>
        <p>
          Email us from your sign-in address with the date and amount of the payment. Write to <Mail />.
        </p>
        <p>A refund goes back through Stripe to the card that paid; how soon it shows is up to your bank.</p>
      </>
    ),
  },
  {
    id: "if-a-payment-looks-wrong",
    title: "If a payment looks wrong",
    body: (
      <p>
        Please write to us before asking your bank to reverse a payment. We can see what happened and put a mistake
        right directly, and a dispute through a bank can take weeks or months to decide.
      </p>
    ),
  },
  {
    id: "changes",
    title: "Changes",
    body: (
      <p>
        If this policy changes, the new version is posted here with its date. A payment is covered by the version that
        was shown when it was made.
      </p>
    ),
  },
  {
    id: "contact",
    title: "Contact",
    body: <p>{COMPANY}, Wyoming, United States. Email <Mail />.</p>,
  },
];

export default function Refunds() {
  return (
    <LegalPage
      title="Refunds and cancellation"
      updated="November 13, 2026"
      intro={
        <p>
          What happens when you cancel Okwan Pro, when a payment is refunded and when it is not. This is part of
          our <a className="font-medium text-ink underline underline-offset-4" href="/terms#plans">Terms of Service</a>;
          where an agreement you have signed with us says something different, the agreement applies.
        </p>
      }
      parts={parts}
    />
  );
}
