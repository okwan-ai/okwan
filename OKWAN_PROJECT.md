# OKWAN AI — Master Project File
**Version 1.8 · August 31, 2026 · Owner: Felix, Co-Founder/CTO — Global Tech Startup LLC (US)**

> Single source of truth for the Okwan AI platform. Every strategic, technical, brand, and immigration-related decision lives here. Update the version and changelog with every major decision.

---

## 1. IDENTITY

**Name:** Okwan AI *(Okwan — Twi for "path/way"; pron. "OH-kwahn")*
**Brand story:** Named for the Twi word for path — the path between AI agents and business data. Ghanaian-founder story doubles as press hook and NIW-narrative texture.
**Legal entity:** Global Tech Startup LLC (US, Meta business-verified; Shopify Partner organization)
**Live:** API `https://okwan.onrender.com` · hosted MCP `/mcp/` · site `https://okwan.vercel.app`
**One-liner:** Reconciliation as an API. Define a match once; get an MCP tool, a SQL view, and a REST endpoint.
**Wedge:** Cross-rail payment reconciliation — the query across systems that disagree.
**Positioning statement:** CData was built so BI tools could reach data. Okwan is built so AI agents can — starting where nobody else looks: reconciling money across payment rails that don't talk to each other.
**Category:** AI-native data connectivity / integration platform (connectors, federation, reconciliation, MCP)
**Business model:** SaaS subscriptions + OEM/ISV embed licensing + usage-based Agent API

**Elevator pitch:**
Merchants collect money across half a dozen rails, and none of them agree with the merchant's own order ledger. Reconciling them is manual, monthly, and error-prone. Okwan is the connectivity and federation layer underneath: production-grade connectors to every rail, each automatically exposed as an MCP server and a SQL table, plus a declarative reconciliation primitive that matches records across rails and reports exactly what doesn't line up. Define a connector or a reconciliation once; agents, SQL, and REST all get it.

**Target ICP:** B2B SaaS and ISVs — not merchants. Sell the primitive to the platform that serves 500 merchants, not to the 500 merchants.

**Market note.** Merchant-facing reconciliation is crowded — A2X, Link My Books, Synder, PayTraqer, Finaloop, Webgility, Bookkeep, Acodei and others all do Shopify/Stripe → QuickBooks. Every one of them is a *product*, not a *primitive*: none sells an embeddable reconciliation engine, none is agent-native, and none has declarative rules an ISV can version and ship. That gap is the wedge, and it holds in both the US and African markets.

---

## 2. BRAND & DESIGN SYSTEM
*(Adapted from the CData Sync design language — cream canvas, electric yellow, serif display type)*

**Benchmark:** CData's marketing pages. The reference is how a serious infrastructure company presents itself — the site must not drift into feeling like a demo toy.

### 2.1 Color tokens
| Token | Hex | Usage |
|---|---|---|
| `--canvas` | `#F4F1EB` | Page background (warm cream) |
| `--surface` | `#FDFCFA` | Cards, panels |
| `--ink` | `#111111` | Primary text, logo |
| `--ink-soft` | `#4A4A45` | Body text |
| `--volt` | `#FFD400` | Primary accent — CTAs, highlights, brand mark |
| `--volt-deep` | `#E8B800` | Hover states |
| `--navy` | `#0D1B2E` | Footer, dark sections, secondary buttons |
| `--sky` | `#B9C6F2` | Diagram flows, soft illustration fills |
| `--line` | `#E2DED4` | Borders, dividers |

### 2.2 Typography
- **Display / headlines:** **Fraunces** at 300–600, tight leading, sentence case. Alternative: Playfair Display.
- **Body / UI:** **Poppins** at 400/500/600. Alternative: DM Sans.
- **Code / data:** JetBrains Mono.
- Headline scale: hero 64–88px, section 40–48px, card 20–24px.

### 2.3 Layout language
- Generous whitespace on cream; content max-width ~920px
- Rounded-corner cards (12–16px radius) with subtle borders, no heavy shadows
- Hub-and-spoke connector diagrams; flow streams in `--sky`
- Yellow full-bleed CTA bands with oversized serif headlines
- Dark navy footer with muted link columns
- Buttons: volt fill + black text (primary), outlined black on cream (secondary)

### 2.4 Site signature (v1.7)
The landing page's hero is **a live reconciliation result**, not a connector diagram. Seven records, five verdicts, real numbers from a live run. Rows settle one at a time so the reader watches reconciliation happen rather than reading a screenshot of it. `prefers-reduced-motion` renders the table complete.

Reasoning: a hub-and-spoke diagram is the picture every connector library has, and §8 records that axis as commoditized. The verdicts are the only picture a competitor cannot copy, because none of them has the primitive.

**Rule:** We adopt the design *language* — never CData's logo, brand name, mark, or copied copy. All copy and assets original.

---

## 3. PRODUCT ARCHITECTURE

```
┌──────────────────────────────────────────────────┐
│  L4  AGENT LAYER — MCP servers (per connector,   │
│      stdio; one hosted multi-tenant at /mcp/),   │
│      REST gateway, API-key auth, usage metering  │
├──────────────────────────────────────────────────┤
│  L3  RECONCILIATION — declarative cross-system   │
│      matching; exact-ref + fuzzy rules, currency │
│      minor-unit correctness, identity guards,    │
│      ambiguity, explained vs unexplained breaks  │
├──────────────────────────────────────────────────┤
│  L2  QUERY — federated SQL over live connectors  │
│      via DuckDB; catalog derived from schemas    │
├──────────────────────────────────────────────────┤
│  L1  CONNECTOR ENGINE — auth adapters, rate      │
│      limiting, retries, schema normalization,    │
│      connector SDK (Python/Pydantic)             │
└──────────────────────────────────────────────────┘
      ↑ credentials resolved per tenant from the vault
```

**The differentiator — the one-definition rule.** A connector defined once produces REST endpoints, SQL tables, and MCP tools. A reconciliation defined once produces an MCP tool, a DuckDB view, and a REST route. Four artifact types from two kinds of definition, with no per-item code.

**The rule has generalized three times.** Reconciliation was the second artifact type; the SQL catalog was the third; tenancy was the fourth. Nobody wrote the catalog — `Resource.schema` was already the source of truth for REST response shapes, so it became the column definition too. Nobody rewrote the reconciliation layer for multi-tenancy either — `CredentialResolver` was already a seam, so the vault slotted in and no call site changed.

### 3.1 Connector SDK core abstractions
- `Connector` — metadata, auth spec, rate-limit profile, optional `context_factory` transport seam
- `Resource` — an entity with a Pydantic schema
- `Operation` — list / get / search / create / update, with `is_read_only` derived from op type
- `AuthAdapter` — OAuth2, API key, HMAC, JWT, connection-string. `ApiKeyAuth` binds `required_fields[0]`, so a connector needing more than one field writes its own adapter (see Shopify)
- `CursorPage[T]` — the single paging contract across every connector
- `okwan_core.currency` — `ZERO_DECIMAL_CURRENCIES`, `to_major`/`to_minor`
- `CredentialResolver` — the seam that made tenancy possible without touching any call site

**Money convention.** Integer minor units everywhere, so two rails are the same kind of number. Decimal-string sources convert with `Decimal`, never float — `299.10 * 100` is `29909.999…` in binary floating point, and money that rounds the wrong way is the bug class reconciliation exists to catch. `*_major` computed fields become SQL columns for free.

### 3.2 Reconciliation abstractions
- `Reconciliation` — name, two `ResourceRef`s, ordered match rules, comparison amount, explanations, optional identity guard
- `ExactRef` / `Fuzzy` — deterministic reference join, then amount + currency inside a window; both take per-side paths
- `AmountRef` — the figure to compare once a pair exists. Matching and comparing are different questions
- `Explains` — a field whose value accounts for a discrepancy of the same size and sign. Reclassifies, does not eliminate. Matches on *magnitude*: rails state adjustments with their own sign convention — Shopify a refund positive, PayPal a fee negative — and both describe the same size of difference
- `MSISDN` — phone identity guard with three-valued agreement
- `validate_against_registry()` — rejects any declaration pointing at a write operation. Read-only is structural
- `AcrossRails` — one order ledger against every declaration sharing its left side, folded to one verdict per order: collected_twice · split_tender · collected_inconsistent · collected · unverifiable · uncollected. The ledger is fetched once, so every member judges the same order rows. Compares each rail's gross take against the order total (not what the ledger received, since a ledger sees only its own checkout), within an integer basis-point tolerance. A match is positive evidence; "uncollected" requires every rail to have read the order. Same one-definition rule: MCP tool, REST route and DuckDB view

**Seven outcomes, not two.** agrees · differs with a known cause · differs unexplained · ambiguous · unmatched-left · unmatched-right · unverifiable. Every distinction came from pointing the engine at real data. `net_unexplained_minor` is the figure a merchant acts on.

**Unverifiable is not unmatched.** Each side reports what it read: its span, whether the record cap cut it short, and the ledger horizon a rail clamped to. A record whose counterpart side could not have read its match (the cap truncated that side, or its span does not reach the record's date) is `unverifiable_left`/`_right` with the reason attached, never unmatched. "No payment was read" is not "no payment exists". While any record is unverifiable, `match_rate` is null: the true denominator is unknown, and a rate over whatever happened to be read (1.0 from a three-record read) is worse than no figure. The counts remain. A truncated side makes every opposite-side miss unverifiable, because not every rail guarantees order within a page.

**Ambiguity over arbitration.** Two candidates with equal amount and currency inside the window report unresolved carrying their alternatives, and are not consumed. A match the system is not entitled to is worse than no match.

**Known limit.** `validate_against_registry()` proves the connector and operation resolve and are read-only. A raw SQL string inside `ResourceRef.params` is opaque to it, so a declaration can outlive the schema it queries. Structural read-only holds; structural schema-validity does not.

### 3.3 Query layer (L2)
- Catalog derived from `Resource.schema` in serialization mode — 12 tables, fully typed
- Lazy per-query fetch: only tables the SQL names are pulled, once per session. Federation, not ETL
- Containers whose shape is only known at call time (Postgres `RowSet`) excluded; `declare_sql_table` covers named queries
- **Reachability.** Tables are marked queryable or not, naming missing credential fields. An agent that cannot distinguish configured from unconfigured learns by failing, and the rational response is to stop trusting the tool — which is exactly what happened in the first hosted run
- **Statement guard.** The only tool taking agent-authored input, so read-only is enforced rather than derived. Rejects writes, `COPY`, `INSTALL`, `ATTACH`, `read_csv`, `glob`, chaining and comment-hidden statements

### 3.4 Vault, tenancy and billing
- **Envelope encryption:** each credential sealed with its own AES-GCM data key; the data key wrapped by a master behind a provider interface (env var for dev, cloud KMS for production)
- **AAD** binds each ciphertext to its tenant, connector and field. A row moved between tenants fails to decrypt
- **API keys** shown once, stored as SHA-256 hash plus public prefix, indexed on a partial index over active keys
- **Vault in its own database**, not a schema: `postgres.sql.query` runs caller-supplied SQL against whatever DSN it receives
- **Hierarchical tenants.** One account model: a solo developer is a tenant with no parent, an ISV is a tenant whose merchants are children. The boundary is one function — `may_administer` — so sibling isolation is not a special case to remember; a sibling is simply not on the target's ancestor chain
- **Out-of-subtree access returns 404, not 403.** A 403 confirms the tenant exists, which turns the admin API into an enumeration oracle for other customers' tenant ids
- **Metering** counts per request, attributed to the calling tenant, billed to the root. Hourly buckets, not a row per call. `402` on exhaustion — a plan needs upgrading, not waiting. Metering never fails the request it counts
- **Root tenants come from the CLI.** Signing up an ISV is a commercial act, and an endpoint that mints root accounts is attack surface with no user until self-serve billing exists

### 3.5 Connector set
**Shipped (6):** WhatsApp Cloud API · PostgreSQL/Neon · Stripe · Paystack · Shopify · PayPal
**P1 remaining:** deferred — see §10. The original list (Sheets, Notion, Airtable, HubSpot, Slack) predates the pivot and is a generic connector-library list.

**Connector criterion.** A payment source qualifies as a reconciliation source only if it exposes a *list* operation over transactions. Push-and-poll APIs do not: if you can only look up transactions by IDs you already stored, you are reconciling your own database against itself.

**A ledger states its own read bound.** Shopify without `read_all_orders` returns only the last 60 days of orders and is silent about the rest. The order list requests the app's access scopes in the same GraphQL call and, when the scope is absent or unreadable, reports `span_start` 60 days back. A payment whose order lies past that is then unverifiable rather than a false orphan.

**Pagination is not one shape.** Stripe pages by cursor, Paystack by page number, PayPal inside a bounded date window — upstream caps a Transaction Search query at 31 days and pages by number within it, so the cursor encodes *(window_start, page)* and rolls forward when a window is exhausted. Every rail still hands callers the same `CursorPage`; the SDK contract is what makes the difference invisible.

**Ledgers carry rows that are not sales.** PayPal's ledger holds payouts, fees, reversals and balance adjustments alongside captures, and the event code alone does not separate them — `T0001` (payout send) and `T0006` (capture) share a family. Direction settles it. A rail connector that cannot say which rows are customer payments hands the reconciliation layer noise it will report as permanent breaks.

---

## 4. TECH STACK (locked)

| Layer | Choice | Notes |
|---|---|---|
| Connector engine | Python 3.12, httpx async, Pydantic v2, Tenacity | |
| Reconciliation | Pure-Python match engine over rows; no transport coupling | |
| API gateway | FastAPI + Uvicorn, API-key auth, per-tenant credentials | |
| Query federation | DuckDB, lazy per-query materialisation | |
| Vault | AES-GCM envelope encryption, `cryptography`; Neon Postgres | |
| DB | Neon (`neondb` demo, `okwan_vault` secrets) + asyncpg | |
| MCP | mcp 2.0 — stdio per connector, streamable HTTP hosted at `/mcp/` | |
| Site | Next.js 15 + TypeScript + Tailwind 4, static export → Vercel | Brand system §2 |
| Hosting | **Render** — Docker, Oregon, starter $7/mo | Railway/Fly free tiers ended |
| Billing | Metering + plan gates shipped; no card charged yet | |
| Test | pytest + pytest-asyncio + ruff | 179 tests as of 2026-08-31 |
| Compliance | Audit logging from day 1; SOC 2 prep at ~$500K ARR | |

**⚠ `okwan_core.currency.to_minor` converts through `float`**, which §3.1 forbids for decimal-string money. Shopify and PayPal both route around it with their own `money_to_minor`. Promote one into core and delete the float version, before a third connector writes a fourth copy.

**⚠ `MAX_RECORDS_PER_WINDOW` is declared but unenforced.** PayPal refuses more than 10,000 records for one date window; a busier account truncates upstream silently. Needs adaptive window splitting.

*Closed in v1.8: the interpreter split (Codespace pinned to 3.12) and the `Store` sync/async split (`badfa03`).*

**Monorepo structure (actual):**
```
okwan/
├── packages/
│   ├── core/okwan_core/            # SDK, auth, client, pagination, currency, registry
│   ├── connectors/{whatsapp,postgres,stripe,paystack,shopify,paypal}/
│   ├── mcp_gen/okwan_mcp/          # per-connector MCP server generation
│   ├── recon/okwan_recon/          # declaration, engine, runner, registry, emitters
│   ├── query/okwan_query/          # catalog, session, guard, rest, mcp, mcp_http
│   └── vault/okwan_vault/          # crypto, keys, store, postgres, authz, usage, cli
├── apps/
│   ├── api/okwan_api/              # gateway + auth + admin
│   └── web/                        # Next.js site
├── tests/
├── Dockerfile · render.yaml
└── pyproject.toml
```

---

## 5. REVENUE MODEL

**Stream 1 — Direct SaaS:** Free (5K requests/mo) → Pro (100K) → Team (1M) → Enterprise (unmetered).
**Stream 2 — OEM/ISV Embed:** Startup $5K/yr → Growth $18K/yr → Enterprise $60K+/yr + per-connector royalty. *Primary long-term revenue engine.*
**Stream 3 — Agent API:** usage-based per MCP tool call, bundled minimums.

**Metering unit: one request.** A federated query touching four connectors is one request even though it costs four upstream calls. That asymmetry is ours to manage, not the customer's to reason about — predictability over precision.

**Billing rolls up to the root.** An ISV's merchants share one allowance and one invoice; the merchants are not customers.

**Gap:** nothing charges a card. Plans are set by CLI. Manual invoicing is fine for the first three ISVs; Stripe subscriptions are a week not yet worth spending.

---

## 6. ROADMAP & MILESTONES

| Phase | Window | Ship | Gate to next phase |
|---|---|---|---|
| **P0 Foundation** | Aug–Oct 2026 | ✅ COMPLETE 2026-08-12 | ✅ agent queries live data via Okwan MCP |
| **P0.5 Wedge** | Aug 2026 | ✅ COMPLETE 2026-08-27: Paystack, reconciliation, Shopify | ✅ GATE PASSED ×2 |
| **P0.75 Platform** | Aug 2026 | ✅ COMPLETE 2026-08-28: query federation, vault, tenancy, billing, deployment | ✅ hosted API serving tenant-scoped queries and reconciliations |
| **P1 Launch** | Nov 2026–Feb 2027 | 10 connectors, dashboard, self-serve signup, HN/dev launch | 200 signups, 10 paid, $2K MRR |
| **P2 Platform** | Mar–Dec 2027 | 25 connectors, Sync engine, Embed SDK | 1st ISV embed deal ($10K+ ACV), $150K ARR |
| **P3 Scale** | 2028 | 50+ connectors, SOC 2, enterprise tier | $500K–1M ARR, 3+ ISV partners |
| **P4 Expand** | 2029–2030 | Agent marketplace, intl connectors, Series A or profitability | $2M+ ARR |

**Constraint:** ~10 focused hrs/week until September competitions clear.

---

## 7. US IMMIGRATION ALIGNMENT (EB-2 NIW)

**Strategy:** Self-petition EB-2 National Interest Waiver, pro se with attorney review before filing. Ghana birth country = no visa backlog. Okwan is the "proposed endeavor."

**Dhanasar prong mapping:**
- **P1 — Substantial merit & national importance:** AI/data infrastructure sits within US Critical & Emerging Technologies priorities; Okwan enables safe, governed enterprise AI adoption. The US-market repositioning argues national benefit directly rather than through an intermediary.
- **P2 — Well positioned:** documented co-founder/CTO role with equity; a deployed, publicly reachable platform under the LLC; Meta business verification; Shopify Partner organization; graduate research record.
- **P3 — Waiver benefit:** founder cannot practically obtain labor certification for his own company. Patrick's US-citizen co-founder declaration is the key asset.

**Governance evidence (Exhibit 19).** Read-only is enforced at four levels, and they are not equivalent:
1. `READ_ONLY` op types in the SDK — derived from the operation, not declared
2. `readOnlyHint`/`destructiveHint` MCP annotations emitted natively from that op type
3. `validate_against_registry()` refusing a reconciliation over a write operation — structural
4. The SQL statement guard — the one place read-only is *enforced* rather than *derived*, because it is the only tool taking agent-authored input

State the limits honestly: the registry check covers operations, not the contents of raw SQL passed as a parameter, and the guard is a denylist over DuckDB's filesystem reach rather than a sandbox.

**Security evidence.** Per-tenant envelope encryption with AAD binding, API keys stored as hashes, credentials never transmitted per request, a vault database outside any connector's reach, and a tested tenant boundary — `may_administer` covers a child reaching its parent, siblings, and cross-ISV access in both directions.

**Demo evidence (Exhibit 22):** four recorded gates. The fourth is the strongest — see below. The third — an agent discovered the schema through `okwan_describe_tables`, wrote its own two-tier reconciliation SQL joining a live Shopify store to a payment ledger, independently derived the exact-reference-then-fuzzy-window strategy, refused ambiguous 1:N pairings to avoid double-counting, and corrected for refunds. It wrote the logic; it did not call a tool that had it.

The fourth (2026-08-31) is the first recorded over the **hosted multi-tenant MCP** with credentials resolved from the vault, which is the §8 moat claim exercised end to end rather than asserted. Asked an open-ended question with no tool named, the agent chose the reconciliation tool, led with the actionable figure rather than the raw discrepancy total, separated processing fees from genuine breaks, diagnosed a short capture as distinct from a fee difference, excluded a non-sale balance adjustment, surfaced an unsettled payout leg, and identified the dataset as sandbox from payer-domain and timestamp evidence — caveating its own conclusion rather than overstating it. On follow-up it flagged a fixture inconsistency we had dismissed. A model declining to overstate what its tools support is the property worth evidencing.

**Filing trigger:** P1 traction story + flags cleared + letters in hand. Target window late 2026–early 2027. Details in `NIW_WORKING_PACK.md`.

**Open blockers:** amended 1065s for TY2024 + TY2025 (IRS processing lag is the long pole), Operating Agreement + Member Resolution signatures, CV/bio consistency, expert letters not started.

**Entity consistency note.** Public artifacts should sit under the LLC. GitHub org, Shopify Partner org, Neon and MoMo accounts do. **Vercel does not** — the site is on a personal Hobby account. Team is a paid move; worth resolving before assembling the binder, since Exhibit 21 references the site.

**Attorney-required, do not self-draft:** the petition brief, any characterization of the amended-return explanatory statements, the final assembled filing, and any structure involving the Ghana sole proprietorship beyond sandbox API access.

---

## 8. COMPETITIVE FRAME

**"MCP-native connector library" is commoditized** — Nango, Composio, Arcade, and an existing Africa Payments MCP server all occupy it. Competing on connector count is competing on a saturated axis.

**The defensible wedge is the federation and reconciliation layer none of them address.** Connector libraries fetch from one system at a time. Reconciliation is a *query across* systems that disagree.

| Player | Their game | Why they don't cover this |
|---|---|---|
| CData | 350+ connectors, driver-first legacy, MCP retrofitted | No cross-source matching primitive; BI-shaped, not money-shaped |
| Fivetran | ELT pipelines to warehouses | Lands data; leaves reconciliation to the analyst |
| Nango / Composio / Arcade | Agent tool-calling, auth infrastructure | Single-source tool calls; no matching, no currency semantics |
| Merge.dev / unified APIs | Category-specific unified APIs | Normalizes shape, not truth; no cross-rail join |
| A2X / Synder / Link My Books | Merchant recon into QuickBooks/Xero | Products, not primitives — no embeddable engine, no API, rules are UI config |
| Modern Treasury / close-automation | Payment ops for finance teams | Enterprise-shaped, not developer-shaped |
| DIY internal builds | Every ISV's default | Our pricing must beat one engineer-month per rail pair |

**Moats to build:** reconciliation rule library as licensed SKUs · ISV switching costs · correctness details competitors get wrong (zero-decimal currencies, MSISDN ambiguity, minor-unit arithmetic, gross-vs-net on refunds, ambiguous pairing) · **merchant-scoped agent endpoints** — an ISV provisions a tenant per merchant and each gets an MCP endpoint seeing only their own systems. No competitor can offer this: the recon products have no API, and the agent-native connector libraries have no tenancy model.

---

## 9. DECISIONS LOG

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-30 | **Name: Okwan AI** (Twi: "path/way") | "Conduit" blocked: Meroxa owns registered CONDUIT (Reg. 7361060, IC 009). Attorney clearance opinion still required |
| 2026-07-30 | MCP-first architecture; repo public under `okwan-ai`, Apache-2.0 | Compete on the modern battlefield; open core SDK |
| 2026-08-12 | **P0 COMPLETE + DEMO GATE PASSED** (2 months early) | 3 connectors, MCP 2.0 auto-gen, REST gateway, live site |
| 2026-08-14 | Principal office: **Wyoming** (Cheyenne PMB) | Matches Articles/EIN/BOIR; amended 1065s must use this address |
| ~2026-08 | **STRATEGIC PIVOT: cross-rail reconciliation wedge** | Connector libraries commoditized. Reconciliation is the query problem none address |
| 2026-08-27 | **Sprints 6–8: Paystack, reconciliation layer, Shopify** | Connectors #4 and #5. One-definition rule extended beyond connectors |
| 2026-08-27 | MSISDN keeps both readings of a leading `00` | `00` is the international dial prefix, but in Côte d'Ivoire a trunk-prefixed number also starts `00`. `candidates()` returns every reading |
| 2026-08-27 | **CROSS-SYSTEM DEMO GATE PASSED** | Agent diagnosed the discrepancy as a feed gap and flagged an arbitrary pairing unprompted |
| 2026-08-28 | Fuzzy reports ambiguity rather than choosing | Time proximity is a tie-break on the least reliable signal a rail has. Live match rate fell 0.83 → 0.75, which is the correct number |
| 2026-08-28 | Explained vs unexplained discrepancies | A rail feed without refund rows makes every refunded order a permanent break. `net_unexplained_minor` is the actionable figure |
| 2026-08-28 | Fuzzy window default 48h → 7d | Once ambiguity detection landed, wide produces visible ambiguities and narrow produces silent misses. Observed settlement lag ~77h |
| 2026-08-28 | **L2 query federation shipped** | SQL over live connectors via DuckDB. Catalog derived from `Resource.schema` |
| 2026-08-28 | Federated SQL over MCP and REST, with a statement guard | First tool taking agent-authored input, so read-only is enforced rather than derived |
| 2026-08-28 | **MoMo Collections rejected as a reconciliation source** | Every transaction-list path 404s. Collections is push-and-poll. Mobile money reaches reconciliation through aggregators or statement import |
| 2026-08-28 | Table reachability in the catalog | The first hosted run listed `stripe.charges`, the fetch died on a missing key, and the agent hand-wrote SQL instead |
| 2026-08-28 | **Vault: server-side credentials, API-key tenancy** | An ISV transmitting a live Stripe key per request was the blocker on public hosting. `resolver_for` slots into the existing seam |
| 2026-08-28 | Vault in a dedicated Neon database; admin via CLI | `postgres.sql.query` runs caller-supplied SQL against whatever DSN it gets, so secrets must be out of reach |
| 2026-08-28 | **Deployed to Render** at `okwan.onrender.com` | Fly and Railway free tiers ended. One uvicorn worker per instance since DuckDB sessions are per-request and in-memory. Health check 503s when the vault is unreachable |
| 2026-08-28 | **Hosted multi-tenant MCP** at `/mcp/` | Tenant comes from the bearer token via `ctx.headers`, never a tool argument — an agent can forge an argument, not a key. Session manager runs in the gateway lifespan |
| 2026-08-28 | **AGENT-AUTHORED SQL DEMO GATE PASSED** | Agent wrote its own two-tier reconciliation query across two live systems and refused ambiguous pairings |
| 2026-08-28 | **Hierarchical tenancy + ISV provisioning API** | One account model: solo developer = tenant with no parent, ISV = tenant with children. Out-of-subtree returns 404 not 403, since 403 confirms existence. Hosted MCP needed no changes, so merchant-scoped agent endpoints fall out of the model |
| 2026-08-28 | **Per-request metering with subtree rollup** | Counted per request, not per upstream call. Attributed to the caller, billed to the root. Hourly buckets. 402 not 429. Metering never fails the request it counts |
| 2026-08-28 | Reconciliations exposed over hosted MCP | `okwan_list_reconciliations` reports runnability per tenant across both sides; `okwan_reconcile` returns the full six-outcome result with ambiguous candidates |
| 2026-08-28 | Landing page repositioned around the primitive | Hero is a live reconciliation result, not a connector diagram. Adds a "This is not" column — an ISV reading a page that claims everything assumes it is vapour |
| 2026-08-30 | **PayPal shipped as connector #6** | Transaction Search passes the §3.5 criterion, verified live before any code was written. First window-walk pagination: the cursor encodes *(window_start, page)* because upstream caps a query at 31 days and pages by number inside it. The walk clamps to `last_refreshed_datetime` — PayPal's own statement of how current its ledger is — rather than to wall-clock now, since reconciling against rows that have not landed manufactures breaks that resolve themselves. Stripe + PayPal + Shopify is the US merchant stack, which makes the multi-rail wedge concrete in the home market |
| 2026-08-30 | **First per-connector test file** | Five connectors had shipped with no dedicated coverage; the suite exercised them only incidentally through `test_query.py`. `tests/test_paypal.py` sets the pattern — pure-function tests over fixture rows plus a fake transport for the walk, no new mocking dependency. Now the standard for every connector |
| 2026-08-31 | **`shopify_paypal` declaration — the wedge on two live systems** | A sixth connector on its own is connector-count competition on the axis §8 records as commoditized; the declaration is what makes it mean something. Joins `shopify.orders.name ↔ paypal.transactions.invoice_id`, which is what a Shopify-through-PayPal checkout carries. Live: 5 exact-ref matches, 83% match rate, $86.62 of raw discrepancy of which $7.93 is unexplained. #1004 and #1005 are both $450 and matched correctly on reference — reference-before-fuzzy is why there was no ambiguity to resolve |
| 2026-08-31 | **`is_payment` needs direction, not just an event code** | `T0001` (payout send) and `T0006` (capture) share the T0 family, so a prefix test counted money leaving the account as revenue arriving. A payment is a T0-family code carrying a positive amount. Found only by running against a ledger that contained both |
| 2026-08-31 | **`Explains` compared raw value instead of magnitude** | Latent engine bug, not a PayPal quirk: `total_refunded_minor` worked only because Shopify happens to report refunds positive. PayPal reports fees negative, so no fee could ever have explained anything. Now compares magnitudes, with `sign` still constraining direction. Rail fees are declared as a known cause — left unexplained they fill the chase-list with the one discrepancy present on every single row, and a list that always fires is a list nobody reads |
| 2026-08-31 | **#1001's Shopify fixture is a seeding bug, not a scenario** | `total_received_minor` of $2,423.00 equals the store's entire gross. It nets correctly to $299.00 after the refund so the reconciliation passes, but a single order carrying the whole-ledger total is an artifact. The `declarations.py` comment describes it as a deliberate partial-refund case; it should not be used as a reference case until the fixture is corrected. Surfaced by the agent in demo 4, not by us |
| 2026-08-31 | **Hosted surface verified per tenant, not just locally** | `paypal.transactions` reads queryable for a tenant holding vault credentials while every unprovisioned connector names its missing field. The local catalog passing says nothing about this — the Stripe reachability bug lived exactly here |
| 2026-10-02 | **`shopify_stripe` declaration; fuzzy compares gross to gross** | Joins `shopify.orders.name ↔ stripe.charges.order_ref` (lifted from charge metadata). The fallback reads `total_received_minor ↔ amount`, not net: Stripe's net is the charge less its fee, so net-to-net could never fire on a real charge, and gross-to-net pairs a refunded order's remainder with an unrelated charge of the same size. Compares `net_payment_minor ↔ net_minor`; refunds net on both sides, so `fee_minor` is the only declared cause, and a positive Stripe fee explains on magnitude as PayPal's negative one does. Live: 3 exact-ref matches, all fee-explained ($47.27), $0.00 unexplained, 50% match rate, four unreferenced $299.00 charges unmatched (15 days before any order, outside the 7d window). #1002 and #1004 match cleanly on both rails, i.e. collected twice. Neither declaration can see that alone |
| 2026-10-02 | **`shopify_paypal` fuzzy fallback was dead; now gross to gross** | It compared `net_payment_minor ↔ net_minor` with zero tolerance, and PayPal's net is the payment less its fee. Against the live ledger not one of the five payments' net equalled any order's, so the fallback could never fire on a real payment. It went unnoticed because every live payment carries `invoice_id` and pairs on reference. Now `total_received_minor ↔ amount_minor`, matching `shopify_stripe`; gross hits #1002, #1004 and #1005 live. The comparison stays net to net. Live figures unchanged (5 exact-ref, 83%, $86.62 / $7.93) |
| 2026-10-02 | **`stripe.charges` takes a `status` filter; `shopify_stripe` reads succeeded only** | A failed attempt carries the same `order_ref` as the retry that succeeded, and the exact rule takes the first charge holding the reference — so list order alone decided which one paired. Stripe's newest-first order usually favours the retry, which is luck, not a guarantee. Stripe's list has no status parameter, so the connector filters each page after fetching and keeps the cursor on the last charge Stripe returned. A connector input rather than an engine feature, so REST, MCP and SQL get it from the one definition, as PayPal's `status` already does |
| 2026-10-02 | **`Reconciliation.lookback`: one declared span, resolved per run** | PayPal's list defaults to the last 30 days, so by October `shopify_paypal` read no transactions and reported all six orders unpaid, without erroring. `lookback` takes the Fuzzy window grammar; the runner resolves it once per run to `start_date`/`end_date` and offers both to each side. A side whose input lacks them is read as before, a side's own params win over it, and caller overrides win over both. Surfaced in tool metadata. Both rail declarations set `90d`, which covers the live data until ~2026-11-10. Only PayPal accepts the window today: Shopify (`created_at_min`) and Stripe (no date input) are read in full, so on `shopify_stripe` it bounds nothing yet. Live with no overrides: `shopify_paypal` 5 exact-ref, 83%, $86.62 / $7.93; `shopify_stripe` 3 exact-ref, 50%, $47.27 / $0.00, both as recorded above. **Known gap:** the two sides of a declaration can read different spans (Shopify in full, PayPal bounded) and nothing in the result says so |
| 2026-10-02 | **Results state what was read: per-side coverage** | An unmatched order is a finding only if its payment would have been read had it existed. Each side now reports its span, whether the record cap cut it short with more upstream, and the ledger horizon a rail clamped to. The connector is the source: `CursorPage` gained optional `span_start`/`span_end`/`horizon`, which PayPal fills (including its own 30-day default, a bound no caller chose) and unbounded lists leave null. Every unmatched row carries `counterpart_read` and a `caveat`; the summary adds `coverage`, `*_counterpart_unread` counts and `caveats` prose, and the DuckDB view gains both columns, so MCP, REST and SQL carry it from one place. A truncated counterpart voids every unmatched record on the other side, not just those outside the span read, because not every rail guarantees order within a page. `unmatched_*` and `match_rate` keep their meaning, so the figures above are unchanged. Live: no caveats at the default cap; at a 3-record cap both sides report truncated and #1001 is marked not a finding. **Open:** whether a truncated side should withhold unmatched records entirely rather than report them with a caveat |
| 2026-10-02 | **Unverifiable replaces the caveat: seven outcomes** | Supersedes the caveat design above. A flag on an unmatched row works only if the reader checks it, and an agent counting `unmatched_left` still got the wrong number. Records whose counterpart side could not have read them are now their own outcome, `unverifiable_left`/`unverifiable_right`, carrying a `reason`, and they leave `match_rate`'s denominator. `unmatched_*` now means a finding and nothing else. `counterpart_read`, `caveat` and `caveats` are removed; the DuckDB view carries `reason`; REST's status filter accepts both new statuses. Without coverage (a bare `match()` over rows) nothing is unverifiable, which is the behaviour before coverage existed. Live at the default cap both §9 figures are unchanged with zero unverifiable; at a 3-record cap `shopify_stripe` reports #1001 and one charge unverifiable and a match rate of 1.0 over the two orders that could be judged. Shopify app checked: it holds `read_all_orders`, so the ledger side really is read in full for this store |
| 2026-10-02 | **`match_rate` withheld when anything is unverifiable; Shopify states its 60-day bound** | Excluding unverifiable records from the denominator produced 1.0 from a three-record read, a number the data cannot support. `match_rate` is now null while either side has unverifiable records, and the counts carry what is known. Separately, `shopify.orders.list` requests `currentAppInstallation { accessScopes }` in the same GraphQL call (no extra round trip against a 2 rps limit). Without `read_all_orders`, or when the scope list cannot be read, the page reports `span_start` 60 days back (or `created_at_min`, if later), so payments for older orders become unverifiable instead of false orphans. Unreadable is treated as absent because overstating what was read is the failure this prevents. This store holds the scope: live figures unchanged, ledger span unbounded. First dedicated `tests/test_shopify.py` |
| 2026-10-02 | **`reconcile_across_rails`: was each order paid once?** | An order taken on both Stripe and PayPal is a clean, fee-explained match on each rail declaration, so the double collection exists only in a fold across them. `AcrossRails` names member declarations sharing an identical left side and lookback, the ledger's order total, and each rail's gross path. It fetches the ledger once and folds by row identity. Order: two or more matches classify as collected_twice (each within 1% of the total), split_tender (sum within 1%) or collected_inconsistent (reported, not arbitrated); one match is collected; none is unverifiable if any rail could not read the order or was ambiguous, else uncollected. A collected order names any `unverified_rails`, since a second collection there cannot be ruled out. Live, identical over REST, MCP and SQL: #1002 and #1004 collected_twice, $1,449.00 overcollected; #1001/#1005/#1006 PayPal only; #1003 Stripe only; nothing uncollected or unverifiable. **Open:** the MCP and REST `limit` default of 100 is the per-side cap, so any merchant above 100 records gets `match_rate: null` and unverifiable orders by default. The hosted `okwan_reconcile` does not expose folds yet |

---

## 10. IMMEDIATE NEXT ACTIONS

**Revenue path**
1. **Talk to one ISV.** Unmoved for three versions. Everything else is speculation until someone uses it. The site and a working URL now exist as assets for that conversation
2. **Self-serve signup.** Onboarding is CLI commands run by Felix. Fine for the first customers, not for 200 signups
3. **Card charging.** Metering and plan gates work; nothing invoices. Manual is fine for three ISVs

**Build**
4. **Promote a `Decimal`-based `money_to_minor` into core** and delete the float `to_minor`. Two connectors already route around it; a third copy is the point it becomes a divergence rather than a duplication
5. **Enforce `MAX_RECORDS_PER_WINDOW`** with adaptive window splitting. A PayPal account busier than the sandbox truncates silently today
6. **Correct the #1001 Shopify fixture** so a reference case stops carrying the whole ledger's gross
7. Connectors, chosen by the §3.5 criterion rather than the stale P1 list

**Strategic**
8. **Paystack account** — signup needs a business registered in a supported African country. The Ghana sole proprietorship covers sandbox access for testing only; any revenue routing is an attorney/CPA question and must not be improvised
9. Register `okwan.ai`; point it at Render and set `OKWAN_ALLOWED_HOSTS`
10. Attorney: formal OKWAN clearance opinion + intent-to-use application, IC 009 + 042
11. Move the Vercel project under the LLC (§7 entity consistency)

**Corporate / NIW** *(operational detail in NIW_WORKING_PACK.md)*
12. ~~Execute Operating Agreement + Member Resolution~~ — **executed 2026-08-30, both signatures.** Remaining: scan both flat as PDFs for Exhibits 12 and 13
13. Amended 1065s, TY2024 and TY2025 — in progress. Capture: date mailed, CPA/EA review, form revision per year, Schedule B-2 status, service center, certified-mail tracking. Proof of mailing is separate evidence from the return
14. IRS Business account transcripts via +1 267-941-1000. The transcript is the evidence; the call is only diagnostic

---

## 11. SECURITY NOTES

- **2026-08-12 / 08-27 / 08-28 / 08-31:** Credentials transited chat on six occasions — the Neon password (twice), a Stripe test-mode key, the Stitch API key, a Shopify Admin token, a passport number, and two Okwan API keys. A sandbox app's NVP/SOAP password also appeared in a screenshot. Causes: pasted config files, screenshots, one echo where a shell substitution printed a value the command was meant to mask, and a CLI that prints a freshly issued key to stdout. Every exposed API key was revoked and reissued.
- **Standing rule:** credentials never in chat, docs, or screenshots — env vars, Codespaces secrets, and secret managers only. The rule has been breached six times, all in test-mode or sandbox scope. If security practice ever forms part of the evidence record, the record should show the rule being followed, not restated. The recurring cause is not carelessness about the rule but that the tooling makes following it awkward — see the vault CLI note below.
- **What the platform now does right, independent of that:** credentials are never transmitted per request, are sealed with per-credential data keys, are bound by AAD to their tenant, live in a database no connector can reach, and the tenant boundary is covered by tests in both directions.
- **Working pattern:** Codespaces secrets (`OKWAN_*`) scoped to `okwan-ai/okwan`, loaded at container start. Secrets only load on rebuild.
- **Repo hygiene:** the root `.gitignore` did not cover the web app's build output; a commit attempt carried 141 MB before `node_modules`, `.next` and `out` were ignored.
- **Vault CLI is unusable in GitHub Codespaces.** `cred set` uses `getpass`, which opens `/dev/tty` directly — neither pasting nor a herestring reaches it, and the prompt hangs with no error. Tenant provisioning currently requires a local shell or direct `store.put_credential` calls. This is §10 item 2 in sharper form: onboarding is not merely manual, it is environment-dependent.
- **Credential field names are a contract.** `env_credentials` maps `OKWAN_<CONNECTOR>_<FIELD>` to the adapter's `required_fields`. `OKWAN_PAYPAL_SECRET` failed silently against an adapter wanting `client_secret`; the fix belongs at the secret, not in the adapter, or the convention stops being a convention.
- **Claude Desktop cannot invoke `npx` on Windows.** It runs MCP servers through `cmd.exe`, and `npx.cmd` lives at `C:\Program Files\nodejs\` — the unquoted space fails with `'C:\Program' is not recognized`, and one crashing server takes down every other entry in the panel. Fix: `npm install -g mcp-remote` and point `command` at `%APPDATA%\npm\mcp-remote.cmd` directly. Separately, `mcp-remote` **silently drops** a `--header` argument not in exact `Name:Value` form — a space after the colon is enough to break auth, with nothing but a warning line in the logs to show for it.
- **Config note:** Claude Desktop on Windows is an MSIX app. The live config is at `%LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Claude\claude_desktop_config.json`, **not** `%APPDATA%\Claude\`. Brace nesting is the recurring failure. `Set-Content -Encoding UTF8` adds a BOM that breaks the file; use `WriteAllText` with a BOM-less encoder.

---

*Changelog:*
*v1.8 — PayPal shipped as connector #6 (§3.5): Transaction Search over a window walk, the first rail that pages inside a bounded date range rather than along a single sequence, with the walk clamped to PayPal's own statement of ledger currency. `shopify_paypal` declaration (§9): one order ledger against a second rail, live across two systems, 83% match rate with fees explained and $7.93 unexplained. Two engine corrections found only by running against real data — payment classification needs direction, not just an event code, and `Explains` must match on magnitude rather than raw value (§3.2). First per-connector test files, now the standard; 179 tests. Demo 4 recorded over the hosted multi-tenant MCP with vault-resolved credentials (§7, Exhibit 22) — the §8 moat claim exercised rather than asserted. Interpreter split and `Store` sync/async debt closed; the currency float path and the unenforced window-record cap recorded as new debt (§4). Vault CLI tty dependency, the credential field-name contract, and the Claude Desktop `npx` failure added to §11.*
*v1.7 — Hierarchical tenancy and the ISV provisioning API (§3.4): one account model, boundary in one tested function, 404 not 403 out of subtree. Per-request metering with subtree rollup and plan gates (§3.4, §5). Reconciliations exposed over the hosted MCP with per-tenant runnability. Landing page repositioned around the primitive with a live reconciliation as the hero (§2.4). Merchant-scoped agent endpoints added to the moat list (§8) — they fell out of the tenancy model at no cost. Store sync/async split and the Vercel entity inconsistency recorded as debt (§4, §7). Repo hygiene note added (§11).*
*v1.6 — L2 query federation over REST and MCP. Credential vault, API-key tenancy, per-tenant resolution. Deployed to Render. Hosted multi-tenant MCP. Three correctness fixes closed: ambiguity, explained discrepancies, window default. MoMo rejected; connector criterion recorded. US market researched. Third demo gate logged.*
*v1.5 — Shopify shipped as connector #5. `AmountRef` and per-pair discrepancy reporting; `Fuzzy` per-side paths. Second demo gate.*
*v1.4 — Strategic pivot to the cross-rail reconciliation wedge. Reconciliation layer added. Paystack shipped. Competitive frame rewritten.*
*v1.3 — (unlogged in prior versions; superseded)*
*v1.2 — P0 demo gate passed 2026-08-12. Security notes section added.*
*v1.1 — Renamed Conduit AI → Okwan AI after USPTO clearance killed "Conduit".*
*v1.0 — initial master file created from ConnectCore → Conduit rebrand session.*
