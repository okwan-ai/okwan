import type { Metadata } from "next";
import { COMPANY, Facts, LegalPage, Mail, type Part } from "../_components/legal";

export const metadata: Metadata = {
  title: "Privacy Policy — Okwan",
  description: "What Okwan collects, why, who it goes to, how long it is kept, and your choices.",
  alternates: { canonical: "/privacy" },
};

/* Every statement here is about what the system does today. When the
   system changes, this page changes with it: docs/audit/data-inventory.md
   is the engineering source for what is stored and for how long. */

const parts: Part[] = [
  {
    id: "who",
    title: "Who we are and our role",
    body: (
      <>
        <p>
          Okwan is operated by {COMPANY}, a Wyoming limited liability company in the United States
          (&ldquo;Okwan&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;). This policy covers the website at okwan.ai,
          the dashboard at app.okwan.ai, and the API and hosted MCP server at api.okwan.ai.
        </p>
        <ul>
          <li>
            <strong>Your account.</strong> For the information that runs your account (your sign-in address,
            usage, and support conversations) we decide how it is used. Under laws such as the GDPR, we are its
            controller.
          </li>
          <li>
            <strong>Records from the services you connect.</strong> When Okwan reads orders, payments and payouts
            from a service you connect, it does so for you. We process those records only to provide Okwan to you,
            as you instruct. Under laws such as the GDPR, we are your processor. If you are a platform that connects
            your merchants&rsquo; accounts, the merchants&rsquo; records are processed for you in the same way, and
            our <a href="/terms">Terms</a> say what you are responsible for.
          </li>
        </ul>
      </>
    ),
  },
  {
    id: "collect",
    title: "What we collect",
    body: (
      <>
        <ul>
          <li>
            <strong>Account details.</strong> Your sign-in email address and the names you give your account and
            merchants. Your password is never stored: we keep only a salted scrypt hash of it, from which the
            password cannot be read back.
          </li>
          <li>
            <strong>Credentials you save.</strong> The API keys, tokens and similar values you save to connect a
            service (for example Stripe, PayPal, Shopify, Square, Clover, Avalara, Paystack, WhatsApp or a Postgres
            database). Each is
            encrypted with AES-256-GCM under its own key and bound to your account. It is decrypted only while a
            request that reads your records runs, and is never shown again: not in the dashboard, and not in our
            staff tools, which show which fields are saved but never their values.
          </li>
          <li>
            <strong>API keys we issue you.</strong> We keep only a hash of each key and a short prefix that helps
            you tell keys apart. The full key is shown to you once.
          </li>
          <li>
            <strong>Records from connected services.</strong> Okwan reads them when you run a check or call the API
            or MCP server, and holds them in memory only for that request. What we store is the result of a
            reconciliation: a summary, and for each order its reference, currency, totals, the amount each service
            reported, and the outcome and reason. A short error message is kept if a check failed. We keep the
            newest 50 results of each check. We do not store the raw records, card numbers, or your customers&rsquo;
            names or email addresses from those services, and we do not store SQL queries or their results.
          </li>
          <li>
            <strong>Files you import.</strong> Settlement reports, transaction exports, delivery platform reports,
            sales tax return summaries and bank statements you upload (CSV, Excel or OFX, up to 8 MB). The file is read in memory while it
            uploads and is never stored. From each row we keep only the fields you map to it: references, dates,
            amounts, a merchant or store ID, a state or tax jurisdiction, an order&rsquo;s status, the statement text, and at most the last four
            digits of a card or account number. With them we keep
            the file&rsquo;s name, the row each came from, the numbers of any rows that could not be read, and a
            fingerprint of the file (its SHA-256 hash) so the same file is not imported twice. Statement text is
            kept as your bank wrote it, so it can name the person or business that paid you. A file that holds a
            full card number anywhere is refused before anything from it is stored; the refusal names the row and
            column, never the number. Previewing a file stores nothing. A column mapping you save keeps only
            column names and how to read them (currency, date order, decimal mark), never a value from a file.
          </li>
          <li>
            <strong>Usage.</strong> Hourly counts of requests for each account and surface (API, MCP, dashboard),
            used to apply your plan&rsquo;s limits.
          </li>
          <li>
            <strong>Support.</strong> What you write to Okwan&rsquo;s support assistant, its answers, and the
            read-only account information it looked up to answer (your plan and usage, which connections are saved,
            connection test results, recent check results and errors, and API key prefixes). Anything that looks
            like a secret (a key, token, password or connection string) is removed automatically before it is
            stored or sent anywhere. If your request is handed to a person, we also keep a ticket with a summary of
            the issue and the replies we send you.
          </li>
          <li>
            <strong>Access records.</strong> Each time your data is read, through the API, the MCP server or the
            dashboard, we record when, which API key (by its short prefix) or that it was a dashboard session, the IP
            address and network it came from, and what was read: the address requested, or the check that ran. Never
            the key itself, the query or its results. We use these records to show you who read your data, and to
            email you when a key is used from a network it has not used before or reads far more than usual.
          </li>
          <li>
            <strong>Technical information.</strong> Your IP address is also used, in memory only, to limit how often
            sign-in and similar requests can be tried. Our hosting providers keep standard request logs: the time,
            IP address, the address requested and the response. Okwan&rsquo;s own logs never contain credentials,
            tokens, passwords, links from our emails, or query text.
          </li>
          <li>
            <strong>Payments.</strong> If you pay for Pro, you enter your card on Stripe&rsquo;s own page; your card
            details go to Stripe and never reach Okwan. We keep the Stripe customer and subscription ids for your
            account and the subscription&rsquo;s state (active, cancelled, the renewal date), so your plan follows
            your payments.
          </li>
          <li>
            <strong>Email.</strong> The messages we send you (a verification link when you sign up, alerts about
            how your API keys are used, and replies to support requests) and your replies to them.
          </li>
        </ul>
      </>
    ),
  },
  {
    id: "cookies",
    title: "Cookies and tracking",
    body: (
      <ul>
        <li>
          <strong>Dashboard.</strong> One cookie, <code>okwan_session</code>, keeps you signed in. It ends after 30
          minutes without activity and 12 hours after you sign in, whichever comes first. It is necessary for the
          dashboard to work, cannot be read by scripts on the page, and is removed when you sign out. Your browser also remembers which setup steps you have ticked; that stays on your device.
        </li>
        <li><strong>Website.</strong> okwan.ai sets no cookies.</li>
        <li>
          <strong>No trackers.</strong> Neither uses analytics, advertising or tracking cookies, or third-party
          tracking scripts. Both load their fonts from Google Fonts, so Google receives your IP address and browser
          details when a page loads; Google&rsquo;s privacy policy applies to that.
        </li>
        <li><strong>Email.</strong> Open and click tracking is turned off for the email we send.</li>
      </ul>
    ),
  },
  {
    id: "use",
    title: "How we use it",
    body: (
      <>
        <ul>
          <li>To provide Okwan: signing you in, connecting your services, running checks, answering API and MCP requests, and applying plan limits.</li>
          <li>To keep it secure: protecting accounts, preventing and investigating abuse and incidents.</li>
          <li>To support you: answering questions and fixing problems.</li>
          <li>To contact you about your account: verification, security and service notices, and replies. We do not send marketing email unless you ask for it.</li>
          <li>To meet legal obligations and enforce our Terms.</li>
        </ul>
        <p>
          We do not sell or rent personal information, we do not share it for advertising, and we do not use your
          data or the records of your connected services to train AI models.
        </p>
        <p>
          Where the GDPR or UK GDPR applies, our legal bases are: performing our contract with you (providing
          Okwan), our legitimate interests (security, support and keeping the service reliable, balanced against
          your rights), legal obligations, and your consent where we ask for it.
        </p>
      </>
    ),
  },
  {
    id: "assistant",
    title: "The support assistant",
    body: (
      <ul>
        <li>
          The first answer to a support question comes from an AI assistant, Claude, provided by Anthropic. It can
          look only at your own account, only to read it, and it cannot change anything; the one thing it can do is
          hand your request to a person.
        </li>
        <li>Your messages and the account information it looks up are sent to Anthropic to produce the answer, after anything that looks like a secret has been removed.</li>
        <li>AI answers can be wrong, so check important steps. A person at Okwan reviews every request the assistant hands over and writes every reply to it.</li>
        <li>Please do not paste passwords or keys into the chat. If you do, they are removed automatically.</li>
      </ul>
    ),
  },
  {
    id: "share",
    title: "Who we share it with",
    body: (
      <>
        <p>These companies process information for us to run Okwan, each only for the purpose shown:</p>
        <Facts
          head={["Provider", "What for", "Where"]}
          rows={[
            ["Render", "Runs the API, the MCP server and the dashboard", "United States (Oregon)"],
            ["Neon", "The database where everything above is stored", "United States (Ohio)"],
            ["Vercel", "Runs the website okwan.ai and its DNS", "Global network"],
            ["Resend", "Sends our email: your address and the message", "United States (Virginia)"],
            ["Anthropic", "The support assistant: support conversations, secrets removed", "United States"],
            ["Stripe", "Takes payment for Pro: your card, name, email and billing address, entered on Stripe's page", "United States"],
            ["Google Fonts", "Fonts on the website and dashboard: IP address and browser details", "Global network"],
          ]}
        />
        <p>We also share information:</p>
        <ul>
          <li>with the services you connect: we send your saved credentials to each one, as it requires, to read your records, because you asked us to;</li>
          <li>when the law requires it, for example a valid court order. We check each request and tell you before we comply, unless the law forbids it;</li>
          <li>if Okwan is sold or merged, with the new owner, who must keep to this policy. We will tell you first.</li>
        </ul>
        <p>
          <strong>Our own staff.</strong> Account information is visible to Okwan staff only through an internal
          console restricted to named people, who must enter a code from an authenticator app. Every action in it is
          recorded in a log that cannot be changed or deleted. It never shows your credentials or keys.
        </p>
      </>
    ),
  },
  {
    id: "transfers",
    title: "International transfers",
    body: (
      <p>
        Okwan is based in the United States and stores your information there. If you use it from elsewhere, your
        information is transferred to the United States. Where the law requires a transfer mechanism, we rely on the
        safeguards our providers offer, such as the European Commission&rsquo;s standard contractual clauses.
      </p>
    ),
  },
  {
    id: "retention",
    title: "How long we keep it",
    body: (
      <>
        <Facts
          head={["Information", "Kept"]}
          rows={[
            ["Account, merchants, saved credentials and API keys", "Until you remove them or delete your account"],
            ["A signup that is never confirmed", "The link expires after 24 hours; the request is removed when you ask, or with your account"],
            ["Sign-in sessions", "30 minutes without activity, 12 hours at most, or until you sign out"],
            ["Your Stripe ids and subscription state", "As long as your account exists. Stripe keeps its own payment records as the law requires"],
            ["Reconciliation results", "The newest 50 of each check, until your account is deleted"],
            ["Files you import: their mapped rows and the file's record", "13 months from the upload, then deleted within the hour; at once if you delete the file"],
            ["Column mappings you save", "Until you delete them or your account"],
            ["Usage counts", "As long as your account exists"],
            ["Access records: when, the API key's prefix, the IP address and network, what was read", "90 days, then deleted within the hour; at once with your account"],
            ["Alerts about how your keys were used", "400 days, or until your account is deleted"],
            ["Support conversations", "30 days after the last message"],
            ["Support tickets and our replies", "As long as your account exists"],
            ["The log of staff actions in our console", "Permanently, as a security record. It names which account or ticket was opened, not what it contains"],
            ["Hosting providers' request logs", "For their own retention periods, typically days to weeks"],
            ["Database backups", "Deleted information leaves backups as they expire, on our database provider's schedule"],
          ]}
        />
      </>
    ),
  },
  {
    id: "security",
    title: "How we protect it",
    body: (
      <>
        <ul>
          <li>Encryption in transit (TLS) everywhere, and saved credentials encrypted individually at rest.</li>
          <li>Passwords, session tokens and API keys stored only as hashes.</li>
          <li>Each account&rsquo;s data kept apart in the database by two independent controls, each tested on its own.</li>
          <li>Read-only access to your payment and commerce services: Okwan never moves money or changes your records there.</li>
          <li>Files you import are read in memory and never stored; a file that holds a full card number is refused before anything from it is kept.</li>
          <li>Staff access only through the protected, logged console described above.</li>
        </ul>
        <p>
          No system is perfectly secure. If a breach affects your information, we will tell you without undue delay,
          with what happened and what we are doing about it.
        </p>
      </>
    ),
  },
  {
    id: "rights",
    title: "Your choices and rights",
    body: (
      <>
        <ul>
          <li>You can remove saved connections and revoke API keys yourself in the dashboard at any time.</li>
          <li>
            You can delete an imported file or a saved column mapping yourself, on the merchant&rsquo;s Files tab or
            through the API. It is deleted at once.
          </li>
          <li>
            You can ask to see, correct, export or delete your information, or to object to or restrict how we use
            it. Email <Mail /> from your sign-in address. We will confirm the request is yours and answer within
            30 days.
          </li>
          <li>
            <strong>Delete your account.</strong> We delete the account, its merchants, its login, saved credentials,
            API keys, check results, imported files and saved mappings, usage, support conversations, tickets and
            replies, within 30 days of your request. The log of staff actions keeps only the entry recording that the deletion was made.
          </li>
          <li>
            Depending on where you live (for example the EU, the UK, California, Ghana, Nigeria, Kenya or South
            Africa), the law may give you further rights, and you may complain to your data protection authority. We
            honour those rights wherever they apply.
          </li>
          <li>
            California residents: we do not sell or share personal information as the California Consumer Privacy Act
            defines those terms, and we do not use sensitive personal information to infer anything about you.
          </li>
        </ul>
      </>
    ),
  },
  {
    id: "children",
    title: "Children",
    body: <p>Okwan is a service for businesses and is not for anyone under 18. We do not knowingly collect information about children.</p>,
  },
  {
    id: "changes",
    title: "Changes to this policy",
    body: (
      <p>
        When we change this policy we post the new version here with a new date. If a change is significant, we
        email account holders before it takes effect.
      </p>
    ),
  },
  {
    id: "contact",
    title: "Contact",
    body: (
      <p>
        {COMPANY}, Wyoming, United States. Email <Mail /> for any question about privacy, any request about your
        information, or to report a security problem.
      </p>
    ),
  },
];

export default function Privacy() {
  return (
    <LegalPage
      title="Privacy Policy"
      updated="November 9, 2026"
      intro={
        <>
          <p>What Okwan collects, why, who it goes to, how long we keep it, and the choices you have. In short:</p>
          <ul className="mt-4">
            <li>Okwan only reads the services you connect. It never moves money or changes your records in them.</li>
            <li>Credentials you save are encrypted and used only to read your own records. Our staff tools never show them.</li>
            <li>Files you import are read in memory. Only the columns you map are kept, for 13 months, and a file that holds a full card number is refused.</li>
            <li>We do not sell your information, show ads, or use tracking cookies.</li>
            <li>An AI assistant answers support questions first, with secrets removed, and a person handles what it cannot.</li>
            <li>Ask us to delete your account and we delete it, with everything in it, within 30 days.</li>
          </ul>
        </>
      }
      parts={parts}
    />
  );
}
