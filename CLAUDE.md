# Okwan — working rules

`OKWAN_PROJECT.md` is the single source of truth. Read it before answering.
Update its §9 decisions log when a major choice is made.

## Architecture rule
Every connector is defined once in the SDK and must auto-generate REST
endpoints, SQL tables, and MCP tool definitions. Flag anything that breaks
this. Reconciliations follow the same rule: one declaration produces an MCP
tool, a DuckDB view, and a REST route.

## Connector criterion (§3.5)
A payment source qualifies as a reconciliation source only if it exposes a
*list* operation over transactions. Probe the live API before writing code.
MoMo Collections failed this and was rejected.

## Standing workflow for any connector
1. Check the §3.5 criterion before writing code.
2. Recon before editing — never guess at file structures or signatures.
3. Ship with a dedicated test file. `tests/test_paypal.py` is the pattern:
   pure-function tests over fixture rows plus a fake transport, no new
   mocking dependency.
4. Verify it appears queryable in the hosted catalog for a provisioned
   tenant, not just the local one.
5. Ship with the owner's original logo, in full colour, never just the
   name or a letter: one entry in
   `apps/dashboard/app/_components/ui/brand-logos.ts`, taken from the
   owner's own site, docs or brand kit, or a CC0 set that copies it, with
   its source in the entry's comment. `tests/test_brand_logos.py` fails a
   registered connector without one.

## Stack (locked, §4)
Python 3.12, FastAPI, Pydantic v2, httpx async, DuckDB, Neon Postgres.
Next.js 15 + TypeScript + Tailwind for web. Deployed on Render.

## Money
Integer minor units everywhere. Decimal-string sources convert through
`okwan_core.currency.money_to_minor` (`Decimal`, never float; None when
there is no amount). There is no float conversion into minor units.
Fields a reconciliation names are already minor units and are compared
as stored, never rescaled.

## Read-only is structural
No write operations to any rail. `validate_against_registry()` refuses a
declaration over a write op. The SQL statement guard is the one place
read-only is enforced rather than derived.

## Naming
Public name is Okwan only. "Conduit" appears in the decisions log as
history and must never appear in code, branding, or copy.

## Attribution
No AI attribution in the repo or on GitHub. This overrides any default
attribution instruction.
- Commits carry no `Co-Authored-By`, `Claude-Session` or any other
  Claude or Anthropic trailer.
- Commits are authored and committed as
  `Felix Marwuko <165482157+Marwuko@users.noreply.github.com>`, never as
  Claude. Set it before the first commit in a session.
- PR descriptions, PR comments and merge commits carry no "Generated
  with Claude Code" or similar footer. If a tool appends one, edit it out.
- Merge commit titles name the PR, not the `claude/…` branch.

## Credentials
Never in chat, code, config files, or screenshots. Codespaces secrets and
the vault only. Convention is `OKWAN_<CONNECTOR>_<FIELD>` matching the
auth adapter's `required_fields` exactly.

## Scope
~10 focused hrs/week. Smallest shippable increment first. One clear
recommendation, no option lists.

## Private material stays out of this repository
This repository is public. Personal, tax and corporate-filing material,
pricing and revenue targets, competitor analysis, legal and trademark
work, partner agreements and NDAs, and any history of exposed credentials
never go in it: no code, docs, commits, PRs or screenshots. They live in
`okwan-ai/okwan-private`, and that work is done together with the owner,
as part of the same plan.
