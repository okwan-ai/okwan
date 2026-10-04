# OKWAN AI — Master Project File
**Version 1.15 · October 4, 2026 · Owner: Felix, Co-Founder/CTO — Global Tech Startup LLC (US)**

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
- `Operation` — list / get / search / create / update / delete, with `is_read_only` derived from op type
- `AuthAdapter` — core ships API key, bearer token and connection string. OAuth2 client-credentials lives in the PayPal connector and Shopify has its own two-field adapter; HMAC and JWT are not built. `ApiKeyAuth` binds `required_fields[0]`, so a connector needing more than one field writes its own adapter (see Shopify)
- `CursorPage[T]` — the single paging contract across every connector
- `okwan_core.currency` — `ZERO_DECIMAL_CURRENCIES`, `to_major`/`to_minor`
- `CredentialResolver` — the seam that made tenancy possible without touching any call site
- `okwan_core.egress` — resolve, refuse non-public addresses, dial the checked one. For any connector whose credential picks the host (Postgres today)
- `Connector.probe()` — the first list operation runnable with nothing but `limit=1`, derived from the definition. Backs the dashboard's Test and Save with no per-connector code; None for WhatsApp, whose only list needs a `waba_id`

**Money convention.** Integer minor units everywhere, so two rails are the same kind of number. Decimal-string sources convert with `Decimal`, never float — `299.10 * 100` is `29909.999…` in binary floating point, and money that rounds the wrong way is the bug class reconciliation exists to catch. `*_major` computed fields become SQL columns for free.

### 3.2 Reconciliation abstractions
- `Reconciliation` — name, two `ResourceRef`s, ordered match rules, comparison amount, explanations, optional identity guard
- `ExactRef` / `Fuzzy` — deterministic reference join, then amount + currency inside a window; both take per-side paths
- `AmountRef` — the figure to compare once a pair exists. Matching and comparing are different questions
- `Explains` — a field whose value accounts for a discrepancy of the same size and sign. Reclassifies, does not eliminate. Matches on *magnitude*: rails state adjustments with their own sign convention — Shopify a refund positive, PayPal a fee negative — and both describe the same size of difference
- `MSISDN` — phone identity guard with three-valued agreement
- `validate_against_registry()` — rejects any declaration pointing at a write operation. Read-only is structural
- `AcrossRails` — one order ledger against every declaration sharing its left side, folded to one verdict per order: collected_twice · split_tender · collected_inconsistent · collected · unverifiable · uncollected. The ledger is fetched once, so every member judges the same order rows. Compares each rail's gross take against the order total (not what the ledger received, since a ledger sees only its own checkout), within an integer basis-point tolerance. A match is positive evidence; "uncollected" requires every rail to have read the order. Same one-definition rule: MCP tool, REST route and DuckDB view

**Seven outcomes, not two.** agrees · differs with a known cause · differs unexplained · ambiguous · unmatched-left · unmatched-right · unverifiable (eight statuses in `engine.STATUSES`, since unverifiable carries a side). Every distinction came from pointing the engine at real data. `net_unexplained_minor` is the figure a merchant acts on.

**Unverifiable is not unmatched.** Each side reports what it read: its span, whether the record cap cut it short, and the ledger horizon a rail clamped to. A record whose counterpart side could not have read its match (the cap truncated that side, or its span does not reach the record's date) is `unverifiable_left`/`_right` with the reason attached, never unmatched. "No payment was read" is not "no payment exists". While any record is unverifiable, `match_rate` is null: the true denominator is unknown, and a rate over whatever happened to be read (1.0 from a three-record read) is worse than no figure. The counts remain. A truncated side makes every opposite-side miss unverifiable, because not every rail guarantees order within a page.

**Ambiguity over arbitration.** Two candidates with equal amount and currency inside the window report unresolved carrying their alternatives, and are not consumed. A match the system is not entitled to is worse than no match.

**Known limit.** `validate_against_registry()` proves the connector and operation resolve and are read-only. A raw SQL string inside `ResourceRef.params` is opaque to it, so a declaration can outlive the schema it queries. Structural read-only holds; structural schema-validity does not.

### 3.3 Query layer (L2)
- Catalog derived from `Resource.schema` in serialization mode — 13 tables, fully typed (12 derived, plus the declared `rail.payments`)
- Lazy per-query fetch: only tables the SQL names are pulled, once per session. Federation, not ETL
- Containers whose shape is only known at call time (Postgres `RowSet`) excluded; `declare_sql_table` covers named queries
- **Reachability.** Tables are marked queryable or not, naming missing credential fields. An agent that cannot distinguish configured from unconfigured learns by failing, and the rational response is to stop trusting the tool — which is exactly what happened in the first hosted run
- **Statement guard.** The only tool taking agent-authored input, so read-only is enforced rather than derived. Rejects writes, `COPY`, `INSTALL`, `ATTACH`, `read_csv`, `glob`, chaining and comment-hidden statements

### 3.4 Vault, tenancy and billing
- **Envelope encryption:** each credential sealed with its own AES-GCM data key; the data key wrapped by a master behind a provider interface. Production runs the env-var master today; `KmsMasterKey` is written and wins when `OKWAN_VAULT_KMS_KEY` is set, but `google-cloud-kms` is not a dependency, so it cannot run as deployed
- **AAD** binds each ciphertext to its tenant, connector and field. A row moved between tenants fails to decrypt
- **API keys** shown once, stored as SHA-256 hash plus public prefix, indexed on a partial index over active keys
- **Vault in its own database**, not a schema: `postgres.sql.query` runs caller-supplied SQL against whatever DSN it receives
- **Hierarchical tenants.** One account model: a solo developer is a tenant with no parent, an ISV is a tenant whose merchants are children. The boundary is one function — `may_administer` — so sibling isolation is not a special case to remember; a sibling is simply not on the target's ancestor chain
- **Out-of-subtree access returns 404, not 403.** A 403 confirms the tenant exists, which turns the admin API into an enumeration oracle for other customers' tenant ids
- **Metering** counts per request, attributed to the calling tenant, billed to the root. Hourly buckets, not a row per call. `402` on exhaustion — a plan needs upgrading, not waiting. Metering never fails the request it counts. **What counts:** every call that reads upstream, on every surface — connector REST, SQL and reconciliation REST, and the hosted MCP's `okwan_query` and `okwan_reconcile` — is quota-gated and metered once on success. Listings (`/v1/query/tables`, `/v1/reconciliations`, `okwan_describe_tables`, `okwan_list_reconciliations`) are free. The dashboard's reconciliation runs (`dashboard:across`, `dashboard:reconcile`, §9 2026-10-03) are gated the same way and attributed to the merchant they run as, not the signed-in ISV. The credential test is metered and not gated
- **Root tenants come from self-serve signup, gated on a verified email** (reversed 2026-10-02; previously CLI-only). One tenant per address. The token completes only with the password that started the signup, so pre-registering someone's address yields nothing. Signup and sign-in answer identically for registered and unknown addresses, the same reasoning as 404-not-403
- **Dashboard sessions are not API keys.** `oks_` sessions administer a tenant (credentials, keys, tests) but cannot read a rail; data routes and the hosted MCP still take an `okw_` key the customer chose to issue
- **The meter is readable by a session.** `GET /v1/tenants/{id}/usage` (§9 2026-10-04) returns the billing root's plan and month-to-date figure and the target subtree's buckets per surface, hourly or per day; it is never metered. The dashboard's plan figures come from it

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
| Dashboard | Next.js 15 server routes → Render (`apps/dashboard`) | Built 2026-10-02, not yet deployed |
| Hosting | **Render** — API on Docker, dashboard on Node, Oregon, starter $7/mo each | Railway/Fly free tiers ended. The dashboard service is declared in `render.yaml` |
| Billing | Metering + plan gates shipped; no card charged yet | |
| Test | pytest + pytest-asyncio + ruff | 452 tests as of 2026-10-02. Ruff is configured but not a clean gate: 25 findings, 12 of them FastAPI's `Depends` idiom (B008) |
| Compliance | Audit logging from day 1; SOC 2 prep at ~$500K ARR | |

**⚠ `okwan_core.currency.to_minor` converts through `float`**, which §3.1 forbids for decimal-string money. Shopify and PayPal both route around it with their own `money_to_minor`. Promote one into core and delete the float version, before a third connector writes a fourth copy. **The engine uses it too:** `Fuzzy` passes both sides' amounts through `to_minor`, but every declaration points `Fuzzy` at fields already in minor units, so each amount is scaled by 100 a second time. It is harmless at today's tolerance of 0, since integers stay exact in a float. Any nonzero `amount_tolerance_minor` would be 100× tighter than its name says.

**⚠ `MAX_RECORDS_PER_WINDOW` is declared but unenforced.** PayPal refuses more than 10,000 records for one date window; a busier account truncates upstream silently. Needs adaptive window splitting.

*Closed in v1.8: the interpreter split (Codespace pinned to 3.12) and the `Store` sync/async split (`badfa03`).*

*New in v1.9: `apps/dashboard`, `okwan_core.egress`, `okwan_api/{signup,mail,ratelimit}.py`, `okwan_vault/accounts.py`.*

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
│   ├── web/                        # Next.js site (static export → Vercel)
│   └── dashboard/                  # Next.js signup/connections/key (server routes → Render)
├── tests/
├── Dockerfile · render.yaml
└── pyproject.toml
```

---

## 5. REVENUE MODEL

**Stream 1 — Direct SaaS:** Free (5K requests/mo) → Pro (100K) → Team (1M) → Enterprise (unmetered).
**Stream 2 — OEM/ISV Embed:** Startup $5K/yr → Growth $18K/yr → Enterprise $60K+/yr + per-connector royalty. *Primary long-term revenue engine.*
**Stream 3 — Agent API:** usage-based per MCP tool call, bundled minimums. *Counted from 2026-10-02: hosted MCP runs are metered as `mcp:query` and `mcp:reconcile` (§3.4).*

**Metering unit: one request.** A federated query touching four connectors is one request even though it costs four upstream calls. That asymmetry is ours to manage, not the customer's to reason about — predictability over precision.

**Billing rolls up to the root.** An ISV's merchants share one allowance and one invoice; the merchants are not customers.

**Gap:** nothing charges a card, and nothing sets a plan: `Store.set_plan` exists with no caller in the CLI or the API, so every tenant is on `free` (5K) unless its row is written by hand. Manual invoicing is fine for the first three ISVs; Stripe subscriptions are a week not yet worth spending.

---

## 6. ROADMAP & MILESTONES

| Phase | Window | Ship | Gate to next phase |
|---|---|---|---|
| **P0 Foundation** | Aug–Oct 2026 | ✅ COMPLETE 2026-08-12 | ✅ agent queries live data via Okwan MCP |
| **P0.5 Wedge** | Aug 2026 | ✅ COMPLETE 2026-08-27: Paystack, reconciliation, Shopify | ✅ GATE PASSED ×2 |
| **P0.75 Platform** | Aug 2026 | ✅ COMPLETE 2026-08-28: query federation, vault, tenancy, billing, deployment | ✅ hosted API serving tenant-scoped queries and reconciliations. *REST reconciliations were not tenant-scoped until 2026-10-02 (§9)* |
| **P0.9 Depth** | Aug–Oct 2026 | ✅ 2026-10-02: PayPal (#6), two live rail declarations, coverage and unverifiable, `reconcile_across_rails` | ✅ double collection found live across two rails (#1002, #1004) |
| **P1 Launch** | Nov 2026–Feb 2027 | 10 connectors, dashboard, self-serve signup, HN/dev launch. **Status 2026-10-02:** 6/10 connectors; dashboard and signup deployed; signup returns 503 until the mailer (§10) | 200 signups, 10 paid, $2K MRR. Paid needs plan setting and card charging, neither built (§5) |
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

**"MCP-native connector library" is commoditized** — Nango, Composio, Arcade, an existing Africa Payments MCP server, and now CData itself (Connect AI, hosted MCP at `mcp.cloud.cdata.com/mcp`) all occupy it. Competing on connector count is competing on a saturated axis.

**The detection is not ours to claim; the primitive is.** Fincile already detects duplicate billing across 11+ gateways as a Shopify app. What `reconcile_across_rails` found live (#1002 and #1004 collected twice) is the class of thing a merchant product can already show. The claim is the primitive underneath it: a declaration an ISV writes and versions, which yields a REST route, a SQL view and an MCP tool, runs per tenant, and says what it could not see (unverifiable) rather than reporting a clean result over a partial read.

**The defensible wedge is the federation and reconciliation layer none of them address.** Connector libraries fetch from one system at a time. Reconciliation is a *query across* systems that disagree.

| Player | Their game | Why they don't cover this |
|---|---|---|
| CData | 350+ connectors; Connect Cloud rebranded Connect AI with a hosted MCP (`mcp.cloud.cdata.com/mcp`) | Agent access to one source at a time is now their product too. Still no cross-source matching primitive; BI-shaped, not money-shaped |
| Fincile | Shopify app: duplicate-billing detection across 11+ gateways | Covers the detection. Sold to merchants as a product; the gap is a primitive an ISV declares, versions and embeds per tenant |
| Fivetran | ELT pipelines to warehouses | Lands data; leaves reconciliation to the analyst |
| Nango / Composio / Arcade | Agent tool-calling, auth infrastructure | Single-source tool calls; no matching, no currency semantics |
| Merge.dev / unified APIs | Category-specific unified APIs | Normalizes shape, not truth; no cross-rail join |
| A2X / Synder / Link My Books | Merchant recon into QuickBooks/Xero | Products, not primitives — no embeddable engine, no API, rules are UI config |
| Modern Treasury / close-automation | Payment ops for finance teams | Enterprise-shaped, not developer-shaped |
| DIY internal builds | Every ISV's default | Our pricing must beat one engineer-month per rail pair |

**Moats to build:** reconciliation rule library as licensed SKUs · ISV switching costs · correctness details competitors get wrong (zero-decimal currencies, MSISDN ambiguity, minor-unit arithmetic, gross-vs-net on refunds, ambiguous pairing) · **merchant-scoped agent endpoints** — an ISV provisions a tenant per merchant and each gets an MCP endpoint seeing only their own systems. No recon competitor offers this: the recon products have no API, and the agent-native connector libraries have no tenancy model. CData's hosted MCP is the nearest, and it serves sources, not reconciliations.

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
| 2026-10-02 | **`limit` pages rows; it no longer caps the read. Folds on the hosted MCP** | `limit` had been passed as the per-side fetch cap on every surface, so asking for 100 rows reconciled a 100-record fragment and reported the rest unmatched or unverifiable. The read now runs to `Reconciliation.max_records`, default raised 500 → 5,000. `limit` bounds rows returned, with `cursor`/`next_cursor`/`has_more`/`total_rows`; the summary always covers the whole result. Tools are stateless, so each page re-runs, and an offset into a changed result would skip or repeat rows without a sign. The cursor carries a fingerprint of the rows it was cut from (status and records, nothing clock-derived), and a stale, foreign-filter or unreadable cursor is refused (409 on REST), never restarted. This departs from PayPal's restart-on-bad-cursor rule because a restarted page here masquerades as the next one. Hosted: `okwan_list_reconciliations` adds `across` with per-tenant runnability (a fold needs every member's credentials); `okwan_reconcile` dispatches by name to either kind and validates `status` against that kind's vocabulary. Names are now one space across both registries. REST's status filter derives from `engine.STATUSES` and reaches ambiguous/matched_explained/matched_discrepant. The hosted `ambiguous` side list is dropped: it was unpaged, and each ambiguous row already carries its candidates. Supersedes the open limit item above |
| 2026-10-02 | **Self-serve signup reverses CLI-only root tenants** | The 2026-08-28 rule was sound when there were no users: an endpoint minting root accounts was an open door with nobody to walk through it. Onboarding by CLI commands Felix runs (§10 item 2, and §11's tty-bound vault CLI) is now what stands between the product and the first ISV, so the open door is the point. What keeps it a door: the root tenant exists only after the emailed token returns *with the password that created that signup*, which closes pre-registration takeover; one tenant per address is the `accounts` primary key; signup and sign-in never distinguish a registered address from an unknown one. Sessions (`oks_`) are a separate token type from API keys and are accepted on admin routes only. 404-not-403 held everywhere, which surfaced a gap: `DELETE /v1/tenants/keys/{id}` had no subtree guard, so any key holder could revoke any key by id. Now guarded, and another tenant's key reads as nonexistent. A live race test against Neon found that two tokens for one address deadlocked in verification (a 500, not a second tenant); verification is now serialized per address by an advisory lock. **Not built: mail.** No provider is configured, so outside dev signup answers 503 rather than mint accounts on unproven addresses |
| 2026-10-02 | **Dashboard with Test and Save: proof rather than a promise** | `apps/dashboard`, Next.js 15 with server routes on Render beside the API. Credential entry posts each field through the existing `put_credential`; values are never returned, logged or held past the write, and the API's 422 handler no longer echoes request input (FastAPI's default would have returned a malformed body's secret). Test runs `Connector.probe()` against what the *vault* holds, so a pass also proves the write and the decrypt round-trip. Upstream errors are scrubbed of stored values. Results: rows · empty · failed · missing · untestable. An empty windowed read names its window. Live on the Codespace credentials: Stripe, Shopify, Postgres return rows; PayPal reads empty for 2026-09-02 → 10-02, which is correct, since its sandbox data predates its 30-day default (see the lookback entry above) |
| 2026-10-02 | **Egress bounds and rate limits gate the mailer; they do not follow it** | Both were open §11 items from the signup work, and both become exploitable the moment verification mail can be sent, because that is what turns "any key holder" into "anyone". Wiring the mailer before them would be shipping the exposure and fixing it afterwards. Egress: resolve, refuse non-public answers, dial the checked address (§11). It is a core module rather than Postgres code because any future connector taking a host from a credential needs the same rule. Shopify gets a stricter rule than resolution: `*.myshopify.com` only, since the Admin API is served nowhere else and Shopify controls that zone. Rate limits: per IP and per address or tenant on the four routes that spend scrypt or an upstream call; scrypt moved off the event loop. Live: Neon still reads through the pinned connection with TLS and SNI intact (11 tables via the Test route); metadata and loopback DSNs are refused before any socket opens. Order of the remaining launch work: deploy and confirm the forwarded-IP hop on Render, then the mailer |
| 2026-10-02 | **Reconciliation REST behind the key; per-request credentials gone; metering covers every run** | Found while checking v1.9 against the code. `/v1/reconciliations/*` took no API key, read credentials from per-connector request headers, and fell back to the server's own environment. That is the per-request model the 2026-08-28 vault entry records as removed, surviving in the one router added alongside it. Production was not exposed only because Render carries no rail variables; setting one for a demo would have opened that account to anyone. The routes now use `check_quota` and vault resolution, and `_header_resolver` is deleted. The hosted MCP and these routes were also unmetered, so §3.4 and §5 described coverage that did not exist. The rule is now one line: every call that reads upstream is gated and metered on every surface, and listings are free. A structural test fails any new route that lacks a key dependency. It first passed against the vulnerable router, because FastAPI 0.141 wraps included routers rather than flattening them; it walks into them now |
| 2026-10-03 | **A dashboard session can run a merchant's reconciliation** | The finding was reachable only with a merchant's API key over REST or MCP, so an ISV who connected rails in the dashboard saw connections and never the result — and the result is what sells. `POST /v1/tenants/{id}/reconciliations[/across]/{name}` take a session (`admin_actor`), pass the subtree guard (404, never 403, checked before the plan so a foreign id learns nothing about quota), then the same plan gate as `check_quota`, and run *as the target*: its vault, its meter (`dashboard:across`, `dashboard:reconcile`). Errors map through the one function the key routes use. This narrows the 2026-10-02 rule that a session cannot read from a rail: it now can, through these routes and the connection test only. `/v1/reconciliations/*`, SQL, connector REST and the hosted MCP stay key-only, and a test holds a session to a 401 there. The across fold now reports `match_rate`, orders paid exactly once (collected + split tender) over orders, withheld while any order is unverifiable, as the engine's is — defined once in the fold, so REST, MCP and the dashboard state the same figure. The dashboard trims rows to what it renders, so rail records with customer fields never reach the browser. Results are not persisted |
| 2026-10-03 | **Overview runs each merchant's fold on load; results stay unpersisted for now** | The redesigned dashboard leads with findings across merchants (Overview, Findings), and results are not stored. Rather than add a table in the same change as the redesign, each load runs the `rails` fold server-side, in parallel, for every merchant with two or more rails ready, through the existing session route, cached for that render only. A failed run shows the merchant as "Couldn't run" and never fails the page. **Cost: every Overview or Findings load is metered once per eligible merchant (`dashboard:across`)**, so an ISV with ten merchants spends ten requests of its 5K per visit. *Amended same day:* a successful run is reused from the dashboard server's memory for 10 minutes, keyed by merchant and ready rails, so reloads and Overview→Findings inside that window are free; failed runs are unmetered and retry. Worst case is now one request per eligible merchant per 10 minutes of active viewing, not per load. The merchant page's Run button stays fresh. The sidebar badge and the Merchants list read an in-tab memory store fed by those runs, so the shell never runs anything. Persistence (a results table written on each run, read by these pages) is the next decision, taken once real usage shows the cost |
| 2026-10-03 | **The dashboard mirrors two backend facts until sessions can read them** | A check reads Shopify, PayPal and Stripe, and a missing credential fails the whole run, but the dashboard called any two connected rails "Ready", so a Shopify+Stripe merchant showed "Couldn't run" on every load. The fold's members are exposed only behind an `okw_` key (`GET /v1/reconciliations`), never to a session, so `lib/finding.ts` now mirrors them as `FOLD_READS` (declarations.py `rails`). Separately, `lib/money.ts` formatted minor units with Intl's currency exponents, which disagree with `okwan_core/currency.py` (how PayPal and Shopify amounts become minor units) for KWD, BHD, JOD, OMR, TND and every zero-decimal code the core table lacks, so those amounts would read 10x or 100x off; it now mirrors `ZERO_DECIMAL_CURRENCIES`. Stripe's rail amounts are Stripe's own minor units and never pass through core, so for BIF, DJF, GNF, KMF, MGA and the three-decimal codes they still disagree with this table (§10 item 12). **Both are second copies and break the one-declaration rule (CLAUDE.md)**; each carries a comment naming its source. Removing them needs a session-readable fold description and a currency exponent on each money field (§10 item 12) |
| 2026-10-04 | **A session can read the meter; the dashboard's plan figures are the API's** | The sidebar showed a static "Free · 5,000" because nothing readable by a session said what the tenant had spent. `GET /v1/tenants/{id}/usage` now takes a session or a key (`admin_actor`), passes the subtree guard (404, never 403), and returns the billing root's plan and month-to-date figure (`Quota` over `usage_since(root, month_start())`, the same arithmetic as the 402 gate) plus buckets per tenant and surface for the target's subtree over a window of 1–92 calendar days ending today, UTC, hourly or summed per day (`granularity=day`, what the dashboard asks for, which bounds the response for a long window over many merchants) (`usage_buckets`, memory and Postgres); tests hold the calendar-day boundary, the day sum and the unknown-id 404. Reading it is free: listings and the meter are never metered (§3.4). Every live plan figure in the dashboard (sidebar meter, Plan & usage, the merchant Usage tab, the strip on Overview, Findings and the merchant page) comes from this one route; when it does not answer, the sidebar says so rather than show a default. The Plans table on Plan & usage still mirrors `PLANS` (okwan_vault/usage.py), flagged in place. The warn (80%) and limit thresholds live only in `apps/dashboard/lib/usage-shape.ts`; if the API ever gains a soft limit, they move there. The setup checklist's "key issued" and "agent connected" steps are confirmed by any `mcp:*` bucket in the window rather than a self-tick, and a confirmation is kept as a tick so a quiet month does not reopen it, because the API still cannot list keys (§10 item 21) |
| 2026-10-04 | **`/v1/connectors` states the three surfaces per declaration, including what writes** | `ConnectorInfo` gains `sql_tables` (from `tables_for`) and `writes` (every operation whose `is_read_only` is false), so the dashboard's catalog page draws each connector's REST routes, SQL tables and MCP tools from the registry rather than a hand-kept list, and marks the operations that write. That surfaced a fact the copy had hidden: connector REST mounts every declared operation, and WhatsApp declares two send operations, so the hosted REST carries two write routes. WhatsApp is not a payment rail and no route can move money, but "no write operations to any rail" (CLAUDE.md) and "every surface is read-only" were overstated. The Security tab and the catalog now say exactly which operations write. **Open (§10 item 23):** unmount non-read operations from the hosted REST (removes two public routes) or keep them and keep saying so |
| 2026-10-04 | **Benchmarked against CData Connect AI: build what the meter and the registry can prove, flag what needs persistence or OAuth** | Nineteen screens of Connect AI (Integrations, Billing, Dashboard analytics, Logs, Data Security, Identities, per-client Connect modals, Playground prompts, agent-first onboarding) were read against the dashboard surface by surface. Built this round, all from data the store already holds: usage analytics with a 7/30/90-day window and per-channel and per-merchant breakdowns; a plan strip on the three pages whose next click spends a request, stating the price in Okwan's unit and, at the limit, the 402 and the reset instant from the API's month start; the connector catalog; MCP client recipes for Claude Code, Cursor, Claude Desktop and any client, with the first-hand Windows pitfalls; a prompt library with one question per fold verdict bound to its exact tool call and its Findings filter, so an agent's answer is checkable; an Endpoints modal; data-handling rows pinned to code paths; what every plan includes, true because nothing but the quota gate reads the plan. Deliberately not copied: client logos (no assets, trademark exposure); agent-does-setup onboarding (an Okwan agent can read a merchant's rails but can never connect them; read-only is structural); a per-call request log and chat retention (new persistence; the meter is the log, and query text must never be stored); users and roles (one owner per root today); OAuth for remote clients, which ChatGPT connectors and claude.ai Connectors require and the bearer-only hosted MCP does not offer, to be decided after `okwan.ai` so redirect URIs bind to the final host. Claude Desktop's mcp-remote header is now passed as `Authorization:${OKWAN_AUTH}` with the value in `env`, mcp-remote's documented form for Claude Desktop, with no space anywhere in the argument (§11) |
| 2026-10-04 | **Connected systems carry their mark, beside the name and never instead of it** | The owner's call after the CData benchmark: a connected tool reads faster with its logo. The dashboard now shows a monochrome mark for Shopify, PayPal, Stripe, PostgreSQL, WhatsApp, Claude (Claude Code and Claude Desktop) and Cursor on the catalog, the connection tiles, rail chips, the merchants table, the proof drawer and the MCP client tabs; a system without one (Paystack) gets a letter mark so rows scan the same. Paths are from Simple Icons (CC0 1.0), vendored into `app/_components/ui/brand-mark.tsx` so the dashboard adds no dependency. **Each mark is its owner's trademark**, used nominatively to identify the integration it names: drawn in the text colour at 12–22px, never recoloured, never animated, never as a hero or in Okwan's own branding, with the owner's brand page named in the component. Full-colour marks were rejected: they would fight §2 (volt and navy are the only saturated colours) and some owners constrain the colour mark more than the one-colour one. The decorative mark is `aria-hidden`; the name stays the accessible text |

---

## 10. IMMEDIATE NEXT ACTIONS

**Launch path** *(item 2 waits on none of it)*
1. **Wire a mail provider.** The last gate on signup: it answers 503 in production until verification mail can be sent (`okwan_api/mail.py`). The provider sees every verification link, so it is a security choice as well as a vendor one. Its other gates are closed (egress bounds, rate limits and the forwarded client IP, §9, §11)

**Revenue**
2. **Talk to one ISV.** Unmoved for five versions. Needs none of the launch path: the hosted MCP with a provisioned tenant is the demo, and `reconcile_across_rails` is the finding to show
3. **Plan setting, then card charging.** Nothing sets a plan today (§5), so the 402 gate holds every tenant to 5K. A CLI or admin command first; invoicing stays manual for three ISVs

**Build**
4. **Promote a `Decimal`-based `money_to_minor` into core** and delete the float `to_minor`, including the engine's `Fuzzy` path, which rescales minor-unit amounts (§4)
5. **Enforce `MAX_RECORDS_PER_WINDOW`** with adaptive window splitting. A PayPal account busier than the sandbox truncates silently today
6. **Correct the #1001 Shopify fixture**, and the landing hero that leads with it: §2.4's table shows #1001 as "explained" at a $2,423.00 rail figure, which §9 records as a seeding artifact not to be used as a reference case
7. Connectors, chosen by the §3.5 criterion rather than the stale P1 list
8. ~~Merchant view in the dashboard~~ — **done 2026-10-03.** `/merchants` lists child tenants and adds one; `/merchants/[id]` reuses Connections and the shown-once key for that tenant, with the API's subtree guard deciding access
9. ~~Reconciliation results in the dashboard~~ — **done 2026-10-03.** Run reconciliation on `/merchants/[id]` (the `rails` fold, run as the merchant), `/results` lists merchants to run; not persisted
10. ~~Dashboard redesign~~ — **done 2026-10-03 (v1.12).** Left-sidebar app shell; `/overview` is the signed-in home (collected-twice hero, needs-attention list, setup checklist, merchants table); `/merchants/[id]` has URL tabs Findings | Connections | API keys; `/findings` replaces `/results`. Shared UI primitives in `app/_components/ui/`. No API, authz or route-handler change
11. **Persist reconciliation results.** Overview and Findings run every eligible merchant's fold at most once per 10 minutes per dashboard server, each metered (§9 2026-10-03); the memory cache is lost on restart and not shared across instances. A results table written on each run would make those pages free to load and give the Merchants list a real last result
12. **Give sessions what the dashboard now mirrors.** Expose the fold's member connectors to an admin session and state each money field's exponent, then delete `FOLD_READS` and `ZERO_DECIMAL` from `apps/dashboard/lib` (§9 2026-10-03). Flagged: Stripe rail amounts are Stripe's own minor units and skip core. Stripe quotes KWD, BHD, JOD, OMR and TND in thousandths (core treats them as hundredths) and BIF, DJF, GNF, KMF and MGA as zero-decimal (core treats them as two-decimal); normalising Stripe amounts through core before the fold closes both
13. **Decide whether a one-rail match can be short.** `_decide` (across.py) returns `collected` whenever exactly one rail matches, whatever it took: a $120.00 order matched to a $90.00 PayPal capture reads "Paid once". The dashboard's money trail now shows the gap, but the verdict, match rate and every surface still count it as paid. Comparing a single rail's gross to the order total, as `_classify_multi` already does for two, would make it `collected_inconsistent`; it changes match rates, so it is a decision, not a fix

**Strategic**
14. **Paystack account** — signup needs a business registered in a supported African country. The Ghana sole proprietorship covers sandbox access for testing only; any revenue routing is an attorney/CPA question and must not be improvised
15. Register `okwan.ai`; point the API and dashboard at it, set `OKWAN_ALLOWED_HOSTS` and `OKWAN_DASHBOARD_URL`
16. Attorney: formal OKWAN clearance opinion + intent-to-use application, IC 009 + 042
17. Move the Vercel project under the LLC (§7 entity consistency)

**Corporate / NIW** *(operational detail in NIW_WORKING_PACK.md)*
18. ~~Execute Operating Agreement + Member Resolution~~ — **executed 2026-08-30, both signatures.** Remaining: scan both flat as PDFs for Exhibits 12 and 13
19. Amended 1065s, TY2024 and TY2025 — in progress. Capture: date mailed, CPA/EA review, form revision per year, Schedule B-2 status, service center, certified-mail tracking. Proof of mailing is separate evidence from the return
20. IRS Business account transcripts via +1 267-941-1000. The transcript is the evidence; the call is only diagnostic

**Raised by the CData benchmark (2026-10-04, §9)**
21. **List keys to a session.** `GET /v1/tenants/{id}/keys` over `api_keys` (prefix, created, revoked; never the hash), so Settings › API keys and a merchant's keys tab can show a table with revoke per row, and the checklist can confirm "key issued" directly instead of inferring it from `mcp:*` usage. Store method in both backends, route behind `admin_actor` and the subtree guard, tests. About six hours
22. **OAuth for remote MCP clients.** ChatGPT connectors and claude.ai Connectors connect only through OAuth; the hosted MCP takes a bearer key. Decide after `okwan.ai` (item 15) so redirect URIs bind to the final host; until then the client recipes say so and Claude Desktop goes through mcp-remote
23. **Write operations on connector REST.** Hosted REST mounts WhatsApp's two send operations (§9 2026-10-04). Unmount non-read operations from the hosted REST, or keep them and keep the catalog and Security tab saying which write. One line either way; it changes the public surface, so it is the owner's call
24. **Request log and identities need persistence.** A per-call log (timestamp, tenant, surface, status, duration; never query text or row content) and members with roles are both new tables and both change what a session can see. Not before item 11

---

## 11. SECURITY NOTES

- **A failed dashboard run is scrubbed like a connection test (closed 2026-10-04).** The session run routes (`POST /v1/tenants/{id}/reconciliations[/across]/{name}`) raised the upstream error body as the HTTP detail unredacted, where `_probe` had replaced any stored value with `[redacted]` since 2026-10-02. Found by the v1.14 review and reproduced: a Stripe-style error quoting the rejected key would have shown a merchant's stored value to the ISV operator. Both routes now pass errors through the same scrub with the values the run's own resolver holds (no second vault read), cut to 300 characters; held by a test in `tests/test_dashboard_runs.py`. The Security tab's Errors row states exactly this.
- **2026-08-12 / 08-27 / 08-28 / 08-31:** Credentials transited chat on six occasions — the Neon password (twice), a Stripe test-mode key, the Stitch API key, a Shopify Admin token, a passport number, and two Okwan API keys. A sandbox app's NVP/SOAP password also appeared in a screenshot. Causes: pasted config files, screenshots, one echo where a shell substitution printed a value the command was meant to mask, and a CLI that prints a freshly issued key to stdout. Every exposed API key was revoked and reissued.
- **Standing rule:** credentials never in chat, docs, or screenshots — env vars, Codespaces secrets, and secret managers only. The rule has been breached six times, all in test-mode or sandbox scope. If security practice ever forms part of the evidence record, the record should show the rule being followed, not restated. The recurring cause is not carelessness about the rule but that the tooling makes following it awkward — see the vault CLI note below.
- **What the platform now does right, independent of that:** credentials are never transmitted per request, are sealed with per-credential data keys, are bound by AAD to their tenant, live in a database no connector can reach, and the tenant boundary is covered by tests in both directions.
- **Working pattern:** Codespaces secrets (`OKWAN_*`) scoped to `okwan-ai/okwan`, loaded at container start. Secrets only load on rebuild.
- **Repo hygiene:** the root `.gitignore` did not cover the web app's build output; a commit attempt carried 141 MB before `node_modules`, `.next` and `out` were ignored.
- **Vault CLI is unusable in GitHub Codespaces.** `cred set` uses `getpass`, which opens `/dev/tty` directly — neither pasting nor a herestring reaches it, and the prompt hangs with no error. Superseded for customers once the dashboard is deployed: credential entry posts through the API's `put_credential` and needs no terminal. The CLI's `cred set` still hangs in a Codespace.
- **An operator-created login skips email verification** (`python -m okwan_vault account add <tenant_id> <email>`, password from `OKWAN_ACCOUNT_PASSWORD` or piped stdin, never `getpass`), so it is for the operator's own tenant only; every customer account comes through signup.
- **Credential field names are a contract.** `env_credentials` maps `OKWAN_<CONNECTOR>_<FIELD>` to the adapter's `required_fields`. `OKWAN_PAYPAL_SECRET` failed silently against an adapter wanting `client_secret`; the fix belongs at the secret, not in the adapter, or the convention stops being a convention.
- **Caller-chosen destinations are bounded (closed 2026-10-02).** Open signup made `postgres.connection_string` a way for anyone to open TCP connections from the API to arbitrary hosts, including Render-internal ones. `okwan_core.egress` now resolves first, refuses unless every address is globally routable (loopback, private, link-local and so the metadata service, CGNAT, IPv4-mapped forms of each), and dials the checked address through `PinnedLoop`, so a second DNS answer is never consulted while TLS keeps the hostname for SNI (Neon routes on it). DSNs are limited to one network host and four query keys: asyncpg would otherwise read `passfile`/`service`/`sslrootcert` from this server's disk or take a second host from `?host=`. A DSN without a password now sends an empty one, since asyncpg's fallback is this server's `PGPASSWORD` and `~/.pgpass`, which it would have sent to the caller's host. Exceptions go in `OKWAN_EGRESS_ALLOW` (hostnames or CIDRs); local development against a local database needs `localhost` there. Shopify's `shop_domain` now must be `<store>.myshopify.com`, which takes away the choice of host rather than filtering it. **Remaining:** ports are not restricted, so any port on a public host is reachable.
- **Signup, sign-in, verification and Test are rate-limited (closed 2026-10-02).** Per client IP and per subject (the address, or the tenant), fixed windows, in memory, one message for every rule so a 429 is not a registration oracle, and checked after the subtree guard so a foreign tenant is still a 404. scrypt (~50 ms, 16 MiB) now runs in a thread with a cap of two concurrent hashes; it had been blocking the single worker's event loop for each one. Behind the dashboard every request arrives from one address, so the dashboard forwards the browser's IP and the API believes it only with `OKWAN_DASHBOARD_SECRET`, which Render generates and shares between the two services. **The client address, confirmed in production 2026-10-02.** Both services choose in the same order: the trusted dashboard header (API only), then `CF-Connecting-IP`, then `X-Forwarded-For` counted from the right (`OKWAN_DASHBOARD_PROXY_HOPS`, default 3; `OKWAN_TRUSTED_PROXY_HOPS`, default 1). The dashboard's chain is `<client>, <Cloudflare edge>, <Render internal>`, observed as `175.213.142.165, 172.71.110.57, 10.28.103.150`; across three requests the edge (172.68.175.85, 172.68.175.86, 172.71.110.57) and the internal hop (10.28.103.150, 10.27.25.223) both varied. Cloudflare's header beats hop counting because Cloudflare overwrites it whatever the client sent and it does not depend on how many hops Render adds. The rule for the fallback: count from the right, never read the leftmost entry, because a forged `X-Forwarded-For` lands to the left of the entries trusted proxies append. Both paths assume every request reaches Render through Cloudflare. The bug was only the dashboard's choice of address: before the fix (14:53–15:02) the API logged `dashboard-secret=matched` with `chose=` a Render-internal address (10.30.203.20, 10.28.103.150, 10.27.25.223), so the shared secret was always fine. After it: `x-forwarded-for=['74.220.48.143', '104.23.160.221'] x-okwan-client-ip=present dashboard-secret=matched via=dashboard chose='175.213.142.165'` (15:26:25 UTC, deploy of `10fe458`; 15:27:45 the same). Both temporary diagnostics (`_log_forwarded`, `logForwarded`) and `OKWAN_LOG_FORWARDED` were removed 2026-10-02. Limits are per instance.
- **No hosted surface takes a credential from a request (closed 2026-10-02).** The reconciliation REST routes were the last: no API key, credentials from `X-Okwan-<Connector>-Credential-*` headers, then from the server's own `OKWAN_<CONNECTOR>_*` environment. They now resolve the tenant from the key and read both sides from its vault, as the hosted MCP does. A sweep found no other request-borne credential: every remaining header read is outbound auth, the Okwan key or session, or the rate limiter's client IP, and no operation input has a credential-shaped field. `env_credentials` remains for the local stdio servers and the CLI; no hosted path reaches it. `tests/test_request_credentials.py` holds this two ways. Behaviourally, the vault, the server env and request headers carry different values, and only the vault's may reach a connector on any data surface. Structurally, every route outside a named public list must depend on a key or session.
- **Claude Desktop cannot invoke `npx` on Windows.** It runs MCP servers through `cmd.exe`, and `npx.cmd` lives at `C:\Program Files\nodejs\` — the unquoted space fails with `'C:\Program' is not recognized`, and one crashing server takes down every other entry in the panel. Fix: `npm install -g mcp-remote` and point `command` at `%APPDATA%\npm\mcp-remote.cmd` directly. Separately, `mcp-remote` **silently drops** a `--header` argument not in exact `Name:Value` form — a space after the colon is enough to break auth, with nothing but a warning line in the logs to show for it.
- **Config note:** Claude Desktop on Windows is an MSIX app. The live config is at `%LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Claude\claude_desktop_config.json`, **not** `%APPDATA%\Claude\`. Brace nesting is the recurring failure. `Set-Content -Encoding UTF8` adds a BOM that breaks the file; use `WriteAllText` with a BOM-less encoder.

---

*Changelog:*
*v1.15 — Brand marks for connected systems (§9 2026-10-04): monochrome, beside the name, Simple Icons paths vendored, trademarks used nominatively; a letter mark where none exists. Dashboard only; no API, authz or test change.*
*v1.14 — CData benchmark round. One API addition: `GET /v1/tenants/{id}/usage`, session or key, subtree-guarded, unmetered, returning the billing root's plan and month-to-date figure with hourly buckets per tenant and surface for a 1–92 day window, in both stores (§9). `GET /v1/connectors` gains `sql_tables` and `writes`. Dashboard: Settings (Workspace, Plan & usage, API keys, Security) with the real meter, a 7/30/90-day usage chart with a table view, per-channel and per-merchant breakdowns, a merchant Usage tab, and a plan strip on the pages that spend requests (from 80%; at the limit, the 402 and the reset instant). Connector catalog: the REST routes, SQL tables and MCP tools each declaration generates, writes marked, with copyable queries and an OpenAPI link. MCP for agents: client recipes for Claude Code, Cursor, Claude Desktop and any MCP client, each with its own steps, snippet and pitfalls; a prompt library with one question per verdict bound to its tool call, its REST form and its Findings filter; an Endpoints modal; the API reference in the sidebar. The setup checklist's key and agent steps are confirmed by the meter. Empty states say what a page is for and what it needs. Security tab gains Results, Errors, Metering and Egress rows, each pinned to a code path; Plan & usage states what every plan includes. A failed dashboard run is now scrubbed of stored values like a connection test (§11), found by the round's adversarial review, which also narrowed every "read-only on every surface" sentence to what the code does. Flags for the owner: write operations on connector REST, OAuth for remote clients, a keys listing, a request log and identities (§10 items 21–24). 511 tests (496 run, 15 skipped).*
*v1.13 — Dashboard v2, after a four-lens audit (accuracy, UX, accessibility, brand) with adversarial verification and a three-angle concept panel. Correctness: a merchant is ready to check only with Shopify, PayPal and Stripe all connected, and readiness names what is missing; money formats at the API's exponents; two page loads share one metered run; the hero's "owed back" is `overcollected_minor`; unverifiable orders no longer read "All paid once"; "None" becomes "None found" beside the merchants that couldn't run; the copy names the hosted MCP tool that exists (`okwan_reconcile` with `name: "rails"`), not `reconcile_across_rails`. Signature: a per-order money trail (order total against what each rail took, to scale, the excess hatched) and an outcome spectrum replace stat tiles, so a double collection or a short payment is visible, not read. Navigation: ⌘K palette over pages, merchants and tabs; a merchant switcher in the sidebar; deep links from any finding to the order, opened and expanded; a merchant page opens on a result Overview already paid for. Findings is a refund worksheet: outcome, merchant and order filters in the URL, an at-stake column, CSV export built in the browser from minor units. Agents: one navy panel per view with the exact MCP call, curl and a read-only agent prompt; MCP setup starts from a merchant and issues that merchant's key. Accessibility: page titles, skip link, the narrow menu as a dialog, input borders at 3.5:1, run results announced, volt out of status colours. Two backend facts mirrored and flagged (§9); engine question on one-rail short payments raised (§10 item 13). No API or route-handler change; 491 tests, unchanged.*
*v1.12 — Dashboard redesign: a left-sidebar app shell (Overview, Merchants, Findings, Developers) in place of the top nav. Overview is the signed-in home — the amount collected twice across merchants as the one volt figure, a needs-attention list in plain sentences, a get-set-up checklist, and a merchants table — fed by running each eligible merchant's `rails` fold on load, reused for 10 minutes in server memory, unpersisted and metered per merchant (§9). Merchant detail gains URL tabs (Findings with an expandable order table and filter pills, Connections as rail tiles with a slide-over form, API keys with MCP and curl snippets). `/findings` replaces `/results`. Shared primitives and status tokens; symbol plus label for every status; 390px layouts. No API or route-handler change. 491 tests (476 run, 15 skipped), unchanged.*
*v1.11 — Reconciliation results in the dashboard: an ISV runs a merchant's `rails` fold with its session and sees the finding — the amount collected twice as the headline, outcome counts, per-rail coverage, and the orders to look at, double collections first. New session routes run as the merchant behind the subtree guard and the plan gate, metered to the merchant; data routes stay key-only (§9). The across fold reports `match_rate`, withheld while anything is unverifiable. `/results` lists merchants to run. 491 tests.*
*v1.10 — The forwarded client IP confirmed in production: Cloudflare's `CF-Connecting-IP` first, then `X-Forwarded-For` counted from the right, with the dashboard's secret-gated header taking precedence at the API (§11). The bug was the dashboard forwarding a Render-internal address; the secret always matched. Both temporary diagnostics removed. The mailer is now the only launch gate (§10). 459 tests. Merchant view in the dashboard: an ISV now sees its child tenants' rails under Merchants instead of an empty Connections page; dashboard-only, with the id passed through to the API's subtree guard (§10). 474 tests.*
*v1.9 — PayPal shipped as connector #6 over a window walk: Transaction Search caps a query at 31 days, so the cursor encodes (window, page) and the walk clamps to PayPal's own last-refreshed time. Two live rail declarations, `shopify_paypal` and `shopify_stripe`. Three engine corrections found only against real data: payment classification by direction rather than event code, `Explains` on magnitude rather than raw value, and the dead PayPal fuzzy fallback (net-to-net could never fire) moved to gross-to-gross. `Reconciliation.lookback` declares one span per run. Results state what was read: per-side coverage, then `unverifiable` as a seventh outcome replacing the caveat, with `match_rate` withheld while anything is unverifiable. `reconcile_across_rails` folds the rail declarations per order and found #1002 and #1004 collected twice ($1,449.00) — invisible to either declaration alone. `limit` pages rows instead of capping the read. Self-serve signup reverses CLI-only root tenants, gated on a verified email, with a dashboard whose Test and Save proves credentials with a live read. Egress bounds and rate limits close the two exposures open signup created, and gate the mailer. Document checked against the code (§3–§5, §10, §11). That check found the reconciliation REST routes unauthenticated and still taking per-request credentials (now behind the key and the vault, with every upstream-reading call metered on every surface), no plan-setting path, and the engine's float conversion. §8 updated for Fincile and CData Connect AI. 452 tests.*
*v1.8 — PayPal shipped as connector #6 (§3.5): Transaction Search over a window walk, the first rail that pages inside a bounded date range rather than along a single sequence, with the walk clamped to PayPal's own statement of ledger currency. `shopify_paypal` declaration (§9): one order ledger against a second rail, live across two systems, 83% match rate with fees explained and $7.93 unexplained. Two engine corrections found only by running against real data — payment classification needs direction, not just an event code, and `Explains` must match on magnitude rather than raw value (§3.2). First per-connector test files, now the standard; 179 tests. Demo 4 recorded over the hosted multi-tenant MCP with vault-resolved credentials (§7, Exhibit 22) — the §8 moat claim exercised rather than asserted. Interpreter split and `Store` sync/async debt closed; the currency float path and the unenforced window-record cap recorded as new debt (§4). Vault CLI tty dependency, the credential field-name contract, and the Claude Desktop `npx` failure added to §11.*
*v1.7 — Hierarchical tenancy and the ISV provisioning API (§3.4): one account model, boundary in one tested function, 404 not 403 out of subtree. Per-request metering with subtree rollup and plan gates (§3.4, §5). Reconciliations exposed over the hosted MCP with per-tenant runnability. Landing page repositioned around the primitive with a live reconciliation as the hero (§2.4). Merchant-scoped agent endpoints added to the moat list (§8) — they fell out of the tenancy model at no cost. Store sync/async split and the Vercel entity inconsistency recorded as debt (§4, §7). Repo hygiene note added (§11).*
*v1.6 — L2 query federation over REST and MCP. Credential vault, API-key tenancy, per-tenant resolution. Deployed to Render. Hosted multi-tenant MCP. Three correctness fixes closed: ambiguity, explained discrepancies, window default. MoMo rejected; connector criterion recorded. US market researched. Third demo gate logged.*
*v1.5 — Shopify shipped as connector #5. `AmountRef` and per-pair discrepancy reporting; `Fuzzy` per-side paths. Second demo gate.*
*v1.4 — Strategic pivot to the cross-rail reconciliation wedge. Reconciliation layer added. Paystack shipped. Competitive frame rewritten.*
*v1.3 — (unlogged in prior versions; superseded)*
*v1.2 — P0 demo gate passed 2026-08-12. Security notes section added.*
*v1.1 — Renamed Conduit AI → Okwan AI after USPTO clearance killed "Conduit".*
*v1.0 — initial master file created from ConnectCore → Conduit rebrand session.*
