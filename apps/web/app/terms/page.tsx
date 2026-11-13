import type { Metadata } from "next";
import { COMPANY, LegalPage, Mail, type Part } from "../_components/legal";

export const metadata: Metadata = {
  title: "Terms of Service — Okwan",
  description: "The agreement between you and Okwan for using the website, dashboard, API and hosted MCP server.",
  alternates: { canonical: "/terms" },
};

const parts: Part[] = [
  {
    id: "service",
    title: "The service",
    body: (
      <>
        <p>
          Okwan connects to the payment, commerce and data services you choose (&ldquo;connected services&rdquo;),
          reads your records, and reports where they agree and where they differ. You use it through the dashboard, a
          REST API, SQL, and an MCP server for AI agents (together, the &ldquo;Service&rdquo;).
        </p>
        <ul>
          <li>
            <strong>Read-only.</strong> Okwan never moves money, issues refunds, or changes records in your payment or
            commerce accounts. The only operations that send anything are messaging operations (for WhatsApp), and
            they run only when you call them.
          </li>
          <li>
            <strong>Information, not advice.</strong> Results are not accounting, tax, legal or financial advice.
            Check them before you act on them; decisions you make with them are yours.
          </li>
          <li>
            <strong>It changes.</strong> We add, change and sometimes remove features. We will give you notice before
            a change that significantly reduces what you rely on.
          </li>
        </ul>
      </>
    ),
  },
  {
    id: "account",
    title: "Your account",
    body: (
      <ul>
        <li>The Service is for businesses. You must be at least 18 and, if you sign up for a company, able to accept these Terms for it.</li>
        <li>Use a real email address you control. One account per address.</li>
        <li>Keep your password, sessions and API keys secure. You are responsible for what is done with your account and keys.</li>
        <li>If you think a key or your password has been exposed, revoke the key in the dashboard and tell us straight away.</li>
      </ul>
    ),
  },
  {
    id: "data",
    title: "Your data and connected services",
    body: (
      <ul>
        <li>
          <strong>Your data stays yours.</strong> You give us permission to access, process and store it only to
          provide, secure and support the Service, as our <a href="/privacy">Privacy Policy</a> describes.
        </li>
        <li>
          <strong>Your right to connect.</strong> You confirm that you are allowed to connect each account you
          connect and to let Okwan read it, and that you use those services within their own terms.
        </li>
        <li>
          <strong>Connected services are run by others.</strong> We are not responsible for their availability,
          their accuracy, changes they make, or what they charge you.
        </li>
        <li>
          <strong>Platforms.</strong> If you add merchants to your account, you are responsible for having their
          authorization to connect their accounts and for telling them that you use Okwan. Toward us, you act for
          them.
        </li>
      </ul>
    ),
  },
  {
    id: "processing",
    title: "How we handle data for you",
    body: (
      <>
        <p>For the records Okwan reads from connected services for you and your merchants, we:</p>
        <ul>
          <li>process them only to provide the Service, and only as you instruct;</li>
          <li>keep them confidential, and let only people who need to see them see them;</li>
          <li>protect them with the measures described in the Privacy Policy;</li>
          <li>use only the providers listed in the Privacy Policy, and tell you before we add one;</li>
          <li>help you answer requests from people about their information;</li>
          <li>tell you without undue delay once we know of a breach affecting them;</li>
          <li>delete them within 30 days after you delete your account.</li>
        </ul>
        <p>If your business needs a signed data processing agreement, email <Mail />.</p>
      </>
    ),
  },
  {
    id: "use",
    title: "Acceptable use",
    body: (
      <>
        <p>You agree not to:</p>
        <ul>
          <li>use the Service to break the law or anyone&rsquo;s rights;</li>
          <li>reach, or try to reach, accounts or data that are not yours;</li>
          <li>probe, scan or test the Service&rsquo;s security without our written permission. If you find a vulnerability, please report it to <Mail />; we welcome it;</li>
          <li>disrupt or overload the Service, or get around its limits, rate limits or security;</li>
          <li>send malware or unsolicited messages, including through messaging operations;</li>
          <li>resell the Service, except as part of your own product for your own merchants.</li>
        </ul>
        <p>
          Okwan&rsquo;s source code is published under the Apache-2.0 license, which governs your use of the code.
          These Terms govern the hosted Service.
        </p>
      </>
    ),
  },
  {
    id: "plans",
    title: "Plans and fees",
    body: (
      <ul>
        <li>
          The free plan includes a monthly number of requests, shown in your dashboard (5,000 today). Requests beyond
          your plan&rsquo;s limit are refused until the next month begins or you move to a larger plan.
        </li>
        <li>
          <strong>Pro</strong> is 99 US dollars a month for 100,000 requests a month, billed monthly in advance by card
          through Stripe. It starts when Stripe confirms your payment and renews each month until you cancel.
        </li>
        <li>
          You can cancel at any time from <strong>Manage billing</strong> in the dashboard. Pro stays on until the end
          of the month you have paid for, and the account then returns to the free plan. If a payment fails, Stripe
          retries it; if it still cannot be collected, the account returns to the free plan.
        </li>
        <li>
          Larger plans are agreed with us in writing, by an order form or by email, and billed as agreed. Fees are
          non-refundable except where the law or our agreement says otherwise, and taxes are extra where they apply.
        </li>
        <li>
          Our <a className="font-medium text-ink underline underline-offset-4" href="/refunds">Refunds and cancellation</a>{" "}
          policy says how cancelling and refunds work in practice, and is part of these Terms.
        </li>
        <li>We may change plans and prices with 30 days&rsquo; notice. A change does not affect a paid term already agreed.</li>
      </ul>
    ),
  },
  {
    id: "support",
    title: "Support and availability",
    body: (
      <ul>
        <li>
          Support is available from the assistant in the dashboard and by email at <Mail />. The assistant uses AI and
          can be wrong; it can only read your own account and cannot change anything. A person handles what it
          cannot settle.
        </li>
        <li>We work to keep the Service available and correct, but unless we have agreed otherwise in writing, we do not promise it will be uninterrupted or error-free.</li>
      </ul>
    ),
  },
  {
    id: "ownership",
    title: "Ownership and feedback",
    body: (
      <ul>
        <li>We own the Service, its name and its brand. Your data stays yours.</li>
        <li>If you send us ideas or feedback, we may use them without owing you anything.</li>
        <li>Names and logos of other companies belong to their owners. Showing them does not mean they endorse Okwan.</li>
      </ul>
    ),
  },
  {
    id: "confidentiality",
    title: "Confidentiality",
    body: <p>Each of us keeps the other&rsquo;s non-public information confidential and uses it only for this agreement, unless the law requires otherwise.</p>,
  },
  {
    id: "ending",
    title: "Suspension and ending",
    body: (
      <ul>
        <li>You can stop using the Service at any time and ask us to delete your account.</li>
        <li>
          We may suspend your access immediately if that is needed to protect the Service, other customers or a
          connected service, or if you seriously break these Terms. We will tell you why when we can, and restore
          access once the problem is resolved.
        </li>
        <li>We may end these Terms with 30 days&rsquo; notice, or at once for a serious breach.</li>
        <li>
          When they end, your access ends and we delete your data within 30 days, unless the law requires us to keep
          it. The sections on ownership, confidentiality, disclaimers, liability, indemnity and disputes continue to
          apply.
        </li>
      </ul>
    ),
  },
  {
    id: "disclaimers",
    title: "Disclaimers",
    body: (
      <p>
        The Service is provided &ldquo;as is&rdquo; and &ldquo;as available&rdquo;. As far as the law allows, we
        disclaim implied warranties, including merchantability, fitness for a particular purpose and
        non-infringement, and we do not warrant that results are complete or free of errors.
      </p>
    ),
  },
  {
    id: "liability",
    title: "Limitation of liability",
    body: (
      <>
        <p>As far as the law allows:</p>
        <ul>
          <li>neither of us is liable to the other for indirect, incidental, special, consequential or punitive damages, or for lost profits, revenue or data;</li>
          <li>our total liability for all claims is limited to the greater of the fees you paid us in the 12 months before the claim and 100 US dollars.</li>
        </ul>
        <p>These limits do not apply where the law does not allow them to, such as for fraud.</p>
      </>
    ),
  },
  {
    id: "indemnity",
    title: "Indemnity",
    body: <p>You will defend and indemnify us against claims by third parties arising from your data, the accounts you connect, or your breach of these Terms or the law.</p>,
  },
  {
    id: "disputes",
    title: "Governing law and disputes",
    body: (
      <ul>
        <li>These Terms are governed by the laws of the State of Wyoming, United States, without regard to its conflict-of-laws rules.</li>
        <li>Before starting a claim, contact us at <Mail /> so we can try to resolve it together within 30 days.</li>
        <li>
          The state and federal courts located in Wyoming have exclusive jurisdiction, but either of us may seek urgent
          relief to protect its rights in any court.
        </li>
        <li>If the law where you are gives you rights that these Terms cannot take away, those rights still apply.</li>
      </ul>
    ),
  },
  {
    id: "changes",
    title: "Changes to these Terms",
    body: (
      <p>
        We may update these Terms. We post the new version here with a new date and, for a significant change, email
        you 30 days before it takes effect, unless the law or a security need requires it sooner. Using the Service
        after a change takes effect means you accept it.
      </p>
    ),
  },
  {
    id: "general",
    title: "General",
    body: (
      <ul>
        <li>These Terms, the Privacy Policy and any order or agreement we sign with you are the whole agreement between us. A signed agreement takes precedence where it differs.</li>
        <li>You may not transfer these Terms without our consent. We may transfer them as part of a merger, acquisition or sale of the business.</li>
        <li>If a part of these Terms cannot be enforced, the rest still applies. Not enforcing a term is not a waiver of it.</li>
        <li>Neither of us is responsible for delays caused by events beyond reasonable control.</li>
        <li>We send notices to your sign-in address; send yours to <Mail />. No one else has rights under these Terms.</li>
      </ul>
    ),
  },
  {
    id: "contact",
    title: "Contact",
    body: <p>{COMPANY}, Wyoming, United States. Email <Mail />.</p>,
  },
];

export default function Terms() {
  return (
    <LegalPage
      title="Terms of Service"
      intro={
        <p>
          These Terms are the agreement between you and {COMPANY}, a Wyoming limited liability company that operates
          Okwan, for using the website at okwan.ai, the dashboard at app.okwan.ai, and the API and hosted MCP server at
          api.okwan.ai. By creating an account or using the Service, you accept them.
        </p>
      }
      parts={parts}
    />
  );
}
