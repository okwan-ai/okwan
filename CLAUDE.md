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

## Stack (locked, §4)
Python 3.12, FastAPI, Pydantic v2, httpx async, DuckDB, Neon Postgres.
Next.js 15 + TypeScript + Tailwind for web. Deployed on Render.

## Money
Integer minor units everywhere. Decimal-string sources convert with
`Decimal`, never float. `okwan_core.currency.to_minor` uses float and is
known debt — do not route new code through it.

## Read-only is structural
No write operations to any rail. `validate_against_registry()` refuses a
declaration over a write op. The SQL statement guard is the one place
read-only is enforced rather than derived.

## Naming
Public name is Okwan only. "Conduit" appears in the decisions log as
history and must never appear in code, branding, or copy.

## Credentials
Never in chat, code, config files, or screenshots. Codespaces secrets and
the vault only. Convention is `OKWAN_<CONNECTOR>_<FIELD>` matching the
auth adapter's `required_fields` exactly.

## Scope
~10 focused hrs/week. Smallest shippable increment first. One clear
recommendation, no option lists.

## Out of scope for Claude Code
Immigration and legal items — flag them, don't draft them. Those live in
`NIW_WORKING_PACK.md` and are handled separately.
