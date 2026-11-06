# Tenancy and Trust Boundaries

> **Package rename (2026-11-02):** `main` renamed the package `agentwatch` → `panoptis` (commit `db95c31`). This branch was merged onto that layout in `4c2025d`; file paths below that start with `agentwatch/` now live under `panoptis/`.

How AgentWatch decides which tenant a request acts for, and which paths enforce it. Written for TO-003 and kept current as routes change.

## Rule

A tenant is never chosen by the caller. It comes from a validated credential:

| Deployment | Credential | Tenant |
|---|---|---|
| Self-hosted, single tenant (default) | `AGENTWATCH_API_KEY` (or no key in development) | always `default` |
| Self-hosted, several tenants | `AGENTWATCH_TENANT_KEYS` (a server-side key → tenant mapping) | the key's tenant |
| Cloud mode (`AGENTWATCH_CLOUD_MODE`) | a tenant API key from the tenant store | the key's tenant |

`X-Tenant-ID` may be sent only as a *selector* that repeats the credential's tenant. A header naming any other tenant is rejected with **403**, never silently honoured.

## Audit (2026-11-02, before the TO-003 fixes)

| Path | Scoped before? | Problem found |
|---|---|---|
| `/api/v3/*` (`build_v3_router`) | partly | Every route takes the tenant from one dependency, `_v3_tenant`, and reads by id check `obs.tenant_id`. But `_v3_tenant` returned `X-Tenant-ID` verbatim outside cloud mode, and fell back to it in cloud mode. |
| `POST /api/v1/events`, `POST /api/v1/sessions` | **no** | No tenant recorded. Cloud events were teed into the v3 `default` tenant. |
| `GET /api/v1/sessions`, `GET /api/v1/sessions/{id}` and its `/events`, `/trace`, `/confidence`, `/reasoning`, `/cost`, `/replay`, `/checkpoints`, plus `POST …/simulate`, `POST …/rollback` | **no** | `_require_api_key` accepts *any* tenant's key in cloud mode, so tenant A could read or act on tenant B's sessions by id. |
| `DELETE /api/v1/sessions/prune` | **no** | Prunes every tenant's sessions. |
| `GET /api/v1/dashboard/summary`, `/top`, `/safety/blocked`, `/compliance/audit-log`, `/governance/*`, `/ingestion/metrics`, `/system/status` | **no** | Cross-tenant aggregates, visible to any tenant key. |
| `GET` / `POST /api/v1/tenants`, `GET /api/v1/tenants/{id}` | **no** | Any tenant key could list or create tenants. |
| `/api/v1/tenants/{id}/api-keys`, `/usage` | yes | `_require_tenant_ownership`. |
| `POST /api/v1/gdpr/erase` | partly | `_require_tenant_context`, but the session-scope delete ignored the tenant (TO-004). |
| `WS /ws/events` | **no** | Checked only the legacy key, then forwarded every event on the bus to every client, whatever the tenant. |
| CLI (`agentwatch …`) | n/a | Runs against a local store with the OS user's permissions; `--tenant` selects within a store the user can already read. This is a local trust boundary, not a network one. |
| Protect pipeline / MCP gateway | yes | `ToolAction.tenant_id` is set by the integration from its configuration, never from the agent; evidence is recorded under it (TO-002). |
| Celery tasks (`panoptis/tasks.py`) | n/a | No tenant-sensitive task reads request data. |

## After TO-003

| Path | Now | Test |
|---|---|---|
| `/api/v3/*` | tenant from the credential; a forged or mismatched `X-Tenant-ID` gets 403; a missing, revoked, expired or suspended key gets 401 | `tests/v3/test_tenant_isolation_api.py`, `tests/test_tenancy_resolver.py` |
| v1 ingestion | events and sessions tagged with the authenticated tenant (any client value is overwritten); no appends to, or re-registration of, another tenant's session (re-checked under the collector lock); the tee writes into the caller's tenant | same file |
| v1 persistence | the database decides who owns a session id, so ownership survives a restart and holds across workers and concurrent creation; a trigger stops a session's tenant changing; every multi-tenant deployment tags rows with their tenant (see [`PERSISTENCE.md`](PERSISTENCE.md#session-ownership)) | `tests/test_session_event_persistence.py` |
| Cost budgets | kept per (tenant, session), so a budget cannot pass to a later holder of the id; no tenant means `default`. Erasure removes it | `tests/test_runtime_tenant_isolation.py` |
| Checkpoints | `rollback` itself checks that the checkpoint belongs to the session and tenant before restoring anything; another tenant's checkpoint answers as an unknown id does. A checkpoint with no tenant belongs to `default`. Erasure removes the caller's metadata and snapshots. The API creates no checkpoints today, so these routes find none | `tests/test_runtime_tenant_isolation.py` |
| API keys | a tenant can only revoke its own keys; with tenant keys configured, every request and WebSocket needs a valid key, even with no operator key set. Operator routes then answer nobody until `AGENTWATCH_API_KEY` is set | `tests/test_api_hardening.py` |
| v1 session routes | 404 unless the session is the caller's; the list shows only the caller's sessions | `tests/v3/test_tenant_isolation_api.py` (11 route tests, all of which fail on the old code) |
| Operator routes | cloud mode requires `AGENTWATCH_ADMIN_KEY`, and they are closed when it is unset; a tenant may read only its own tenant record | `tests/v3/test_tenant_isolation_api.py` |
| `WS /ws/events` | tenant key required (4001); a mismatched `X-Tenant-ID` closes with 4003; only the connection tenant's events are streamed | `tests/v3/test_tenant_isolation_api.py` |
| CLI | tenant-taking commands read only that tenant; the others read only `default` | `tests/v3/test_cli_tenant_scope.py` |

**Configuration:**

- `AGENTWATCH_TENANT_KEYS`: the self-hosted key → tenant map.
- `AGENTWATCH_ADMIN_KEY`: the cloud operator credential.
- Both are documented in `.env.example`.

## Tenant and API-key storage (TO-006)

| Setting | Store |
|---|---|
| `AGENTWATCH_TENANT_STORE=<url>` | that database |
| cloud mode, no explicit URL | the v3 database (`AGENTWATCH_STORE`), else `$AGENTWATCH_HOME/tenants.db` |
| self-hosted single tenant | memory (keys come from `AGENTWATCH_API_KEY` / `AGENTWATCH_TENANT_KEYS`) |

`SqlTenantStore` (`panoptis/api/tenant_store_sql.py`) creates or upgrades its tables on start-up and refuses a schema newer than the code. Its tables:

| Table | Holds |
|---|---|
| `aw_tenants` | tenant records |
| `aw_api_keys` | the PBKDF2 `salt:hash` only, a unique `key_prefix`, scopes, `expires_at`, `revoked` / `revoked_at` |
| `aw_tenant_usage` | monthly usage |
| `aw_tenant_daily_events` | daily event counts |
| `aw_tenant_audit` | append-only record of create, update, suspend, key create and key revoke |
| `aw_tenant_schema` | the schema version |

Behaviour:

- A raw key is shown once, when it is created; it is never stored or logged.
- Suspending a tenant revokes all its keys in the same transaction.
- A database error while validating a key **denies** the key. A database error while writing **raises**, so no write appears to succeed when it did not.
- **Bootstrap:** the operator (`AGENTWATCH_ADMIN_KEY`) creates the tenant with `POST /api/v1/tenants?name=…` and issues its first key with `POST /api/v1/tenants/{id}/api-keys`. From then on the tenant manages its own keys, and a key can act only on its own tenant.

**Recovery:** the store is ordinary SQL. Back it up with the database; to restore, point `AGENTWATCH_TENANT_STORE` at the restored database. To revoke every key of a compromised tenant, suspend the tenant.

## Policy dry-run endpoint (`POST /api/v1/safety/check`, TO-008)

Evaluates a command against the deployment's safety policy without running it.

- **Authentication:** an API key. In cloud mode, a tenant key or the operator key.
- **Detail:** the rule-by-rule `threat_path`, and the names and reasons of custom rules, describe the deployment's policy. They are returned only to the policy owner: the self-hosted key holder, or the cloud operator (`AGENTWATCH_ADMIN_KEY`). A cloud tenant receives `decision`, `risk_level` and `risk_score`, an empty `threat_path`, and custom rules redacted as `custom-policy` / "matched a custom policy rule".
- **Bounds:** these are rejected with 422 before any rule is evaluated, which also caps the regex work one request can cause:
  - `command` up to `AGENTWATCH_SAFETY_CHECK_MAX_COMMAND` (8192 characters);
  - `arguments` up to `AGENTWATCH_SAFETY_CHECK_MAX_ARGS_BYTES` (16 KB of JSON) and 8 levels deep;
  - `tool_name` up to 128 characters;
  - up to 100 `affected_resources`.
- **Rate limit:** the write tier (`API_RATE_LIMIT_WRITE`).
- **Tests:** `tests/test_api_safety_check.py`.
