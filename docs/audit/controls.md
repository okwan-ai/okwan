# Controls

Each claim, the code that enforces it, and the test that proves it.
Paths and test names are checked by `tests/test_audit_pack.py`: this
table cannot point at something that no longer exists.

## Tenant isolation

| # | Claim | Enforced in | Proven by |
|---|---|---|---|
| C1 | Every route, mount and hosted MCP tool is classified, so none escapes the isolation tests | — | `tests/test_tenant_isolation.py::test_every_route_is_classified`, `tests/test_tenant_isolation.py::test_every_hosted_mcp_tool_is_classified` |
| C2 | A route naming another tenant answers 404 exactly like a missing tenant, and changes nothing | `apps/api/okwan_api/admin.py` (`_guard`), `packages/vault/okwan_vault/authz.py` | `tests/test_tenant_isolation.py::test_an_outsider_gets_404_and_changes_nothing`, `tests/test_tenant_isolation.py::test_a_foreign_id_reads_exactly_like_one_that_does_not_exist` |
| C3 | Every data surface returns only the caller's records | `apps/api/okwan_api/auth.py`, `packages/query/okwan_query/mcp_http.py` | `tests/test_tenant_isolation.py::test_each_http_data_surface_returns_only_the_callers_records`, `tests/test_tenant_isolation.py::test_each_hosted_mcp_tool_returns_only_the_callers_records`, `tests/test_tenant_isolation.py::test_every_connector_operation_reads_only_the_callers_vault` |
| C4 | Nothing carries over between requests | `packages/vault/okwan_vault/store.py` (`resolver_for`), `packages/query/okwan_query/session.py` | `tests/test_tenant_isolation.py::test_back_to_back_callers_never_see_each_other`, `tests/test_tenant_isolation.py::test_the_process_wide_paypal_token_cache_is_keyed_by_the_secret` |
| C5 | Every query filters on the tenant, enough on its own | `packages/vault/okwan_vault/postgres.py` | `tests/test_tenant_isolation.py` on the `postgres_where_only` store |
| C6 | Row-level security keeps tenants apart, enough on its own; with no scope nothing is visible or writable | `packages/vault/okwan_vault/schema.sql`, `packages/vault/okwan_vault/postgres.py` (`_scoped`, `_enter_scope`) | `tests/test_tenant_rls.py::test_with_no_scope_the_role_sees_nothing_and_writes_nothing`, `tests/test_tenant_rls.py::test_scoped_to_one_tenant_a_query_without_where_sees_only_that_tenant`, `tests/test_tenant_rls.py::test_scoped_to_one_tenant_the_role_cannot_write_another`, `tests/test_tenant_rls.py::test_every_store_method_over_tenant_data_runs_scoped` |
| C7 | The restricted role cannot log in, owns nothing, cannot bypass, reads only tenant tables | `packages/vault/okwan_vault/schema.sql` | `tests/test_tenant_rls.py::test_the_role_cannot_log_in_owns_nothing_and_does_not_bypass`, `tests/test_tenant_rls.py::test_the_role_cannot_read_the_identity_tables` |
| C8 | A scope never outlives its transaction | `packages/vault/okwan_vault/postgres.py` (`_enter_scope`) | `tests/test_tenant_rls.py::test_a_scope_never_outlives_its_transaction` |
| C9 | The API refuses to start without the database wall | `packages/vault/okwan_vault/postgres.py` (`_check_isolation`) | `tests/test_tenant_rls.py::test_the_api_refuses_to_start_without_the_wall` |
| C10 | The isolation tests fail when isolation breaks | — | `scripts/plant_leaks.py` |

## The query engine

| # | Claim | Enforced in | Proven by |
|---|---|---|---|
| C11 | Caller SQL cannot read files, the environment or the network, even past the guard | `packages/query/okwan_query/session.py` (`SANDBOX`) | `tests/test_tenant_isolation.py::test_the_engine_itself_refuses_to_leave_its_tables`, `tests/test_tenant_isolation.py::test_the_engine_cannot_be_unlocked` |
| C12 | The statement guard refuses every known escape and anything but a single read | `packages/query/okwan_query/guard.py` | `tests/test_tenant_isolation.py::test_the_statement_guard_refuses_every_escape` |
| C13 | No escape works over REST or hosted MCP | Both of the above | `tests/test_tenant_isolation.py::test_no_escape_works_over_rest`, `tests/test_tenant_isolation.py::test_no_escape_works_over_hosted_mcp` |

## Secrets and credentials

| # | Claim | Enforced in | Proven by |
|---|---|---|---|
| C14 | Each credential is sealed with its own key and bound to its tenant, connector and field | `packages/vault/okwan_vault/crypto.py`, `packages/vault/okwan_vault/models.py` (`aad`) | `tests/test_vault.py`, `tests/test_vault_postgres.py` |
| C15 | Keys, sessions and verification tokens are stored only as hashes | `packages/vault/okwan_vault/apikey.py`, `packages/vault/okwan_vault/accounts.py` | `tests/test_vault.py`, `tests/test_signup.py` |
| C16 | No hosted route takes a credential from a request, and a tenant without one is not rescued by the server's environment | `apps/api/okwan_api/main.py`, `apps/api/okwan_api/auth.py` | `tests/test_request_credentials.py::test_every_route_that_can_read_a_rail_requires_a_key`, `tests/test_request_credentials.py::test_an_unconfigured_tenant_is_not_rescued_by_the_environment` |
| C17 | Validation errors never echo the input (a password, a secret) | `apps/api/okwan_api/main.py` (`_validation_error`) | `tests/test_signup.py::test_a_malformed_credential_body_does_not_echo_the_value`, `tests/test_signup.py::test_a_rejected_signup_does_not_echo_the_password` |

## Edge and transport

| # | Claim | Enforced in | Proven by |
|---|---|---|---|
| C18 | Bodies over 1 MiB are refused before any handler, declared or streamed | `apps/api/okwan_api/edge.py` | `tests/test_edge.py::test_a_declared_oversized_body_is_refused_before_any_handler`, `tests/test_edge.py::test_a_streamed_oversized_body_is_refused` |
| C19 | Every API response carries HSTS, DENY framing, nosniff, no referrer, no-store; JSON a locked policy | `apps/api/okwan_api/edge.py` | `tests/test_edge.py::test_json_responses_carry_every_protective_header` |
| C20 | A caller-chosen host must be publicly routable, and is dialed as checked | `packages/core/okwan_core/egress.py` | `tests/test_egress.py` |

## Operations

| # | Claim | Enforced in | Proven by |
|---|---|---|---|
| C21 | Every failure the monitor reports links to a runbook section that exists | `scripts/monitor.py`, `docs/ops/RUNBOOK.md` | `tests/test_monitor.py::test_every_runbook_link_the_monitor_gives_exists` |
| C22 | The suite runs against a real Postgres on every pull request, and fails if an isolation test skipped | `.github/workflows/ci.yml` | The workflow's own run on each pull request |
