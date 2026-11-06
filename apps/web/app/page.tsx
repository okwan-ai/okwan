/* Okwan public site. A server component: everything renders as HTML,
   and the only motion is CSS, which prefers-reduced-motion turns off. */

import { OkwanLogo, OkwanMark, Wrap } from "./_components/brand";

const GITHUB = "https://github.com/okwan-ai/okwan";
const API_DOCS = "https://api.okwan.ai/docs";
const CONTACT = "mailto:info@globaltechstartup.com?subject=Okwan%20%E2%80%94%20reconciliation";

/* ── Brand ──────────────────────────────────────────────────────── */

/** The signal dot that ends the headline: the mark's yellow point. */
function SignalDot() {
  return (
    <span
      aria-hidden="true"
      className="ml-[0.06em] inline-block h-[0.2em] w-[0.2em] translate-y-[-0.02em] rounded-full border-[0.035em] border-ink bg-signal align-baseline"
    />
  );
}

function Eyebrow({ children, light = false }: { children: React.ReactNode; light?: boolean }) {
  return (
    <p className={`flex items-center gap-2 font-mono text-[12px] uppercase tracking-[0.14em] ${light ? "text-night-soft" : "text-ink-muted"}`}>
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-signal" aria-hidden="true" />
      {children}
    </p>
  );
}

/* ── Path glyphs ─────────────────────────────────────────────────
   Okwan is Twi for "path". Every verdict is drawn as the path between
   a record and its counterpart: one line when they agree, a fork when
   one order was paid twice, a break where nothing accounts for a gap,
   and a path that runs out where the other side has nothing.       */

type Shape =
  | "once" | "twice" | "explained" | "unexplained"
  | "ambiguous" | "unpaid" | "orphan" | "unverifiable";

const INK = "#0A0A0A";
const SOFT = "#A3A3A3";
const SIGNAL = "#FFD400";

function PathGlyph({ shape, delay = 0, className = "" }: { shape: Shape; delay?: number; className?: string }) {
  const node = (cx: number, cy: number, fill = INK, stroke = "none", dashed = false) => (
    <circle cx={cx} cy={cy} r="3.6" fill={fill} stroke={stroke} strokeWidth="1.5" strokeDasharray={dashed ? "2 2" : undefined} />
  );
  const line = { stroke: INK, strokeWidth: 1.6, fill: "none", strokeLinecap: "round" as const };
  const faint = { stroke: SOFT, strokeWidth: 1.6, fill: "none", strokeDasharray: "3 4", strokeLinecap: "round" as const };

  let body: React.ReactNode;
  switch (shape) {
    case "once":
      body = (<><path d="M10 22H102" {...line} />{node(6, 22)}{node(106, 22)}</>);
      break;
    case "twice":
      body = (
        <>
          <path d="M10 22C44 22 58 9 102 9" {...line} />
          <path d="M10 22C44 22 58 35 102 35" {...line} />
          {node(6, 22)}
          {node(106, 9, SIGNAL, INK)}
          {node(106, 35, SIGNAL, INK)}
        </>
      );
      break;
    case "explained":
      body = (
        <>
          <path d="M10 22H102" {...line} />
          <rect x="52" y="18" width="8" height="8" transform="rotate(45 56 22)" fill="#FFFFFF" stroke={INK} strokeWidth="1.5" />
          {node(6, 22)}{node(106, 22)}
        </>
      );
      break;
    case "unexplained":
      body = (
        <>
          <path d="M10 22H47M65 22H102" {...line} />
          <circle cx="56" cy="22" r="5" fill={SIGNAL} stroke={INK} strokeWidth="1.5" />
          {node(6, 22)}{node(106, 22)}
        </>
      );
      break;
    case "ambiguous":
      body = (
        <>
          <path d="M10 22H40" {...line} />
          <path d="M40 22C62 22 70 9 102 9" {...faint} stroke={INK} />
          <path d="M40 22C62 22 70 35 102 35" {...faint} stroke={INK} />
          {node(6, 22)}
          {node(106, 9, "#FFFFFF", INK)}
          {node(106, 35, "#FFFFFF", INK)}
        </>
      );
      break;
    case "unpaid":
      body = (
        <>
          <path d="M10 22H60" {...line} />
          <path d="M66 22H100" {...faint} />
          {node(6, 22)}
          {node(106, 22, "#FFFFFF", SOFT, true)}
        </>
      );
      break;
    case "orphan":
      body = (
        <>
          <path d="M12 22H46" {...faint} />
          <path d="M52 22H102" {...line} />
          {node(6, 22, "#FFFFFF", SOFT, true)}
          {node(106, 22)}
        </>
      );
      break;
    case "unverifiable":
      body = (
        <>
          <path d="M10 22H100" {...faint} />
          {node(6, 22)}
          <circle cx="106" cy="22" r="5" fill="#FFFFFF" stroke={SOFT} strokeWidth="1.5" />
          <path d="M104.4 20.2a1.8 1.8 0 1 1 2.4 1.7c-.5.2-.8.6-.8 1.1" stroke={SOFT} strokeWidth="1.2" fill="none" strokeLinecap="round" />
        </>
      );
      break;
  }
  return (
    <svg viewBox="0 0 112 44" className={`h-11 w-28 shrink-0 ${className}`} aria-hidden="true">
      <g className="path-draw" style={{ animationDelay: `${delay}s` }}>{body}</g>
    </svg>
  );
}

/* ── Hero result ─────────────────────────────────────────────────
   An example modelled on a test-mode run across a Shopify store,
   Stripe and PayPal, in which the engine found #1002 and #1004 paid
   on both rails. Labelled as an example on the page, not as live. */

type Tone = "signal" | "plain" | "outline";

const RUN: {
  order: string; total: string; rails: [string, string][];
  shape: Shape; verdict: string; tone: Tone; note: string;
}[] = [
  { order: "#1002", total: "$999.00", rails: [["Stripe", "$999.00"], ["PayPal", "$999.00"]],
    shape: "twice", verdict: "Charged twice", tone: "signal",
    note: "Both rails took the full total. $999.00 is owed back." },
  { order: "#1003", total: "$150.00", rails: [["Stripe", "$150.00"]],
    shape: "once", verdict: "Paid once", tone: "plain",
    note: "Matched on the order reference." },
  { order: "#1004", total: "$450.00", rails: [["Stripe", "$450.00"], ["PayPal", "$450.00"]],
    shape: "twice", verdict: "Charged twice", tone: "signal",
    note: "The same order, collected on both rails." },
  { order: "#1005", total: "$120.00", rails: [["PayPal", "$115.32"]],
    shape: "explained", verdict: "Fee explained", tone: "plain",
    note: "PayPal's $4.68 fee accounts for the gap exactly." },
  { order: "#1006", total: "$75.00", rails: [],
    shape: "unpaid", verdict: "No payment", tone: "outline",
    note: "Marked paid in the ledger. Nothing on any rail." },
  { order: "—", total: "", rails: [["PayPal", "$333.00"]],
    shape: "orphan", verdict: "No order", tone: "outline",
    note: "Collected against #9999, which doesn't exist." },
];

const TONE: Record<Tone, string> = {
  signal: "border-ink bg-signal text-ink",
  plain: "border-line-strong bg-paper text-ink-soft",
  outline: "border-ink bg-paper text-ink",
};

function Chip({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full border px-2.5 py-0.5 font-mono text-[11.5px] font-medium ${TONE[tone]}`}>
      {children}
    </span>
  );
}

function ResultPanel() {
  return (
    <figure className="relative">
      <div className="overflow-hidden rounded-[20px] border border-line bg-paper shadow-[0_1px_0_rgba(0,0,0,0.04),0_24px_60px_-28px_rgba(0,0,0,0.25)]">
        <div className="flex items-start justify-between gap-4 border-b border-line bg-mist/60 px-5 py-3.5">
          <div className="min-w-0">
            <p className="font-mono text-[12.5px] text-ink">recon.across_rails</p>
            <p className="mt-0.5 text-[13px] text-ink-muted">Shopify orders against Stripe and PayPal</p>
          </div>
          <span className="shrink-0 rounded-full border border-line-strong bg-paper px-2.5 py-0.5 font-mono text-[11px] text-ink-muted">
            Example · test data
          </span>
        </div>
        <ul>
          {RUN.map((r, i) => (
            <li
              key={r.order + i}
              className="settle grid grid-cols-[74px_minmax(0,1fr)] items-center gap-x-3 gap-y-2 border-b border-line px-5 py-3.5 last:border-0 sm:grid-cols-[78px_112px_136px_minmax(0,1fr)]"
              style={{ animationDelay: `${0.25 + i * 0.12}s` }}
            >
              <div>
                <p className="font-mono text-[13px] font-medium text-ink">{r.order}</p>
                <p className="font-mono text-[12px] text-ink-muted">{r.total || "no order"}</p>
              </div>
              <PathGlyph shape={r.shape} delay={0.35 + i * 0.12} className="hidden sm:block" />
              <div className="hidden space-y-0.5 sm:block">
                {r.rails.length === 0 ? (
                  <p className="font-mono text-[12px] text-ink-muted">nothing collected</p>
                ) : (
                  r.rails.map(([rail, amount]) => (
                    <p key={rail} className="font-mono text-[12px] text-ink-soft">
                      <span className="text-ink">{rail}</span> {amount}
                    </p>
                  ))
                )}
              </div>
              <div className="min-w-0">
                <Chip tone={r.tone}>{r.verdict}</Chip>
                <p className="mt-1 text-[13px] leading-snug text-ink-soft">{r.note}</p>
              </div>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line bg-ink px-5 py-3.5 text-white">
          <p className="text-[13.5px]">
            <span className="mr-2 inline-block h-2 w-2 rounded-full bg-signal align-middle" aria-hidden="true" />
            2 orders charged twice · <span className="font-semibold">$1,449.00 owed back</span>
          </p>
          <p className="font-mono text-[11.5px] text-night-soft">read: Shopify · Stripe · PayPal</p>
        </div>
      </div>
      <figcaption className="mt-3 max-w-[560px] text-[13px] leading-relaxed text-ink-muted">
        An example modelled on a test-mode run across a Shopify store, Stripe and PayPal, in which
        the engine found orders #1002 and #1004 charged on both. Each rail on its own reported a
        clean match; only the check across them shows the double charge.
      </figcaption>
    </figure>
  );
}

/* ── Sections ───────────────────────────────────────────────────── */

function Header() {
  return (
    <header className="sticky top-0 z-40 border-b border-line/80 bg-paper/85 backdrop-blur-md">
      <Wrap className="flex h-16 items-center justify-between gap-6">
        <a href="#top" aria-label="Okwan home"><OkwanLogo width={104} /></a>
        <nav className="hidden items-center gap-8 text-[14.5px] text-ink-soft md:flex" aria-label="Sections">
          <a className="hover:text-ink" href="#retail">Retail</a>
          <a className="hover:text-ink" href="#industries">Industries</a>
          <a className="hover:text-ink" href="#how">How it works</a>
          <a className="hover:text-ink" href="#security">Security</a>
        </nav>
        <div className="flex items-center gap-5">
          <a className="hidden text-[14.5px] text-ink-soft hover:text-ink sm:inline" href={API_DOCS}>API docs</a>
          <a
            href={CONTACT}
            className="rounded-full bg-ink px-4 py-2 text-[14px] font-medium text-white transition-colors hover:bg-[#262626] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
          >
            Book a call
          </a>
        </div>
      </Wrap>
    </header>
  );
}

function Hero() {
  return (
    <section id="top" className="relative overflow-hidden">
      <div aria-hidden="true" className="hero-field pointer-events-none absolute inset-0" />
      <Wrap className="relative grid items-center gap-14 pb-20 pt-14 sm:pt-20 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] lg:gap-16 lg:pb-28 lg:pt-24">
        <div>
          <Eyebrow>Reconciliation infrastructure · retail &amp; payments</Eyebrow>
          <h1 className="mt-6 text-[46px] font-semibold leading-[0.98] tracking-[-0.04em] sm:text-[64px] xl:text-[76px]">
            Find where your systems{" "}
            <span className="whitespace-nowrap">disagree<SignalDot /></span>
          </h1>
          <p className="mt-7 max-w-[560px] text-[18px] leading-[1.6] text-ink-soft">
            Your POS, your processors, your order ledger and your bank each tell a different story
            about the same money. Okwan matches them record by record and says what agrees, what
            differs and why, as an engine your product embeds.
          </p>
          <div className="mt-9 flex flex-wrap items-center gap-3">
            <a
              href={CONTACT}
              className="rounded-full bg-ink px-6 py-3 text-[15px] font-medium text-white transition-colors hover:bg-[#262626] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
            >
              Book a call
            </a>
            <a
              href={API_DOCS}
              className="rounded-full border border-line-strong bg-paper px-6 py-3 text-[15px] font-medium text-ink transition-colors hover:border-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
            >
              Read the API docs
            </a>
          </div>
          <ul className="mt-10 flex flex-wrap gap-x-7 gap-y-2.5 text-[14px] text-ink-soft">
            {["Read-only by construction", "Credentials sealed per tenant", "Open core, Apache-2.0"].map((t) => (
              <li key={t} className="flex items-center gap-2 whitespace-nowrap">
                <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M3 8.5l3 3 7-7" stroke="#0A0A0A" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                {t}
              </li>
            ))}
          </ul>
        </div>
        <ResultPanel />
      </Wrap>
    </section>
  );
}

function Facts() {
  const facts = [
    ["6", "connectors live today"],
    ["7", "verdicts, where most tools give two"],
    ["1 → 3", "one definition becomes an API, a SQL view and an agent tool"],
    ["0", "write access to any payment rail"],
  ];
  return (
    <section className="border-y border-line bg-paper">
      <Wrap>
        <div className="grid grid-cols-2 gap-px bg-line lg:grid-cols-4">
          {facts.map(([n, label]) => (
            <div key={label} className="bg-paper py-8 pr-4 [&:nth-child(even)]:pl-6 lg:[&:not(:first-child)]:pl-8">
              <p className="text-[34px] font-semibold tracking-[-0.03em] sm:text-[40px]">{n}</p>
              <p className="mt-1 max-w-[220px] text-[14px] leading-snug text-ink-soft">{label}</p>
            </div>
          ))}
        </div>
      </Wrap>
    </section>
  );
}

function SectionHead({ eyebrow, title, intro, light = false }: { eyebrow: string; title: string; intro?: string; light?: boolean }) {
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)] lg:items-end lg:gap-16">
      <div>
        <Eyebrow light={light}>{eyebrow}</Eyebrow>
        <h2 className={`mt-4 text-[36px] font-semibold leading-[1.04] tracking-[-0.035em] sm:text-[48px] ${light ? "text-white" : "text-ink"}`}>
          {title}
        </h2>
      </div>
      {intro && (
        <p className={`text-[17px] leading-[1.6] ${light ? "text-night-soft" : "text-ink-soft"}`}>{intro}</p>
      )}
    </div>
  );
}

const RETAIL: { title: string; body: string; status: "live" | "next"; detail?: string }[] = [
  { status: "live", title: "Orders against every payment rail",
    body: "Each order traced to the rail that collected it, across Stripe and PayPal at once: paid once, charged twice, split across rails, or not paid at all.",
    detail: "GET /v1/reconciliations/across/rails" },
  { status: "live", title: "Fees and refunds, explained",
    body: "When a processor fee or a refund accounts for a gap exactly, it is named as the cause, so the list you chase holds only real breaks.",
    detail: "explains=[rail_fee, refund]" },
  { status: "live", title: "Your own database, against any rail",
    body: "Point a declaration at a query over your own Postgres and reconcile it against a processor like any other system.",
    detail: 'ResourceRef(connector="postgres", resource="sql")' },
  { status: "next", title: "POS batches against processor deposits",
    body: "Card batches closed at the register against what the processor actually deposited, by day and by location." },
  { status: "next", title: "Gift cards, tips and chargebacks",
    body: "The adjustments that make a day's sales and a day's deposits disagree, each named rather than netted away." },
  { status: "next", title: "Residuals against the merchant book",
    body: "For dealers and ISOs: processor residual statements checked against the merchants and volume you expect to be paid on." },
];

function Retail() {
  return (
    <section id="retail" className="bg-mist">
      <Wrap className="py-24 lg:py-32">
        <SectionHead
          eyebrow="Retail first"
          title="Built for the retail stack."
          intro="For POS dealers, ISVs and payment providers whose merchants sell in store and online, and collect on more than one system. Three reconciliations run today; the next three are chosen with the platforms we work with."
        />
        <div className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {RETAIL.map((c) => (
            <article
              key={c.title}
              className={`flex min-w-0 flex-col rounded-[18px] border p-6 ${c.status === "live" ? "border-line bg-paper" : "border-dashed border-line-strong bg-mist"}`}
            >
              <span
                className={`inline-flex w-fit items-center gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-[11px] ${c.status === "live" ? "bg-ink text-white" : "border border-line-strong text-ink-muted"}`}
              >
                {c.status === "live" && <span className="h-1.5 w-1.5 rounded-full bg-signal" aria-hidden="true" />}
                {c.status === "live" ? "Live" : "In discovery"}
              </span>
              <h3 className="mt-5 text-[19px] font-semibold tracking-[-0.015em]">{c.title}</h3>
              <p className="mt-2 flex-1 text-[15px] leading-relaxed text-ink-soft">{c.body}</p>
              {c.detail && (
                <p className="mt-5 truncate border-t border-line pt-4 font-mono text-[12px] text-ink-muted">{c.detail}</p>
              )}
            </article>
          ))}
        </div>
        <p className="mt-8 text-[15px] text-ink-soft">
          Which of the three in discovery costs your merchants the most?{" "}
          <a href={CONTACT} className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
            Tell us
          </a>
          ; it decides what we build next.
        </p>
      </Wrap>
    </section>
  );
}

const INDUSTRIES = [
  { name: "Healthcare", pair: "Claims ↔ remittances", body: "What was billed against what payers actually paid, and why the rest did not arrive." },
  { name: "Logistics", pair: "Carrier invoices ↔ shipments", body: "Every invoiced shipment against one that moved, at the rate that was agreed." },
  { name: "Subscriptions", pair: "Billing ↔ payouts", body: "Invoices and renewals against the money that settled, after fees, refunds and disputes." },
  { name: "Marketplaces", pair: "Orders ↔ seller payouts", body: "What buyers paid against what each seller received, net of commission." },
];

function Industries() {
  return (
    <section id="industries" className="bg-paper">
      <Wrap className="py-24 lg:py-32">
        <SectionHead
          eyebrow="Beyond retail"
          title="One engine. Any two systems that should agree."
          intro="Retail is where we start, not where the engine stops. A reconciliation is a declaration over any two systems that can list their records, so a new industry is a set of connectors, not a new product."
        />
        <div className="mt-14 grid gap-px overflow-hidden rounded-[18px] border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
          {INDUSTRIES.map((s) => (
            <article key={s.name} className="flex flex-col bg-paper p-6">
              <p className="text-[13px] font-medium text-ink-muted">{s.name}</p>
              <p className="mt-3 font-mono text-[14px] text-ink">{s.pair}</p>
              <PathGlyph shape="once" className="mt-4 h-8 w-24" />
              <p className="mt-3 text-[15px] leading-relaxed text-ink-soft">{s.body}</p>
            </article>
          ))}
        </div>
        <p className="mt-6 text-[14px] text-ink-muted">
          These use the same engine as retail; each needs its systems&apos; connectors first.
        </p>
      </Wrap>
    </section>
  );
}

const VERDICTS: { shape: Shape; name: string; body: string }[] = [
  { shape: "once", name: "Agrees", body: "Matched, and the amounts agree." },
  { shape: "explained", name: "Differs, explained", body: "Matched; a fee or a refund accounts for the gap exactly." },
  { shape: "unexplained", name: "Differs, unexplained", body: "Matched, and nothing accounts for the gap. The number to chase." },
  { shape: "ambiguous", name: "Ambiguous", body: "More than one equally good candidate. Reported, never guessed." },
  { shape: "unpaid", name: "No payment", body: "In the ledger, and on no rail that was read." },
  { shape: "orphan", name: "No order", body: "Collected, with no record it belongs to." },
  { shape: "unverifiable", name: "Unverifiable", body: "The other side was not read far enough back to say. Never called missing." },
];

function Verdicts() {
  return (
    <section className="border-t border-line bg-paper">
      <Wrap className="py-24 lg:py-32">
        <SectionHead
          eyebrow="The distinctions are the product"
          title="Seven verdicts, not two."
          intro="A tool that only says matched or unmatched turns every refund into a false alarm and pairs look-alike payments by coin flip. Okwan tells them apart, and says what it could not see."
        />
        <div className="mt-14 grid gap-x-8 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
          {VERDICTS.map((v, i) => (
            <div key={v.name}>
              <PathGlyph shape={v.shape} delay={i * 0.08} />
              <h3 className="mt-3 text-[17px] font-semibold tracking-[-0.01em]">{v.name}</h3>
              <p className="mt-1.5 text-[14.5px] leading-relaxed text-ink-soft">{v.body}</p>
            </div>
          ))}
        </div>
      </Wrap>
    </section>
  );
}

const DECLARATION = `Reconciliation(
    name="shopify_stripe",
    left=ResourceRef(connector="shopify", resource="orders",
                     operation="list"),
    right=ResourceRef(connector="stripe", resource="charges",
                      operation="list", params={"status": "succeeded"}),
    keys=[
        ExactRef(left="name", right="order_ref"),
        Fuzzy(amount="total_received_minor", currency="currency",
              amount_right="amount", timestamp_right="created",
              window="7d"),
    ],
    amount=AmountRef(left="net_payment_minor", right="net_minor"),
    explains=[Explains(path="fee_minor", side="right", label="rail_fee")],
)`;

const SURFACES = [
  { tag: "REST", title: "An endpoint", body: "GET /v1/reconciliations/shopify_stripe, with request validation and an OpenAPI reference." },
  { tag: "SQL", title: "A queryable view", body: "recon.shopify_stripe, joinable with anything else in the catalog and fetched live at query time." },
  { tag: "MCP", title: "A tool for your agents", body: "okwan_reconcile on the hosted MCP server. Read-only by construction." },
];

function How() {
  return (
    <section id="how" className="bg-night text-white">
      <Wrap className="py-24 lg:py-32">
        <SectionHead
          light
          eyebrow="How it works"
          title="One definition. Three interfaces."
          intro="Declare the match once. Okwan generates a REST route, a SQL view and an agent tool from that one declaration, with nothing hand-written in between, so the three can never drift apart."
        />
        <div className="mt-14 grid gap-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
          <pre className="min-w-0 overflow-x-auto rounded-[18px] border border-night-line bg-night-2 p-6 font-mono text-[12.5px] leading-[1.75] text-[#E5E5E5] sm:p-8">
            <code>{DECLARATION}</code>
          </pre>
          <div className="grid gap-4">
            {SURFACES.map((s) => (
              <div key={s.tag} className="rounded-[18px] border border-night-line bg-night-2 p-6">
                <div className="flex items-center gap-3">
                  <span className="rounded-md border border-night-line px-2 py-0.5 font-mono text-[11.5px] text-white">{s.tag}</span>
                  <h3 className="text-[16px] font-semibold">{s.title}</h3>
                </div>
                <p className="mt-3 font-mono text-[12.5px] leading-relaxed text-night-soft">{s.body}</p>
              </div>
            ))}
          </div>
        </div>
      </Wrap>
    </section>
  );
}

const CONNECTORS = ["Shopify", "Stripe", "PayPal", "Paystack", "PostgreSQL", "WhatsApp"];

const QUERY = `curl https://api.okwan.ai/v1/query \\
  -H "Authorization: Bearer okw_…" \\
  -d '{"sql": "SELECT o.name, o.total_price_minor, c.amount
               FROM shopify.orders o
               LEFT JOIN stripe.charges c
                 ON c.order_ref = o.name"}'`;

function Developers() {
  return (
    <section id="developers" className="bg-paper">
      <Wrap className="grid gap-12 py-24 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:items-center lg:gap-16 lg:py-32">
        <div>
          <Eyebrow>For developers</Eyebrow>
          <h2 className="mt-4 text-[36px] font-semibold leading-[1.04] tracking-[-0.035em] sm:text-[48px]">
            Query across systems in one statement.
          </h2>
          <p className="mt-5 max-w-[520px] text-[17px] leading-[1.6] text-ink-soft">
            Federated SQL over live connectors. Only the tables a query names are fetched, and only
            when it runs: no pipeline, no copy of your merchants&apos; data.
          </p>
          <p className="mt-8 font-mono text-[12px] uppercase tracking-[0.14em] text-ink-muted">Connected today</p>
          <ul className="mt-3 flex flex-wrap gap-2">
            {CONNECTORS.map((c) => (
              <li key={c} className="rounded-full border border-line-strong px-3 py-1 text-[14px] text-ink">{c}</li>
            ))}
          </ul>
          <a href={API_DOCS} className="mt-8 inline-flex items-center gap-2 text-[15px] font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
            Read the API reference <span aria-hidden="true">→</span>
          </a>
        </div>
        <pre className="min-w-0 overflow-x-auto rounded-[18px] bg-night p-6 font-mono text-[12.5px] leading-[1.75] text-[#E5E5E5] sm:p-8">
          <code>{QUERY}</code>
        </pre>
      </Wrap>
    </section>
  );
}

const TRUST = [
  { title: "Read-only by construction", body: "A reconciliation over a write operation cannot be declared. Okwan reads; it never moves money." },
  { title: "Credentials sealed per tenant", body: "Each stored credential has its own encryption key and is bound to its tenant, connector and field. None travels in a request." },
  { title: "Keys shown once", body: "API keys are stored as hashes. A lost key is replaced, never read back." },
  { title: "Isolated by default", body: "Another account's tenant answers 404, not 403, so the API cannot reveal who else is a customer." },
];

function Security() {
  return (
    <section id="security" className="border-t border-line bg-mist">
      <Wrap className="py-24 lg:py-32">
        <SectionHead
          eyebrow="Security"
          title="Built to be trusted with merchants' money."
          intro="Okwan holds credentials to your merchants' payment systems, so the guarantees are structural: enforced by how the system is built, not by a setting someone can change."
        />
        <div className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {TRUST.map((t) => (
            <div key={t.title} className="rounded-[18px] border border-line bg-paper p-6">
              <h3 className="text-[16px] font-semibold tracking-[-0.01em]">{t.title}</h3>
              <p className="mt-2 text-[14.5px] leading-relaxed text-ink-soft">{t.body}</p>
            </div>
          ))}
        </div>
      </Wrap>
    </section>
  );
}

function Closing() {
  return (
    <section className="relative overflow-hidden bg-night text-white">
      <Wrap className="relative py-24 text-center lg:py-32">
        <div className="mx-auto mb-8 w-fit"><OkwanMark size={56} light /></div>
        <h2 className="mx-auto max-w-[820px] text-[40px] font-semibold leading-[1.02] tracking-[-0.04em] sm:text-[60px]">
          Tell us what your merchants can&apos;t reconcile.
        </h2>
        <p className="mx-auto mt-6 max-w-[600px] text-[17px] leading-[1.6] text-night-soft">
          We are working with a small number of retail and payments platforms. Bring one
          reconciliation your team or your merchants do by hand, and we will show you the verdicts.
        </p>
        <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
          <a
            href={CONTACT}
            className="rounded-full bg-white px-6 py-3 text-[15px] font-medium text-ink transition-colors hover:bg-[#E5E5E5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
          >
            Book a call
          </a>
          <a
            href={API_DOCS}
            className="rounded-full border border-night-line px-6 py-3 text-[15px] font-medium text-white transition-colors hover:border-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
          >
            Read the API docs
          </a>
        </div>
      </Wrap>
    </section>
  );
}

function Footer() {
  const cols: [string, [string, string][]][] = [
    ["Product", [["Retail", "#retail"], ["Industries", "#industries"], ["How it works", "#how"], ["Security", "#security"]]],
    ["Developers", [["API docs", API_DOCS], ["GitHub", GITHUB], ["Apache-2.0 license", `${GITHUB}/blob/main/LICENSE`]]],
    ["Company", [["Contact", CONTACT], ["Privacy", "/privacy"], ["Terms", "/terms"]]],
  ];
  return (
    <footer className="border-t border-night-line bg-night text-night-soft">
      <Wrap className="grid gap-10 py-14 sm:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(0,1fr))]">
        <div>
          <OkwanLogo width={104} light />
          <p className="mt-4 max-w-[280px] text-[14px] leading-relaxed">
            Reconciliation infrastructure. Okwan is Twi for &ldquo;path&rdquo;: the path between
            systems that disagree.
          </p>
        </div>
        {cols.map(([title, links]) => (
          <div key={title}>
            <p className="text-[13px] font-medium text-white">{title}</p>
            <ul className="mt-4 space-y-2.5 text-[14px]">
              {links.map(([label, href]) => (
                <li key={label}><a className="hover:text-white" href={href}>{label}</a></li>
              ))}
            </ul>
          </div>
        ))}
      </Wrap>
      <Wrap className="border-t border-night-line py-6 text-[13px]">
        <p>© 2026 Global Tech Startup LLC</p>
      </Wrap>
    </footer>
  );
}

export default function Home() {
  return (
    <main>
      <Header />
      <Hero />
      <Facts />
      <Retail />
      <Industries />
      <Verdicts />
      <How />
      <Developers />
      <Security />
      <Closing />
      <Footer />
    </main>
  );
}
