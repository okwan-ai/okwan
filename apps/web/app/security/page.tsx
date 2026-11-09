import type { Metadata } from "next";
import { LegalPage, Mail, type Part } from "../_components/legal";

/** Where the master key is and who could use it, today or once the KMS step lands. */
function KeyPlan({ label, rows }: { label: string; rows: [string, string][] }) {
  return (
    <div className="rounded-xl border border-line bg-white p-5">
      <p className="font-mono text-[12px] uppercase tracking-[0.14em] text-ink-muted">{label}</p>
      <dl className="mt-3 space-y-3 text-[15px]">
        {rows.map(([term, detail]) => (
          <div key={term}>
            <dt className="font-medium text-ink">{term}</dt>
            <dd className="mt-0.5">{detail}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export const metadata: Metadata = {
  title: "Security — Okwan",
  description: "How Okwan reads your systems without writing to them, encrypts the credentials you save, keeps accounts apart, and what is not in place yet.",
  alternates: { canonical: "/security" },
};

/* Every statement here is about what the system does today, and the
   "not in place yet" section says what it does not. The engineering
   sources: packages/vault/okwan_vault/keys.py and crypto.py (encryption),
   packages/query/okwan_query/guard.py and session.py (SQL), schema.sql
   (row-level security and the staff log), docs/audit/ (known gaps).
   When one of them changes, this page changes with it. */

const parts: Part[] = [
  {
    id: "read-only",
    title: "Read-only to your payment, commerce and bank systems",
    body: (
      <>
        <p>
          Okwan never writes to your payment, commerce or bank systems. The only thing it sends is alert messages
          you set up, through WhatsApp.
        </p>
        <p>
          This is not a setting. Every operation a connector offers is declared once, and a reconciliation built on
          anything but a read is refused before it can run, so Okwan cannot move money, issue a refund or change a
          record in a system you connect. A WhatsApp message goes out only when your own request to the API sends
          one; those two send operations are the only ones in Okwan that write, and the API catalog marks them as
          writes.
        </p>
        <p>
          Where a system lets you, save a key that can only read, such as a Stripe restricted key with read
          permissions. Okwan needs nothing more.
        </p>
      </>
    ),
  },
  {
    id: "records",
    title: "What happens to your records",
    body: (
      <ul>
        <li>
          Okwan reads records from your systems when you run a check or call the API or MCP server, and holds them
          in memory for that request only.
        </li>
        <li>
          What it stores is the result: for each order, its reference, amounts, the outcome and how it was reached.
          It does not store the raw records, card numbers, or your customers&rsquo; names or email addresses. It
          keeps the newest 50 results of each check.
        </li>
        <li>
          Files you import are the one place records are kept, because you asked: the file is read in memory and
          never stored, and only the columns you map are kept, for 13 months or until you delete the file. A file
          that holds a full card number anywhere is refused before anything from it is kept.
        </li>
        <li>
          SQL you send runs in a sandbox held in memory: one read statement at a time, with no access to files,
          the network or extensions, and settings that cannot be changed from inside a query. Queries and their
          results are not stored.
        </li>
      </ul>
    ),
  },
  {
    id: "keys",
    title: "How saved credentials are encrypted",
    body: (
      <>
        <p>Each credential you save (an API key, token or connection string) is sealed in two layers.</p>
        <ul>
          <li>
            <strong>Its own key.</strong> It is encrypted with AES-256-GCM under a random 256-bit key made for that
            credential alone. The encryption is bound to your account, the system and the field, so a sealed value
            moved to another account or field cannot be opened.
          </li>
          <li>
            <strong>One master key.</strong> That per-credential key is itself encrypted by a single master key. The
            database holds only the two encrypted values, and neither opens without the master key.
          </li>
        </ul>
        <div className="grid gap-4 sm:grid-cols-2">
          <KeyPlan
            label="Today"
            rows={[
              ["Where the master key lives", "In the encrypted environment settings of our host, Render, given only to the API service."],
              ["How a credential is opened", "The API service holds the master key in memory and unwraps the credential’s key itself."],
              ["Who could open every credential", "Anyone who could read the API service’s environment on Render."],
            ]}
          />
          <KeyPlan
            label="Planned"
            rows={[
              ["Where the master key lives", "In a cloud key management service (KMS), which never hands it out."],
              ["How a credential is opened", "The API service asks the KMS to unwrap each key, and each request is logged."],
              ["Who could open every credential", "Only a caller the KMS allows, with a record of each use."],
            ]}
          />
        </div>
        <p>
          The master key is never in our code, our repository or our database. The KMS step is written but not in
          use yet; this page will say so when it is.
        </p>
        <p>
          A credential is decrypted only while a request that needs it runs, and only to send it to the system it
          belongs to. It is never shown again: not in the dashboard, not by the API, and not in our staff
          tools, which show which fields are saved but never their values.
        </p>
      </>
    ),
  },
  {
    id: "accounts",
    title: "Accounts, sessions and API keys",
    body: (
      <ul>
        <li>Your password is stored only as a salted scrypt hash, from which it cannot be read back.</li>
        <li>
          Sign-in uses one cookie that scripts on the page cannot read and that is sent only over HTTPS. Okwan keeps
          only a hash of it. It ends after 30 minutes without activity and 12 hours after you sign in, whichever
          comes first.
        </li>
        <li>
          API keys are stored as a SHA-256 hash and a short prefix that helps you tell them apart. The full key is
          shown to you once; a lost key is replaced, never read back. You can revoke a key at any time.
        </li>
        <li>Sign-in, sign-up and similar requests are limited per address and per IP address, so passwords cannot be guessed at speed.</li>
      </ul>
    ),
  },
  {
    id: "access",
    title: "A record of every read, and alerts",
    body: (
      <ul>
        <li>
          Every time your data is read, through the API, the MCP server or the dashboard, Okwan records when, which
          API key (by its prefix) or a dashboard session, the IP address and network it came from, and what was
          read. Never the key itself, the query or its results. Records are kept 90 days, and only your account can
          read them, through the API today.
        </li>
        <li>
          If an API key reads your data from a network it has not used in the last 30 days, we email your account
          at once with the key, the address and what was read, so you can revoke the key if it was not you. If a key
          reads far more in an hour than in its busiest hour of the week before, we email you within the hour.
        </li>
        <li>
          A key&rsquo;s first day sets where it normally runs from, so a new deployment does not set off an alert
          with its own first requests.
        </li>
      </ul>
    ),
  },
  {
    id: "isolation",
    title: "Keeping accounts apart",
    body: (
      <ul>
        <li>
          Every request is filtered to your account, and the database enforces the same boundary on its own with
          Postgres row-level security: a query that lost its filter would still see only one account&rsquo;s rows.
        </li>
        <li>
          Each of the two walls is tested by itself. An automated proof removes one at a time and confirms the
          other holds and the break is caught.
        </li>
        <li>An account that is not yours answers &ldquo;not found&rdquo;, never &ldquo;forbidden&rdquo;, so the API cannot reveal who else is a customer.</li>
      </ul>
    ),
  },
  {
    id: "staff",
    title: "Staff access",
    body: (
      <ul>
        <li>Okwan staff see account information only through an internal console open to named people, each of whom must enter a code from an authenticator app. A code cannot be used twice.</li>
        <li>Every action in the console is written to a log the database will not let anyone change or delete.</li>
        <li>The console never shows credential values or API keys.</li>
      </ul>
    ),
  },
  {
    id: "transit",
    title: "In transit",
    body: (
      <p>
        The website, the dashboard, the API and the hosted MCP server are served only over HTTPS. Okwan reaches
        Stripe, PayPal, Shopify, Square, Clover, Avalara, Paystack and WhatsApp over HTTPS, and its connection to our own database is
        encrypted; our database provider, Neon, accepts no other kind. If you connect your own Postgres database,
        the connection string you save decides whether that connection is encrypted, so include{" "}
        <code>sslmode=require</code>.
      </p>
    ),
  },
  {
    id: "not-yet",
    title: "What is not in place yet",
    body: (
      <>
        <p>We would rather you hear these from us:</p>
        <ul>
          <li><strong>A KMS for the master key.</strong> As above: the master key is in our host&rsquo;s encrypted settings today.</li>
          <li><strong>Master key rotation.</strong> The design allows a new master key by re-encrypting only the per-credential keys, but there is no tool for it yet.</li>
          <li><strong>A second factor at sign-in.</strong> Staff need an authenticator code; customer accounts use a password only.</li>
          <li><strong>Scoped API keys and roles.</strong> Every API key can read everything its account can.</li>
          <li><strong>An independent audit.</strong> We have not completed an independent security review or a certification such as SOC 2. A review is planned, and this page will link to it.</li>
        </ul>
      </>
    ),
  },
  {
    id: "more",
    title: "Retention, providers and breaches",
    body: (
      <p>
        How long each kind of information is kept, the companies that process it for us, and what we do if a breach
        affects you are in the <a href="/privacy">Privacy Policy</a>.
      </p>
    ),
  },
  {
    id: "report",
    title: "Report a security problem",
    body: (
      <p>
        Email <Mail /> with what you found and how to reproduce it. Please do not read, change or keep other
        people&rsquo;s data while you look, and give us time to fix the problem before you make it public. We
        reply to every report.
      </p>
    ),
  },
];

export default function Security() {
  return (
    <LegalPage
      title="Security"
      updated="November 9, 2026"
      intro={
        <>
          <p>How Okwan handles the access you give it, and what it does not do yet. In short:</p>
          <ul className="mt-4">
            <li>Okwan never writes to your payment, commerce or bank systems, so it cannot move money or change your records. The only thing it sends is alert messages you set up, through WhatsApp.</li>
            <li>Each saved credential is encrypted under its own key, which is in turn encrypted by one master key held in our host&rsquo;s encrypted settings. Moving that key to a cloud KMS is planned.</li>
            <li>Records from your systems are read in memory for each request; only results are stored. From a file you import, only the columns you map are kept.</li>
            <li>Every read of your data is recorded with the key and the address it came from, and a key used from somewhere new is emailed to you.</li>
            <li>Accounts are kept apart by two independent controls, each tested on its own.</li>
          </ul>
        </>
      }
      parts={parts}
    />
  );
}
