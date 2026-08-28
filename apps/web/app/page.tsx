"use client";

import { useEffect, useRef, useState } from "react";

const GITHUB = "https://github.com/okwan-ai/okwan";
const API = "https://okwan.onrender.com";

/* ── Wordmark: pipe glyph (two strokes, data dot passing through) ── */
function Mark({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path d="M6 8h20" stroke="#111111" strokeWidth="3.5" strokeLinecap="round" />
      <path d="M6 24h20" stroke="#111111" strokeWidth="3.5" strokeLinecap="round" />
      <circle cx="16" cy="16" r="4.5" fill="#FFD400" stroke="#111111" strokeWidth="2" />
    </svg>
  );
}

function Wordmark() {
  return (
    <span className="flex items-center gap-2">
      <Mark />
      <span className="font-body text-[22px] font-semibold tracking-tight">okwan</span>
    </span>
  );
}

/* ── Signature: a real reconciliation result ──────────────────────
   These are the actual numbers from a live run against a Shopify
   store and a payment ledger. Six orders, six different verdicts —
   the distinctions are the product.                              */

type Verdict = "agrees" | "explained" | "ambiguous" | "unpaid" | "orphan";

const VERDICT: Record<Verdict, { label: string; tone: string }> = {
  agrees:    { label: "agrees",             tone: "border-line text-ink-soft" },
  explained: { label: "differs · refund",   tone: "border-ink bg-volt text-ink" },
  ambiguous: { label: "ambiguous",          tone: "border-ink bg-sky text-navy" },
  unpaid:    { label: "no payment",         tone: "border-ink text-ink" },
  orphan:    { label: "no order",           tone: "border-ink text-ink" },
};

const RESULT: {
  order: string; ledger: string; rail: string; verdict: Verdict; note: string;
}[] = [
  { order: "#1001", ledger: "$299.00", rail: "$2,423.00", verdict: "explained",
    note: "rail holds the gross charge; the ledger nets out a $2,124 refund the feed cannot express" },
  { order: "#1002", ledger: "$999.00", rail: "$999.00", verdict: "agrees",
    note: "matched on reference, amounts agree" },
  { order: "#1003", ledger: "$150.00", rail: "$150.00", verdict: "agrees",
    note: "matched on reference, amounts agree" },
  { order: "#1004", ledger: "$450.00", rail: "—", verdict: "ambiguous",
    note: "two reference-less $450 payments, two $450 orders — not entitled to pick one" },
  { order: "#1005", ledger: "$450.00", rail: "—", verdict: "ambiguous",
    note: "same pair; reported unresolved rather than paired arbitrarily" },
  { order: "#1006", ledger: "$75.00", rail: "—", verdict: "unpaid",
    note: "marked paid in the ledger, nothing collected" },
  { order: "—", ledger: "—", rail: "$333.00", verdict: "orphan",
    note: "collected against order #9999, which does not exist" },
];

function ReconResult() {
  const [stage, setStage] = useState(-1);
  const [started, setStarted] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) { setStage(RESULT.length); setStarted(true); return; }

    const io = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) { setStarted(true); io.disconnect(); } },
      { threshold: 0.25 }
    );
    io.observe(node);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!started) return;
    if (stage >= RESULT.length) return;
    const t = setTimeout(() => setStage((s) => s + 1), stage < 0 ? 400 : 260);
    return () => clearTimeout(t);
  }, [started, stage]);

  const done = stage >= RESULT.length;
  const settled = RESULT.slice(0, Math.max(0, stage));

  return (
    <div ref={ref} className="overflow-hidden rounded-2xl border border-line bg-surface">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-line px-5 py-4 sm:px-7">
        <p className="flex items-center gap-2 font-mono text-[12.5px] text-ink-soft">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full transition-colors duration-500 ${
              done ? "bg-ink-soft" : "animate-pulse bg-volt-deep"
            }`}
            aria-hidden="true"
          />
          reconcile · shopify.orders ↔ payment rail
        </p>
        <p className="font-mono text-[12.5px] text-ink-soft">
          {done ? (
            <>net unexplained <span className="font-medium text-ink">$0.00</span></>
          ) : (
            <span className="text-ink-soft">matching {settled.length}/{RESULT.length}…</span>
          )}
        </p>
      </div>

      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line font-mono text-[11.5px] uppercase tracking-wider text-ink-soft">
            <th className="px-5 py-3 font-normal sm:px-7">Order</th>
            <th className="px-3 py-3 text-right font-normal">Ledger</th>
            <th className="px-3 py-3 text-right font-normal">Rail</th>
            <th className="px-5 py-3 font-normal sm:px-7">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {RESULT.map((r, i) => {
            const shown = i < stage;
            return (
              <tr
                key={i}
                className="border-b border-line align-top transition-all duration-500 ease-out last:border-0"
                style={{
                  opacity: shown ? 1 : 0,
                  transform: shown ? "translateY(0)" : "translateY(6px)",
                }}
              >
                <td className="px-5 py-4 font-mono text-[13.5px] sm:px-7">{r.order}</td>
                <td className="px-3 py-4 text-right font-mono text-[13.5px] text-ink-soft">{r.ledger}</td>
                <td className="px-3 py-4 text-right font-mono text-[13.5px] text-ink-soft">{r.rail}</td>
                <td className="px-5 py-4 sm:px-7">
                  <span className={`inline-block rounded-md border px-2 py-0.5 font-mono text-[11.5px] ${VERDICT[r.verdict].tone}`}>
                    {VERDICT[r.verdict].label}
                  </span>
                  <p className="mt-1.5 max-w-[42ch] font-body text-[13px] leading-relaxed text-ink-soft">
                    {r.note}
                  </p>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ── Building blocks ─────────────────────────────────────────────── */
function VoltButton({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href}
      className="inline-block rounded-xl bg-volt px-6 py-3 font-body text-[15px] font-semibold text-ink transition-colors hover:bg-volt-deep focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink">
      {children}
    </a>
  );
}

function GhostButton({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href}
      className="inline-block rounded-xl border border-ink px-6 py-3 font-body text-[15px] font-medium text-ink transition-colors hover:bg-ink hover:text-surface focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink">
      {children}
    </a>
  );
}

const declaration = `Reconciliation(
    name="shopify_orders",
    left  = ResourceRef("postgres", "sql", params={"sql": PAYMENTS}),
    right = ResourceRef("shopify", "orders"),

    keys=[
        ExactRef(left="reference", right="name"),
        Fuzzy(amount="amount", currency="currency",
              amount_right="net_payment_minor", window="7d"),
    ],

    amount   = AmountRef(left="amount", right="net_payment_minor"),
    explains = [Explains(path="total_refunded_minor", label="refund")],
)`;

const curlExample = `curl ${API}/v1/query \\
  -H "Authorization: Bearer okw_..." \\
  -d '{"sql": "SELECT o.name, o.net_payment_minor, p.amount
               FROM shopify.orders o
               LEFT JOIN rail.payments p ON p.reference = o.name"}'`;

/* ── Page ────────────────────────────────────────────────────────── */
export default function Home() {
  return (
    <main>
      <header className="mx-auto flex max-w-[920px] items-center justify-between px-5 py-6">
        <a href="#" aria-label="Okwan home"><Wordmark /></a>
        <nav className="flex items-center gap-6 font-body text-[15px]">
          <a className="hidden text-ink-soft hover:text-ink sm:inline" href="#how">How it works</a>
          <a className="hidden text-ink-soft hover:text-ink sm:inline" href="#api">API</a>
          <a className="hidden text-ink-soft hover:text-ink sm:inline" href="#fit">Who it's for</a>
          <a href={GITHUB}
            className="rounded-lg bg-volt px-4 py-2 font-semibold text-ink transition-colors hover:bg-volt-deep">
            GitHub
          </a>
        </nav>
      </header>

      {/* hero */}
      <section className="mx-auto max-w-[920px] px-5 pb-12 pt-10 sm:pt-16">
        <p className="rise mb-5 font-mono text-[13px] tracking-wide text-ink-soft" style={{ animationDelay: "0.05s" }}>
          Open core · Python · Apache-2.0
        </p>
        <h1 style={{ animationDelay: "0.15s" }} className="rise max-w-[820px] font-display text-[44px] font-normal leading-[1.05] tracking-tight sm:text-[68px]">
          Reconciliation as an API<span className="text-volt-deep">.</span>
        </h1>
        <p className="rise mt-6 max-w-[640px] font-body text-[17px] leading-relaxed text-ink-soft" style={{ animationDelay: "0.3s" }}>
          Your merchants collect money on rails that disagree with their own order
          ledger. Define the match once and Okwan gives you an API endpoint, a SQL
          view, and an MCP tool for your agents — from the same definition.
        </p>
        <div className="rise mt-9 flex flex-wrap items-center gap-3" style={{ animationDelay: "0.45s" }}>
          <VoltButton href="#api">See the API</VoltButton>
          <GhostButton href={GITHUB}>Read the source</GhostButton>
        </div>
      </section>

      {/* SIGNATURE */}
      <section className="mx-auto max-w-[920px] px-5 pb-6">
        <ReconResult />
        <p className="mt-4 font-body text-[14px] leading-relaxed text-ink-soft">
          A live run against a Shopify store and a payment ledger. Seven records,
          five different verdicts — the distinctions are the product. A tool that
          only says <span className="font-mono text-[13px]">matched</span> or{" "}
          <span className="font-mono text-[13px]">unmatched</span> would report
          #1001 as a $2,124 problem and pair #1004 with #1005 on a coin flip.
        </p>
      </section>

      {/* how */}
      <section id="how" className="border-y border-line bg-surface">
        <div className="mx-auto max-w-[920px] px-5 py-20">
          <h2 className="font-display text-[34px] leading-tight sm:text-[44px]">
            One declaration. Three interfaces.
          </h2>
          <p className="mt-4 max-w-[600px] font-body text-[16px] leading-relaxed text-ink-soft">
            The declaration above produced that table. It also produced a REST
            route, a SQL view, and an MCP tool — nothing was hand-written in
            between, so they cannot drift apart.
          </p>

          <div className="mt-12 grid gap-6 lg:grid-cols-[1.2fr_1fr]">
            <pre className="overflow-x-auto rounded-2xl bg-navy p-6 font-mono text-[12px] leading-relaxed text-[#E8EDF7]">
              <code>{declaration}</code>
            </pre>

            <div className="flex flex-col gap-4">
              {[
                { k: "REST", title: "An endpoint",
                  body: "GET /v1/reconciliations/shopify_orders. Request validation and OpenAPI docs come with it." },
                { k: "SQL", title: "A queryable view",
                  body: "Join it against anything else in the catalog. Tables are fetched live at query time — federation, not a pipeline." },
                { k: "MCP", title: "A tool your agent can call",
                  body: "Read-only by construction: a declaration pointing at a write operation is rejected before it exists." },
              ].map((c) => (
                <div key={c.k} className="rounded-2xl border border-line bg-canvas p-5">
                  <div className="flex items-center gap-3">
                    <span className="rounded-md bg-volt px-2 py-0.5 font-mono text-[12px] font-medium">{c.k}</span>
                    <h3 className="font-body text-[16px] font-semibold">{c.title}</h3>
                  </div>
                  <p className="mt-2 font-body text-[14px] leading-relaxed text-ink-soft">{c.body}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* api */}
      <section id="api" className="mx-auto max-w-[920px] px-5 py-20">
        <h2 className="font-display text-[34px] leading-tight sm:text-[44px]">
          Query across systems that disagree
        </h2>
        <p className="mt-4 max-w-[600px] font-body text-[16px] leading-relaxed text-ink-soft">
          One SQL statement over a live Shopify store, a live Stripe account, and
          your database. Only the tables the query names are fetched, and only
          when it runs.
        </p>

        <pre className="mt-10 overflow-x-auto rounded-2xl bg-navy p-6 font-mono text-[12.5px] leading-relaxed text-[#E8EDF7]">
          <code>{curlExample}</code>
        </pre>

        <div className="mt-6 grid gap-4 sm:grid-cols-3">
          {[
            { t: "Your keys stay yours", b: "Upstream credentials are stored server-side, sealed per tenant. They never travel in a request." },
            { t: "One key per merchant", b: "Provision a tenant per merchant through the API. Each gets an endpoint scoped to their own systems." },
            { t: "Read-only, structurally", b: "Not an annotation applied afterwards — a reconciliation over a write operation cannot be constructed." },
          ].map((c) => (
            <div key={c.t} className="rounded-xl border border-line p-5">
              <h3 className="font-body text-[15px] font-semibold">{c.t}</h3>
              <p className="mt-2 font-body text-[13.5px] leading-relaxed text-ink-soft">{c.b}</p>
            </div>
          ))}
        </div>

        <div className="mt-10 flex flex-wrap items-center gap-x-6 gap-y-2 font-mono text-[13px] text-ink-soft">
          <span className="text-ink">Connected today:</span>
          <span>Shopify</span><span>Stripe</span><span>Paystack</span>
          <span>PostgreSQL</span><span>WhatsApp</span>
        </div>
      </section>

      {/* fit */}
      <section id="fit" className="border-y border-line bg-surface">
        <div className="mx-auto max-w-[920px] px-5 py-20">
          <h2 className="font-display text-[34px] leading-tight sm:text-[44px]">
            Built for platforms, not merchants
          </h2>
          <div className="mt-8 grid gap-10 md:grid-cols-2">
            <div>
              <h3 className="font-body text-[16px] font-semibold">This is for you if</h3>
              <ul className="mt-4 space-y-3 font-body text-[15px] leading-relaxed text-ink-soft">
                <li>Your product serves merchants who take payments on more than one rail.</li>
                <li>Your users reconcile in a spreadsheet, or ask your support team to.</li>
                <li>You would rather ship a matching engine than build and maintain one.</li>
              </ul>
            </div>
            <div>
              <h3 className="font-body text-[16px] font-semibold">This is not</h3>
              <ul className="mt-4 space-y-3 font-body text-[15px] leading-relaxed text-ink-soft">
                <li>A bookkeeping product. Tools that sync Shopify into QuickBooks already exist and are good.</li>
                <li>A connector library. Fetching from one system at a time is a solved and crowded problem.</li>
                <li>Finished. Five connectors, one primitive, and an honest list of what it does not handle yet.</li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="bg-volt">
        <div className="mx-auto max-w-[920px] px-5 py-20 text-center">
          <h2 className="mx-auto max-w-[680px] font-display text-[38px] leading-[1.08] sm:text-[52px]">
            Tell us what your merchants can't reconcile.
          </h2>
          <p className="mx-auto mt-5 max-w-[520px] font-body text-[16px] leading-relaxed text-ink">
            We are looking for a handful of platforms to build against. If the
            table above looks like a problem your users have, we want the call.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <a href="mailto:info@globaltechstartup.com?subject=Okwan%20—%20reconciliation"
              className="rounded-xl bg-ink px-6 py-3 font-body text-[15px] font-semibold text-surface transition-opacity hover:opacity-85">
              Start a conversation
            </a>
            <a href={GITHUB}
              className="rounded-xl border border-ink px-6 py-3 font-body text-[15px] font-medium text-ink transition-colors hover:bg-ink hover:text-volt">
              Read the source first
            </a>
          </div>
        </div>
      </section>

      <footer className="bg-navy text-[#B8C2D4]">
        <div className="mx-auto flex max-w-[920px] flex-col gap-6 px-5 py-12 font-body text-[14px] sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2 text-surface">
            <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
              <path d="M6 8h20" stroke="#FDFCFA" strokeWidth="3.5" strokeLinecap="round" />
              <path d="M6 24h20" stroke="#FDFCFA" strokeWidth="3.5" strokeLinecap="round" />
              <circle cx="16" cy="16" r="4.5" fill="#FFD400" />
            </svg>
            <span className="font-semibold">okwan</span>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <a className="hover:text-surface" href={GITHUB}>GitHub</a>
            <a className="hover:text-surface" href={`${GITHUB}/blob/main/LICENSE`}>Apache-2.0</a>
            <a className="hover:text-surface" href={`${API}/docs`}>API docs</a>
          </div>
          <p>© 2026 Global Tech Startup LLC</p>
        </div>
      </footer>
    </main>
  );
}
