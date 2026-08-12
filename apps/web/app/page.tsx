const GITHUB = "https://github.com/okwan-ai/okwan";

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
      <span className="font-body text-[22px] font-semibold tracking-tight">
        okwan
      </span>
    </span>
  );
}

/* ── Signature: hub-and-spoke with traveling data dots ───────────── */
function HubDiagram() {
  const sources = [
    { label: "WhatsApp", y: 60 },
    { label: "Postgres", y: 150 },
    { label: "Stripe", y: 240 },
  ];
  const rails = [
    { label: "REST API", y: 60 },
    { label: "SQL tables", y: 150 },
    { label: "MCP server", y: 240 },
  ];
  const inPaths = sources.map(
    (s) => `M 128 ${s.y} C 220 ${s.y}, 260 150, 330 150`
  );
  const outPaths = rails.map(
    (r) => `M 430 150 C 500 150, 540 ${r.y}, 632 ${r.y}`
  );

  return (
    <div className="relative mx-auto max-w-[760px] overflow-hidden rounded-2xl border border-line bg-surface px-2 py-6 sm:px-6">
      <svg viewBox="0 0 760 300" className="w-full" role="img" aria-label="Sources flow into the Okwan hub and out as REST, SQL, and MCP interfaces">
        {/* soft flow stream underlay */}
        <path d="M 128 150 H 632" stroke="#B9C6F2" strokeWidth="26" strokeLinecap="round" opacity="0.25" />

        {[...inPaths, ...outPaths].map((d, i) => (
          <path key={i} d={d} fill="none" stroke="#C9C4B8" strokeWidth="1.5" />
        ))}

        {/* arrowheads into hub and out of rails */}
        {sources.map((s, i) => (
          <polygon key={`ai${i}`} points="326,146 336,150 326,154" fill="#C9C4B8" transform="rotate(0)" />
        ))}
        {rails.map((r, i) => (
          <polygon key={`ao${i}`} points={`624,${r.y - 4} 634,${r.y} 624,${r.y + 4}`} fill="#C9C4B8" />
        ))}

        {/* source nodes */}
        {sources.map((s) => (
          <g key={s.label}>
            <rect x="24" y={s.y - 22} width="104" height="44" rx="12" fill="#FDFCFA" stroke="#E2DED4" />
            <text x="76" y={s.y + 5} textAnchor="middle" fontFamily="Poppins, sans-serif" fontSize="14" fill="#111111">
              {s.label}
            </text>
          </g>
        ))}

        {/* volt hub */}
        <g>
          <rect x="330" y="102" width="100" height="96" rx="20" fill="#FFD400" stroke="#111111" strokeWidth="2" />
          <path d="M 355 134 h 50" stroke="#111111" strokeWidth="5" strokeLinecap="round" />
          <path d="M 355 166 h 50" stroke="#111111" strokeWidth="5" strokeLinecap="round" />
          <circle cx="380" cy="150" r="9" fill="#FDFCFA" stroke="#111111" strokeWidth="3.5" />
        </g>

        {/* interface rails */}
        {rails.map((r) => (
          <g key={r.label}>
            <rect x="632" y={r.y - 22} width="108" height="44" rx="12" fill="#0D1B2E" />
            <text x="686" y={r.y + 5} textAnchor="middle" fontFamily="JetBrains Mono, monospace" fontSize="12.5" fill="#FDFCFA">
              {r.label}
            </text>
          </g>
        ))}
      </svg>

      {/* traveling data dots (CSS offset-path over the SVG geometry) */}
      {[...inPaths, ...outPaths].map((d, i) => (
        <div
          key={`dot${i}`}
          className="dot pointer-events-none absolute h-[7px] w-[7px] rounded-full bg-ink"
          style={{
            left: 0,
            top: 0,
            offsetPath: `path("${d}")`,
            animationDelay: `${(i % 3) * 1.2 + (i > 2 ? 0.6 : 0)}s`,
          }}
        />
      ))}

      <p className="mt-3 text-center font-body text-sm text-ink-soft">
        One connector definition in — three interfaces out. Nothing hand-written in between.
      </p>
    </div>
  );
}

/* ── Small building blocks ───────────────────────────────────────── */
function VoltButton({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      className="inline-block rounded-xl bg-volt px-6 py-3 font-body text-[15px] font-semibold text-ink transition-colors hover:bg-volt-deep focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
    >
      {children}
    </a>
  );
}

function GhostButton({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      className="inline-block rounded-xl border border-ink px-6 py-3 font-body text-[15px] font-medium text-ink transition-colors hover:bg-ink hover:text-surface focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
    >
      {children}
    </a>
  );
}

const connectorCode = `whatsapp = register(Connector(
    name="whatsapp",
    auth=BearerTokenAuth(),
    rate_limit=RateLimitProfile(rps=20),
))

messages = whatsapp.resource("messages", schema=Message)

@messages.operation(OpType.CREATE,
    input_model=SendTextIn, output_model=Message)
async def send_text(ctx, params):
    data = await ctx.client.post(
        f"/{params.phone_number_id}/messages", json=...)
    return Message(message_id=data["messages"][0]["id"])`;

/* ── Page ────────────────────────────────────────────────────────── */
export default function Home() {
  return (
    <main>
      {/* nav */}
      <header className="mx-auto flex max-w-[920px] items-center justify-between px-5 py-6">
        <a href="#" aria-label="Okwan home">
          <Wordmark />
        </a>
        <nav className="flex items-center gap-6 font-body text-[15px]">
          <a className="hidden text-ink-soft hover:text-ink sm:inline" href="#interfaces">Product</a>
          <a className="hidden text-ink-soft hover:text-ink sm:inline" href="#connectors">Connectors</a>
          <a className="hidden text-ink-soft hover:text-ink sm:inline" href="#quickstart">Docs</a>
          <a
            href={GITHUB}
            className="rounded-lg bg-volt px-4 py-2 font-semibold text-ink transition-colors hover:bg-volt-deep"
          >
            GitHub
          </a>
        </nav>
      </header>

      {/* hero */}
      <section className="mx-auto max-w-[920px] px-5 pb-16 pt-10 text-center sm:pt-16">
        <p className="mb-5 font-mono text-[13px] tracking-wide text-ink-soft">
          Open-source · Python SDK · Apache-2.0
        </p>
        <h1 className="mx-auto max-w-[780px] font-display text-[44px] font-normal leading-[1.05] tracking-tight sm:text-[72px]">
          The data connectivity layer built for{" "}
          <span className="whitespace-nowrap">AI agents<span className="text-volt-deep">.</span></span>
        </h1>
        <p className="mx-auto mt-6 max-w-[620px] font-body text-[17px] leading-relaxed text-ink-soft">
          Your business data lives in hundreds of apps, databases, and APIs.
          Okwan is the path between them and your agents: define a connector
          once, and REST endpoints, SQL tables, and MCP servers are generated
          from that single definition.
        </p>
        <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
          <VoltButton href="#quickstart">Get started</VoltButton>
          <GhostButton href={GITHUB}>View on GitHub</GhostButton>
        </div>
      </section>

      {/* signature diagram */}
      <section className="mx-auto max-w-[920px] px-5 pb-24">
        <HubDiagram />
      </section>

      {/* one definition, three interfaces */}
      <section id="interfaces" className="border-y border-line bg-surface">
        <div className="mx-auto max-w-[920px] px-5 py-20">
          <h2 className="font-display text-[34px] leading-tight sm:text-[44px]">
            One definition. Three interfaces.
          </h2>
          <p className="mt-4 max-w-[560px] font-body text-[16px] leading-relaxed text-ink-soft">
            A connector is resources, operations, auth, and rate limits —
            written once in the Python SDK. Everything an app or agent touches
            is generated from it, so the interfaces can never drift apart.
          </p>

          <div className="mt-12 grid gap-6 lg:grid-cols-[1.15fr_1fr]">
            <pre className="overflow-x-auto rounded-2xl bg-navy p-6 font-mono text-[12.5px] leading-relaxed text-[#E8EDF7]">
              <code>{connectorCode}</code>
            </pre>

            <div className="flex flex-col gap-4">
              {[
                {
                  k: "REST",
                  title: "Typed REST endpoints",
                  body: "Every operation mounts as a route with request validation and OpenAPI docs. POST /v1/whatsapp/messages/send_text exists because the definition does.",
                },
                {
                  k: "MCP",
                  title: "MCP servers for agents",
                  body: "Each connector serves its operations as MCP tools with schemas and read-only/write annotations, so agent runtimes can gate writes.",
                },
                {
                  k: "SQL",
                  title: "SQL-queryable tables",
                  body: "Resource schemas project into a federated SQL layer — query live systems side by side, no pipelines. Shipping in the platform release.",
                  soon: true,
                },
              ].map((c) => (
                <div key={c.k} className="rounded-2xl border border-line bg-canvas p-5">
                  <div className="flex items-center gap-3">
                    <span className="rounded-md bg-volt px-2 py-0.5 font-mono text-[12px] font-medium">
                      {c.k}
                    </span>
                    <h3 className="font-body text-[16px] font-semibold">
                      {c.title}
                    </h3>
                    {c.soon && (
                      <span className="ml-auto rounded-md border border-line px-2 py-0.5 font-mono text-[11px] text-ink-soft">
                        soon
                      </span>
                    )}
                  </div>
                  <p className="mt-2 font-body text-[14px] leading-relaxed text-ink-soft">
                    {c.body}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* connectors */}
      <section id="connectors" className="mx-auto max-w-[920px] px-5 py-20">
        <h2 className="font-display text-[34px] leading-tight sm:text-[44px]">
          Production-grade connectors
        </h2>
        <p className="mt-4 max-w-[560px] font-body text-[16px] leading-relaxed text-ink-soft">
          Auth adapters, token-bucket rate limiting, Retry-After-aware
          backoff, and typed errors are handled by the SDK — connector code is
          pure business logic.
        </p>

        <div className="mt-10 grid grid-cols-2 gap-3 sm:grid-cols-3">
          {[
            { name: "WhatsApp Cloud API", live: true },
            { name: "PostgreSQL / Neon", live: true },
            { name: "Stripe", live: true },
            { name: "Shopify" },
            { name: "Google Sheets" },
            { name: "Notion" },
            { name: "Airtable" },
            { name: "HubSpot" },
            { name: "Slack" },
            { name: "Paystack" },
            { name: "MTN MoMo" },
            { name: "Salesforce" },
          ].map((c) => (
            <div
              key={c.name}
              className={`rounded-xl border p-4 font-body text-[14px] ${
                c.live
                  ? "border-ink bg-surface font-medium"
                  : "border-line bg-transparent text-ink-soft"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span>{c.name}</span>
                {c.live ? (
                  <span className="rounded-md bg-volt px-1.5 py-0.5 font-mono text-[10.5px] font-medium">
                    live
                  </span>
                ) : (
                  <span className="font-mono text-[10.5px]">planned</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* quickstart */}
      <section id="quickstart" className="border-y border-line bg-surface">
        <div className="mx-auto max-w-[920px] px-5 py-20">
          <h2 className="font-display text-[34px] leading-tight sm:text-[44px]">
            Running in three commands
          </h2>
          <div className="mt-10 grid gap-6 md:grid-cols-3">
            {[
              {
                step: "Install",
                cmd: "git clone github.com/okwan-ai/okwan\ncd okwan && pip install -e .",
                note: "Python 3.12+. The SDK, all connectors, and both generators come with it.",
              },
              {
                step: "Serve the REST API",
                cmd: "uvicorn okwan_api.main:app",
                note: "Every registered connector mounts automatically. OpenAPI docs at /docs.",
              },
              {
                step: "Give it to an agent",
                cmd: "python -m okwan_mcp postgres",
                note: "An MCP server over stdio. Point Claude Desktop at it and ask about your data.",
              },
            ].map((s, i) => (
              <div key={s.step} className="rounded-2xl border border-line bg-canvas p-6">
                <p className="font-mono text-[12px] text-ink-soft">step {i + 1}</p>
                <h3 className="mt-1 font-body text-[17px] font-semibold">{s.step}</h3>
                <pre className="mt-4 overflow-x-auto rounded-lg bg-navy p-4 font-mono text-[12px] leading-relaxed text-[#E8EDF7]">
                  <code>{s.cmd}</code>
                </pre>
                <p className="mt-3 font-body text-[13.5px] leading-relaxed text-ink-soft">
                  {s.note}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA band */}
      <section className="bg-volt">
        <div className="mx-auto max-w-[920px] px-5 py-20 text-center">
          <h2 className="mx-auto max-w-[640px] font-display text-[38px] leading-[1.08] sm:text-[56px]">
            Give your agents a path to your data.
          </h2>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <a
              href={GITHUB}
              className="rounded-xl bg-ink px-6 py-3 font-body text-[15px] font-semibold text-surface transition-opacity hover:opacity-85"
            >
              Star okwan on GitHub
            </a>
            <a
              href="#quickstart"
              className="rounded-xl border border-ink px-6 py-3 font-body text-[15px] font-medium text-ink transition-colors hover:bg-ink hover:text-volt"
            >
              Read the quickstart
            </a>
          </div>
        </div>
      </section>

      {/* footer */}
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
            <a className="hover:text-surface" href={`${GITHUB}#readme`}>Docs</a>
          </div>
          <p>© 2026 Global Tech Startup LLC</p>
        </div>
      </footer>
    </main>
  );
}
