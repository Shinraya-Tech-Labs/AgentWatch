# Implementation Evidence Ledger

> **Package rename (2026-11-02):** `main` renamed the package `agentwatch` → `panoptis` (commit `db95c31`). This branch was merged onto that layout in `4c2025d`; file paths below that start with `agentwatch/` now live under `panoptis/`.

Task list: [`docs/MASTER_TASKS.md`](MASTER_TASKS.md). Each task records its changed files, the tests it added, the exact commands run with their results, its commits, and any remaining limitation.

Statuses: `NOT STARTED` · `IN PROGRESS` · `BLOCKED` · `FAILED` · `DONE`. A task is `DONE` only when tests exercise the behaviour; code inspection alone does not count (CTRL-004).

## CTRL-001 — Baseline

| Item | Value |
|---|---|
| Repository | https://github.com/sreerevanth/AgentWatch.git |
| Base branch / commit | `main` @ `8d5cf12e8ac948d265d274f09ab11a181c4a660d` |
| Work branch | `feat/protect-runtime-security` (CTRL-002) |
| Working tree at start | clean except the owner's uncommitted edit to `PROJECT_TASKS_TO_BE_DONE.md`; preserved and never staged |
| OS | Windows 11 Home (`10.0.26200`) |
| Python | 3.14.2 (default); 3.12.4 via `py -3.12` (the project pin is `>=3.12,<3.13`) |
| Node / npm | 22.23.0 / 11.12.1 |
| Docker | client 29.3.1; **daemon not running** (Compose smoke tests need Docker Desktop started) |
| PostgreSQL / Redis | not running locally (ports 5432 / 6379 closed); CI provides them |
| Claude Code CLI | 2.1.229 |
| Framework packages | `langgraph` 1.0.10, `langchain-core` 1.2.17 installed; AutoGen and smolagents **not installed** |
| Baseline tests | `pytest tests/`: **2226 passed, 8 skipped, 0 failed** (8 m 41 s); coverage 81.1 % of code on `main` |
| Baseline audit | [`docs/REPO_AUDIT.md`](REPO_AUDIT.md) |

## Status

| Task | Status | Evidence |
|---|---|---|
| CTRL-001 baseline | DONE | this section |
| CTRL-002 isolation | DONE | all work on `feat/protect-runtime-security`; `main` untouched |
| CTRL-003 ledger | IN PROGRESS | this file, kept current per task |
| CTRL-004 … CTRL-008 | IN PROGRESS | rules applied throughout; CTRL-008 runs at the end |
| TO-001 fail-open execution | DONE | [TO-001](#to-001) |
| TO-002 enforcement pipeline | IN PROGRESS | [TO-002](#to-002) |
| TO-003 tenant identity | DONE | [TO-003](#to-003) |
| Port onto `panoptis` layout | DONE | merge `4c2025d` (see below) |
| TO-004 tenant-safe erasure | DONE | [TO-004](#to-004) |
| TO-005 attestation keys | DONE | [TO-005](#to-005) |
| TO-006 tenant/key persistence | DONE | [TO-006](#to-006) |
| TO-007 DB fallback | DONE | [TO-007](#to-007) |
| TO-008 safety-check endpoint | DONE | [TO-008](#to-008) |
| TO-009 service exposure | DONE | [TO-009](#to-009) |
| TO-010 request limits | DONE | [TO-010](#to-010) |
| TO-011 adapter audit and enforcement | DONE | [TO-011](#to-011) |
| TO-012 Claude Code PreToolUse | BLOCKED (owner: sign the `claude` CLI in) | [TO-012](#to-012) |
| TO-013 / TO-014 / TO-015 LangGraph, AutoGen, smolagents | DONE | [TO-013, TO-014, TO-015](#to-013-to-014-to-015) |
| TO-016 generic and direct tools | DONE | [TO-016](#to-016) |
| TO-017 threat analysis | DONE | [TO-017](#to-017) |
| TO-018 … TO-040 | NOT STARTED unless listed below | — |

## Pre-task work already on the branch

| Commit | Change | Tests |
|---|---|---|
| `3a8897a` … `9cb58bd` | `panoptis/protect/`: model, argument analysis, threat classifier, output inspector, loop detector, intent grounding, RBAC, ledger, control/approvals, pipeline | `pytest tests/protect`: 55 passed (threat analysis and output inspection only; the other modules have no tests yet) |
| `6d6bb4d` | `docs/REPO_AUDIT.md` | n/a |

## Task log

<!-- one section per task, appended as work completes -->

### TO-001

**Status:** DONE

**Root cause:** in `GenericAdapter._wrap`, both the sync and async safety gates caught every exception from the safety check, logged it at debug level and ran the tool. Two further fail-open paths were also found:

- a `SafetyEngine` that failed to construct made `watch()` return the agent unwrapped;
- a malformed verdict (for example a non-bool `blocked`) was read as "not blocked".

**Changes:** `panoptis/core/watcher.py`

- The decision is separated from event publishing. Failing to reach a decision raises `AgentWatchBlockedError` (fail closed). A telemetry failure is logged and does not change the decision.
- Decision deadline: async = approval timeout + 30 s, or `safety_timeout_s` / `AGENTWATCH_SAFETY_TIMEOUT_S`. The sync path is bounded on a worker thread when a timeout is configured.
- Verdict validation: `(bool, list[str])` for sync; for async, an `AgentEvent` carrying a safety decision.
- A policy that cannot load installs `_UnavailableSafetyEngine`: the agent stays wrapped and every tool-like call is blocked.
- Fail-closed blocks are published as `SAFETY_BLOCK` events with `enforcement_failure: true`.

**Tests:** `tests/test_watcher_fail_closed.py`, 32 tests. Every one asserts the wrapped tool's side-effect counter:

- exception, sync and async;
- timeout, sync and async;
- 8 sync and 4 async invalid-verdict cases;
- policy-load failure;
- allowed calls run exactly once on sync, async and streaming methods;
- blocked calls never run;
- no double wrapping;
- telemetry failure does not block an allowed call;
- the `SAFETY_BLOCK` event is published;
- `ActionBlocked` is caught by `except AgentWatchBlockedError`;
- sync/async parity.

The original bug was reproduced first: with the old `watcher.py` stashed, the exception tests fail.

**Commands and results:**

- `pytest tests/test_watcher_fail_closed.py`: 32 passed
- `pytest tests/test_watcher.py tests/test_sync_safety.py`: 30 passed
- `pytest tests/`: 2313 passed, 8 skipped, 0 failed (5 m 36 s)
- `ruff check` / `ruff format --check` on the changed files: clean

**Commits:** `60de76f` `823ff22` `6ac18fc` `aa76269` `8e992bf` `7340060` `dbbb134` `012f4fb` `30846c9`, plus this ledger commit.

**Limitation:** a sync check that hangs is bounded only when a timeout is configured (`safety_timeout_s` or `AGENTWATCH_SAFETY_TIMEOUT_S`). A thread hand-off per call would otherwise add latency to every sync call. Without a timeout a hung check blocks the caller; it never runs the tool.

### TO-002

**Status:** IN PROGRESS. The boundary is complete and tested. The last open requirement is "all integrations call this shared pipeline"; it is met as each integration adopts it (TO-011–016 framework adapters, TO-023 MCP gateway).

**Changes:**

- **`panoptis/protect/enforce.py`:**
  - `enforce_call` / `aenforce_call`: the single boundary. Order: inspect and decide → approval or mutation → execute once → inspect output → return. The tool's own exceptions pass through unchanged. A failure inside enforcement raises `ActionBlocked`, and a failure after execution withholds the result and records `executed: true`.
  - `protect_tool()`: the public API.
  - `protection_context()` and `current_context()` carry identity.
  - Default pipeline: `set_default_pipeline` / `get_default_pipeline`.
  - `bind_arguments`: maps mutated values back to positional, keyword and variadic parameters.
- **`panoptis/protect/model.py`:**
  - `ToolAction` carries `tenant_id`, `trace_id`, `parent_call_id` and `defaulted_identity`.
  - `scope` (tenant/session) keys all session state.
  - Decisions record `mutation.changed_paths`.
- **`panoptis/protect/pipeline.py`:**
  - Per-tenant sessions, loop windows and breakers.
  - `failure_decision`.
  - `protect.execution` evidence: outcome, duration, error type, whether arguments were mutated.
  - Evidence is recorded under the action's tenant, with the trace and parent ids declared.
  - `require_context` blocks calls that carry no supplied identity.
- **`panoptis/protect/ledger.py`:** `record(..., tenant_id=)` and `KIND_EXECUTION`.
- **`panoptis/core/watcher.py`:** `watch(agent, protection=pipeline)` and `GenericAdapter(protection=)` send tool-like calls through `enforce_call` / `aenforce_call`.
- **`panoptis/core/http_forwarder.py` (performance fix found while testing):**
  - With the API server down, every event waited out a connection attempt, about 2.3 s on Windows, and a wrapped call emits several events.
  - The forwarder now backs off for `AGENTWATCH_FORWARD_BACKOFF_S` (default 30 s), and `AGENTWATCH_FORWARD_EVENTS=0` disables it.
  - Effect: the protected `watch()` test went from 12.4 s to 2.4 s; `test_watcher.py` went from about 100 s to 37 s.

**Tests:** 25 new.

`tests/protect/test_enforce.py` (16):

- ordering: decide → execute → inspect output;
- a blocked call never executes;
- tool exceptions pass through;
- an enforcement failure before execution blocks;
- a failure after execution withholds the result;
- async parity;
- identity fields reach the evidence record;
- the same session id in two tenants is two sessions;
- execution is recorded once per allowed call and never for a blocked one;
- mutated arguments reach the tool, the mutation is recorded, and the secret is never stored;
- `watch(protection=)` routes through the pipeline;
- approval: approve-then-execute order, reject, timeout, approver edits re-inspected, and edits that would be hard-blocked are refused.

`tests/protect/test_protect_tool.py` (10):

- decorator forms and metadata;
- blocked calls;
- async;
- mutation mapped back to positional-only and keyword-only parameters;
- no double wrapping;
- identity from context;
- exception passthrough;
- varargs;
- `require_context` blocks, and default mode allows.

`tests/test_http_forwarder_backoff.py` (3).

**Commands and results:**

- `pytest tests/protect`: 89 passed
- `pytest tests/`: 2341 passed, 8 skipped, 0 failed (4 m 32 s)
- `ruff check` / `ruff format` on the changed files: clean

**Commits:** `3df851e` `7daeabe` `e55d5c4` `c5620d9` `f80a467` `040eefb` `9dc1fd6` `a1a349b`, the ledger commit `79e7cef`, and `3fbf0f1`. The approval-path tests in `3fbf0f1` were written with TO-002 but left out of its commits by mistake; they were committed afterwards.

**Limitations:**

- Output from streaming methods (generators) is not inspected: the generator object is returned as is. This is tracked in TO-011 and TO-018.
- The legacy `SafetyEngine` gate is still the default for `watch()` without `protection=`; changing that default is decided in TO-016.

### TO-003

**Status:** DONE. Route-by-route audit and trust model: [`docs/TENANCY.md`](TENANCY.md).

**Vulnerabilities found and fixed** (each reproduced: the new tests fail on the old code):

1. `_v3_tenant` returned `X-Tenant-ID` verbatim in self-hosted mode, and fell back to it in cloud mode.
2. In cloud mode, any valid tenant key could read, simulate or roll back another tenant's v1 session by id, and list every session.
3. Cloud v1 events were teed into the v3 `default` tenant. A tenant could append events to another tenant's session, or re-register its id.
4. Any tenant key could list or create tenants and see cross-tenant aggregates and reports, the global safety policy, and prune.
5. `/ws/events` in cloud mode accepted anyone when no legacy key was set, and streamed every tenant's events to every client.

**Changes:**

- `panoptis/api/tenancy.py`: a pure resolver.
  - Single tenant → `default`.
  - `AGENTWATCH_TENANT_KEYS` key map → the key's tenant.
  - Cloud → the tenant store's key.
  - A header is only a selector: mismatch gets 403; no or invalid key in cloud mode gets 401, never a fallback.
- `panoptis/api/server.py`:
  - `_principal`, `_request_tenant`, `_owner`, `_require_session_owner`, `_require_operator`;
  - v1 ingestion tagging and session guards;
  - 10 session routes scoped, and the session list filtered;
  - 14 operator-only routes, plus the tenant-record rule;
  - WebSocket authentication and per-tenant filtering;
  - the tee is tenant-aware;
  - `_key_matches` accepts mapped keys, compared in constant time.
- `panoptis/tracing/collector.py`: a cross-tenant append to an existing session is dropped under the lock (race-safe).
- `tests/v3/test_contracts_api.py`: the multi-tenant isolation test uses real per-tenant keys instead of forged headers.
- `.env.example` and `docs/TENANCY.md`: configuration documented.

**Tests:** 51 new.

- `tests/test_tenancy_resolver.py` (14).
- `tests/v3/test_tenant_isolation_api.py` (36):
  - cloud: a cross-tenant observation read gets 404; the payload tenant is ignored; the header cannot switch tenant;
  - missing, revoked, expired and suspended keys get 401; a forged header in self-hosted mode gets 403;
  - v1 tagging; no cross-tenant append or claim; the collector race; the tee tenant;
  - 8 parametrised session-read cases plus 2 action cases; list scoping;
  - 9 parametrised operator GET cases, plus operator writes, closed-when-unset, own tenant record, and self-hosted unchanged;
  - WebSocket auth (4001/4003) and per-tenant delivery.
- `tests/v3/test_cli_tenant_scope.py` (1).

**Commands and results:**

- `pytest tests/v3/test_tenant_isolation_api.py tests/test_tenancy_resolver.py tests/v3/test_cli_tenant_scope.py`: 51 passed
- Existing API suites: `test_api_*`, `test_server`, `tests/v3/test_api*`, `test_contracts_api`: all pass
- `pytest tests/`: 2392 passed, 8 skipped, 0 failed (6 m 32 s)

**Commits:** `bff42c8` `46fa441` `2c4cea4` `19f7e0d` `ddd7d5d` `9728660` `eabb16d` `971b2e8` `8178f43`, plus this ledger commit.

**Limitations:**

- Tenancy on the v1 in-memory collector is held in session/event `metadata`. It is not yet a column in the optional Postgres v1 tables; persistent tenant and key storage is TO-006.
- The session-scope GDPR delete is TO-004.
- MCP gateway tenant binding is tested with the gateway (TO-023).

### Port onto the `panoptis` package layout (owner decision, 2026-11-02)

- **What changed on `main`:** commit `db95c31` renamed `agentwatch` → `panoptis`. PR #824, which the owner merged, then left an orphan `agentwatch/protect/` on `main` whose imports broke `main`'s CI at test collection.
- **Decision:** the owner chose "port the branch onto `panoptis`", fixing `main` only through this branch.
- **Merge `4c2025d`:** brings `main` into the branch.
  - Git's rename detection carried the TO-001–003 edits onto the renamed files.
  - One conflict, the import block of `api/server.py`, was resolved to `main`'s imports.
  - `protect/` and `api/tenancy.py` moved under `panoptis/`.
  - Branch code was renamed with `main`'s convention: lowercase identifiers changed; `AgentWatch*` classes and `AGENTWATCH_*` variables kept.
- **Not caused by this branch:** `tests/test_cli_animator.py` (2 tests) also fails on unmodified `main` (`panoptis.cli.animator` lost `Live`). It is fixed under TO-032.

### TO-004

**Status:** DONE. Documentation: [`docs/ERASURE.md`](ERASURE.md).

**Vulnerabilities found and fixed** (each reproduced on a real database before the fix):

1. Session-scope erasure deleted by session id with no tenant constraint. Reproduced: tenant A's erase removed tenant B's rows.
2. The other scopes deleted events by session id alone, so another tenant's events reusing the id were removed.
3. **Over-deletion:** `SessionRecord` has no `user_id` column, and unknown filter keys were silently dropped. A `user_id`-scope erase therefore matched every session of the tenant; the test counted 3 sessions instead of 2.
4. Erased sessions stayed readable through the v1 API, because they were still in the in-memory trace cache the routes are served from.

**Changes:**

- `panoptis/core/models.py`:
  - `Repository.erase_session(session_id, tenant_id)`: every `DELETE` (events, tasks, checkpoints, session) is constrained through an owned-session subquery;
  - `_session_filters`: `user_id` maps to `session_metadata.user_id` (`->>` on PostgreSQL, `json_extract` on SQLite), and an unknown key matches nothing; `count_events` follows the user's sessions;
  - JSONB columns are `JSON().with_variant(JSONB, "postgresql")`. PostgreSQL DDL is unchanged, and the schema now also runs on SQLite.
- `panoptis/api/server.py`:
  - all scopes erase through `erase_session`;
  - a new `_TraceCacheErasureTarget` covers every scope within the caller's tenant, using the principal's tenant;
  - `gdpr_erase` passes the principal tenant through.

**Tests:**

- `tests/test_session_erasure_tenancy.py` (11, real in-memory SQLite):
  - tenant A cannot erase tenant B's session;
  - the owner erases the session with its events, task and checkpoint;
  - unknown and foreign ids are indistinguishable;
  - another tenant's events with the same session id survive;
  - the API target is tenant-scoped for every scope;
  - the user scope erases only that user's sessions;
  - unknown filter keys match nothing;
  - parametrised over 4 scopes: counted equals erased, and tenant B is never reached.
- `tests/v3/test_tenant_isolation_api.py` (+3):
  - the HTTP erase evicts the owner's cached session and spares tenant B;
  - a foreign erase returns the same receipt as an unknown one;
  - v3 subject crypto-shredding stays per tenant.

**Commands and results:**

- `pytest tests/test_session_erasure_tenancy.py`: 11 passed (3.1 s)
- `pytest -k "gdpr or erasure or erase"`: 44 passed
- `pytest tests/`: 2411 passed, 8 skipped, 2 failed. The 2 failures are the pre-existing `test_cli_animator` failures on `main`, described above.

**Environment note:** with files in the Windows temp directory, SQLite fixtures stalled about 19 s each on lock waits. In-memory SQLite removed that: the file went from 158 s to 3 s.

**Commits:** `c37aa28` `0ebcff1` `762ffe8` `aa4e62a` `1337770` `8cadd74` `8745911`, plus this ledger commit. That is 8: the task had 7 separable changes, and no filler commits were made.

**Limitation:** the memory target's missing function is TO-031.

### TO-005

**Status:** DONE. Operations guide: [`docs/ATTESTATION_KEYS.md`](ATTESTATION_KEYS.md).

**Vulnerability:**

- `AttestationEngine.create_seal` and `verify_seal` defaulted to the HMAC key `panopti-seal-default-key`, which is in the source; `panopti attest verify` fell back to it too. Anyone could forge a seal that verified.
- The demo and the quickstart notebook also signed with keys published in the repository (`panopti-demo-hmac-key`, `panopti-quickstart-audit-key`).

**Changes:**

- `panopti/attest_keys.py` (new): a `Keyring` with key ids, an active key and revocation.
  - Configured through `AGENTWATCH_ATTEST_KEYS` (base64 or hex) and `AGENTWATCH_ATTEST_ACTIVE_KID` / `AGENTWATCH_ATTEST_REVOKED`.
  - Rejects keys under 32 bytes and the known public keys.
  - `AGENTWATCH_ATTEST_DEV_KEY=1` gives an ephemeral dev key, refused in production and cloud mode.
  - `check_startup()` is the server gate. Errors never include key material.
- `panopti/attest.py`:
  - version-2 seals carry `key_id` and sign every field as canonical JSON;
  - there is no default key;
  - verification fails closed on an unknown or revoked key id, a modified field or signature, or a key-id swap;
  - version-1 seals verify only with `allow_legacy=True` and an explicit key.
- `panoptis/api/server.py`: the lifespan refuses to start on an unsafe attestation configuration.
- `panopti/cli.py`: `attest verify` uses `--key` or the keyring, and exits 2 without either.
- `panopti/demo.py`: signs with a random in-process key.
- `examples/quickstart_panopti_guardrails.ipynb`: a generated key; all code cells were executed and the seal verifies.
- `panopti/reports.py`: shows `key_id` and `version`; version-1 seals still render.
- `scripts/verify_seal.py` (new): a standalone verifier using only the standard library.
- `panopti/__init__.py`: exports the keyring API.
- `.env.example`: documents the settings.

**Tests:**

- `tests/test_attest_keys.py` (14):
  - a missing configuration is an error;
  - public and weak keys are rejected; malformed entries and duplicate key ids are rejected;
  - hex and base64 keys; the active key id;
  - no key material appears in errors or summaries;
  - the dev key is ephemeral and refused in production;
  - the startup gate (4 unsafe configurations);
  - the API server refuses to start with the public key and starts with a strong key.
- `tests/test_panopti_attest.py` (21):
  - sign and verify; there is no default key;
  - public and weak keys can neither sign nor verify;
  - 7 parametrised field-tamper cases; a modified signature; the wrong key; a key-id swap;
  - rotation; revocation and unknown key ids; the keyring from the environment;
  - legacy opt-in; JSON round trip; a dev key from another process does not verify;
  - the independent verifier agrees with the engine (valid, tampered, wrong key, no key).
- `tests/test_panopti_cli.py` (+1): no default key; wrong key gives exit 1.
- `tests/v3/test_seal_reports.py` (+1, and a test keyring for the 21 rendering tests that had relied on the removed default key).

**Commands and results:**

- `pytest tests/test_attest_keys.py tests/test_panopti_attest.py tests/test_panopti_cli.py tests/v3/test_seal_reports.py`: all pass
- Demo smoke run: seal issued, exit 0
- `pytest tests/`: 2445 passed, 8 skipped, 2 failed. The 2 failures are the pre-existing `test_cli_animator` failures on `main`.

**Commits:** `45764be` `023b496` `e085f35` `f8bacc9` `620539b` `7ba0e9c` `7a38e11` `cf8838a`, plus this ledger commit.

### TO-006

**Status:** DONE. See the "Tenant and API-key storage" section of [`docs/TENANCY.md`](TENANCY.md).

**Problems found and fixed:**

1. Tenants, keys, revocations and usage lived only in Python dicts: lost on restart and inconsistent across replicas.
2. `_require_tenant_ownership` declared `path_tenant_id`, which FastAPI treated as a required *query* parameter. Every normal call to `POST/GET /api/v1/tenants/{id}/api-keys` and `/usage` therefore failed with 422.
3. There was no way to issue a new tenant's first key: only that tenant's own key could create keys.
4. Every tenant on a plan shared one `PLAN_DEFAULTS` `TenantConfig` object, so changing one tenant's limits changed every tenant's. It was found because a test leaked a limit into another file's tests.

**Changes:**

- `panoptis/api/tenant_store_sql.py` (new, `SqlTenantStore`): a durable store with the same interface.
  - Tables `aw_tenants`, `aw_api_keys` (unique `key_prefix`; PBKDF2 hash only), `aw_tenant_usage`, `aw_tenant_daily_events`, `aw_tenant_audit` and `aw_tenant_schema`; a newer schema is refused.
  - Transactional suspend-and-revoke; key creation retries on a prefix collision.
  - A failed read denies the key; a failed write raises.
  - `last_used_at` is written at most once a minute.
- `panoptis/api/tenant_auth.py`: `tenant_store_url()` and `get_tenant_store()` choose the durable store whenever cloud mode is on or `AGENTWATCH_TENANT_STORE` is set; plan configs are copied; `reset_tenant_store_for_tests()`.
- `panoptis/api/server.py`: the ownership dependency binds `{tenant_id}`, and the operator key may act for any tenant (bootstrap).
- `.env.example` and `docs/TENANCY.md` updated.

**Tests:**

- `tests/test_tenant_store_sql.py`: 14 tests (×2 back ends for the 9 database tests).
  - Tenants, keys, scopes and revocations survive a restart; suspension revokes all keys atomically and persists.
  - Only the hash is stored (the PBKDF2 `salt:hash` shape is checked); expired, unknown and malformed keys are rejected; a tenant must exist before a key.
  - The audit trail; usage persists and limits apply; concurrent create and revoke with 8 threads.
  - A database failure denies validation and fails writes; a newer schema is refused.
  - Store selection: memory when self-hosted, never memory in cloud mode, an explicit URL wins.
  - End to end over HTTP: the operator bootstraps the first key, the tenant creates its own next key, cross-tenant key creation gets 403, and after a restart a key still authenticates while a revoked one gets 401.
- `tests/test_tenant_auth_memory.py` (8): the in-memory store, including the plan-config leak test, which fails against the old code.

**Commands and results:**

- `pytest tests/test_tenant_store_sql.py tests/test_tenant_auth_memory.py`: 22 passed (SQLite)
- `AGENTWATCH_TEST_PG_URL=postgresql+psycopg://…@127.0.0.1:55432/awtest pytest tests/test_tenant_store_sql.py`: **23 passed** against a local `postgres:16-alpine` container (Docker), including all PostgreSQL variants
- Coverage: `panoptis/api/tenant_auth.py` **99 %** (audit: 19 %); `tenant_store_sql.py` 81 %
- `pytest tests/`: 2467 passed, 8 skipped, 2 failed (the pre-existing `test_cli_animator` failures on `main`)

**Environment:** installed `psycopg[binary]` 3.3.6 (PostgreSQL driver for these tests).

**Commits:** `df38938` `f9661b9` `4120657` `81415d3`, plus this ledger commit.

### TO-007

**Status:** DONE. Operations guide: [`docs/PERSISTENCE.md`](PERSISTENCE.md).

**Problems found and fixed:**

1. With `DATABASE_URL` set but unreachable, the API logged a warning and ran in memory, and `/health` said `in_memory` without marking the deployment degraded.
2. A database write failure for an event or session was logged as a warning, and the request still answered "accepted".
3. Discovered while documenting retry: an event retried after it had been stored hit a duplicate-key error (503), and the in-memory trace recorded retries twice.

**Changes:**

- `panoptis/api/server.py`:
  - `_persistence` state: `not_configured` / `connected` / `fallback`.
  - An unreachable configured database refuses to start; `AGENTWATCH_ALLOW_MEMORY_FALLBACK=1` allows a degraded start outside production only.
  - `/health` reports `persistence` and is 503 in fallback.
  - Write failures are captured per request (a context variable shared with the bus handlers), and the route answers 503 "not persisted".
- `panoptis/core/models.py`: `insert_event` is idempotent on `event_id`.
- `panoptis/tracing/collector.py`: `Trace.add_event` ignores a known `event_id`.
- `.env.example` and `docs/PERSISTENCE.md`: modes and recovery.

**Tests:** `tests/test_persistence_failures.py` (6, real aiosqlite database):

- an unreachable database stops startup;
- the explicit fallback starts degraded (`/health` 503) and every write gets 503;
- the fallback is refused in production;
- no database configured is healthy, in memory by design;
- write failures give 503 for events and sessions, health is 503, and both recover after reconnecting;
- a retry after an outage stores the event exactly once, in the database and in the trace.

**Commands and results:**

- `pytest tests/test_persistence_failures.py`: 6 passed
- Related API suites: 117 passed
- `pytest -k "collector or trace or tracing or event or replay or session"`: 276 passed
- `pytest tests/`: 2473 passed, 8 skipped, 2 failed (the pre-existing `test_cli_animator` failures)

**Commits:** `53a271b` `9a0d999` `1408747`, plus this ledger commit.

### TO-008

**Status:** DONE. Trust model: the "Policy dry-run endpoint" section of [`docs/TENANCY.md`](TENANCY.md).

**Finding:**

- Authentication was already in place on current `main`: the endpoint depends on `_require_api_key`, a change that arrived with `main`'s later commits.
- The response still listed **every** rule of the policy in `threat_path`, matched or not and including custom rules' ids and reasons, to any authenticated caller, including any cloud tenant.
- The input was unbounded, and the endpoint was not rate limited.

**Changes:** `panoptis/api/server.py`:

- `SafetyCheckRequest` bounds the command (8192), the tool name (128), the arguments (16 KB of JSON, 8 levels deep) and the resources (100), each configurable.
- Auth is `_require_api_key_or_operator`, and the route uses the write-tier rate limit.
- `threat_path` and custom-rule names go only to the policy owner (self-hosted key holder, cloud operator); tenants get the decision with custom rules redacted.
- Docs: `docs/TENANCY.md` and `docs/API-reference.md`.

**Tests:** `tests/test_api_safety_check.py` (+9):

- a credential is required when a key is configured;
- a cloud tenant does not see the rules (custom id and reason absent; empty threat path);
- the owner (operator, and self-hosted) gets the full threat path;
- 5 oversized or deep inputs are rejected before any rule is evaluated (the scorer is never called);
- the rate limit.

8 of these fail against the previous handler.

**Commands and results:**

- `pytest tests/test_api_safety_check.py`: 12 passed
- `pytest tests/`: 2482 passed, 8 skipped, 2 failed (the pre-existing `test_cli_animator` failures)

**Commits:** `6d6e9ff` `ec21f6b` `d32bd2e`, plus this ledger commit.

### TO-009

**Status:** DONE. Guide: [`docs/DEPLOYMENT_SECURITY.md`](DEPLOYMENT_SECURITY.md).

**Findings:**

- **Compose:** Postgres (fixed password `panoptis`), unauthenticated Redis, the API, the dashboard and Jaeger were all published on `0.0.0.0`.
- **Helm:**
  - `values.yaml` held `postgres.password: panoptis`, rendered in plain text into the Deployment spec;
  - the chart never set `AGENTWATCH_API_KEY`, which production requires, so its pods could not start;
  - there was no pod security context.

**Changes:**

- `docker-compose.yml`:
  - `POSTGRES_PASSWORD` and `REDIS_PASSWORD` are required;
  - Redis runs with `--requirepass`, and its healthcheck uses `REDISCLI_AUTH`;
  - every published port is on `127.0.0.1`, with `AGENTWATCH_BIND_HOST` for the API and dashboard;
  - all containers run with `no-new-privileges`; the app containers have `cap_drop: [ALL]`.
- `scripts/check_deploy_security.py` (new): the Compose exposure and credential checker. CI runs it before the smoke test, which now generates random passwords.
- `scripts/deploy_smoke_probe.py` (new): probes a running stack.
- `helm/`:
  - credentials only through a **required** `secrets.existingSecret` (`envFrom`), with no password in values;
  - pod security: `runAsNonRoot` (10001), `RuntimeDefault` seccomp, no privilege escalation, all capabilities dropped.
- `.env.example` and `docs/DEPLOYMENT_SECURITY.md`.

**Tests:** `tests/test_deploy_security.py` (13):

- the old layout is rejected (exposed ports, default and missing passwords); the hardened layout passes;
- data services are never allowed public, and an app port needs `--allow-public`;
- weak Postgres passwords (4 cases), a short Redis password, privileged containers;
- the repository's Compose file passes, and Compose refuses to render without the secrets;
- config JSON input;
- the Helm chart requires a Secret, renders with it, carries no password and sets the security context;
- the Helm values have no default password.

**Deployment smoke test (real stack):** the full stack was built and started with `docker compose -p aw-smoke` (random passwords; an override moved only the host port *numbers*, because the owner's own stack holds the defaults).

```text
python scripts/deploy_smoke_probe.py smoke.env --project aw-smoke ...   (output, credentials masked)
PASS  redis rejects unauthenticated PING  -NOAUTH Authentication required.
PASS  redis accepts the configured password
PASS  redis rejects a default password  -WRONGPASS
PASS  postgres rejects default password  FATAL: password authentication failed
PASS  postgres accepts the configured password
PASS  port 55433/56379/58000/53000 unreachable on LAN address 192.168.1.5   (4 checks)
PASS  api healthy / rejects requests without the key / accepts the key; dashboard serves
PASS  api and frontend run as uid 10001, CapEff 0000000000000000, no-new-privileges   (6 checks)
19/19 checks passed
```

- **Validation tools:** `helm lint` passes; `helm template` without the Secret fails with the `required` message.
- **Environment:** installed Helm 4.2.3 (winget) for these checks.

**⚠ Owner action needed (found during this task):**

- A local Compose stack named `agentwatch`, created from the *old* compose file, is running on this machine. Its Postgres, Redis, API and dashboard are published on `0.0.0.0`.
- A control probe got `+PONG` from its **Redis with no password on the LAN address 192.168.1.5**: anyone on the local network can reach it.
- It was not modified. Recreate it with the new compose file (`docker compose down`, set the passwords in `.env`, then `docker compose up -d`).

**Commits:** `aa234bd` `85258de` `22376f2` `8468857`, plus this ledger commit.

### TO-010

**Status:** DONE. Limits table: "Request and payload limits" in [`docs/PERSISTENCE.md`](PERSISTENCE.md).

**Changes:**

- `panoptis/api/limits.py` (new): `BodySizeLimitMiddleware`, a pure ASGI middleware mounted outermost.
  - It answers 413 from `Content-Length` before the body is read, and cuts off a chunked body once it passes the limit (default 10 MiB).
  - `check_json_limits` is an iterative bound on depth, string size and item count.
- `panoptis/api/v3.py`: `ObservationBatch` caps the batch size (5000) and the depth and string limits (422).
- `panoptis/api/server.py`: v1 `metadata` and tool arguments are bounded (413); `ws_max_size` for `python -m panoptis.api.server`.
- `panoptis/cli/main.py`: `server start` passes `ws_max_size` (1 MiB). `Dockerfile.api` passes `--ws-max-size 1048576`.
- Related finding, fixed: `server start` defaulted to `--host 0.0.0.0`; it now defaults to `127.0.0.1` (CLI surface snapshot updated).
- **Test isolation:** the API's rate limiter is module-global, so a test inherited the earlier tests' budget. `test_schema_validation` started failing with 429 depending on test order; an autouse fixture in `tests/conftest.py` now resets it.

**Tests:** `tests/test_request_limits.py` (14):

- **Middleware:**
  - a declared oversize body gets 413 and the handler never runs;
  - exactly the limit is accepted;
  - a chunked overflow gets 413, and a small chunked body passes;
  - an invalid `Content-Length` is refused.
- **Structural check:** 4 cases, and 50,000-level nesting raises `PayloadTooLarge`, not `RecursionError`.
- **Real API:**
  - an oversized body gets 413 while a small one passes;
  - batch count, depth and string bounds;
  - malformed JSON and a 20,000-level nesting bomb get a 4xx (never 500);
  - v1 metadata is bounded.
- **CLI:** `server start` passes `ws_max_size` from the environment.

**Commands and results:**

- `pytest tests/test_request_limits.py`: 14 passed
- `pytest tests/`: **2508 passed**, 9 skipped, 2 failed (the pre-existing `test_cli_animator` failures on `main`)

**Commits:** `822d8d1` `8eaacca` `0a55eda` and the test-isolation commit, plus this ledger commit.

### Legacy `agentwatch` Docker stack remediation (owner request, 2026-11-02)

**Inspected:** project `agentwatch`, created on 2026-05-22 from the *old* `D:\agentwatch\docker-compose.yml`.

- Postgres: user and db `agentwatch`, the default password `agentwatch`, volume `agentwatch_postgres_data` (7.5 MB, no user tables).
- Redis: no auth and no volume.
- API: old image, running as root.
- All ports were published on `0.0.0.0`; Redis answered unauthenticated `PING` on the LAN address.

**Done, without deleting data or touching other containers:**

1. **Backup:** a full `pg_dumpall` and container-inspect snapshots, saved to `%USERPROFILE%\.agentwatch-legacy\`, outside the repository.
2. **Password rotation:** the Postgres password was rotated in place (`ALTER USER`, with the value passed through the environment).
3. **Recreation:** the stack was recreated from `docker-compose.legacy-hardened.yml` (kept in that folder) with the same images, the same external volume and the same network.
   - Ports bound to loopback.
   - Redis `requirepass`.
   - `no-new-privileges` everywhere; the app containers drop all capabilities.
   - New credentials are in `legacy.env` in that folder.
   - `check_deploy_security.py`: 0 errors, 0 warnings.

**Verification:** 10/10 passed.

- Redis NOAUTH without the password, PONG with it.
- The old Postgres password is rejected; the new one works on the same database.
- Ports 5432, 6379, 8000 and 3000 are unreachable on 192.168.1.5.
- The API and dashboard answer on loopback.
- The volume `agentwatch_postgres_data` (created 2026-05-22) was kept.

Operating and rollback notes: `%USERPROFILE%\.agentwatch-legacy\README.md`.

### TO-011

**Status:** DONE. Matrix: [`docs/ADAPTER_SUPPORT.md`](ADAPTER_SUPPORT.md), generated from `panoptis/protect/support.py`.

**Inventory and classification:** every module in `panoptis/adapters/` is classified (`tests/protect/test_support_matrix.py` fails if one is missing).

- `PRE_EXECUTION_ENFORCEMENT`: `protect_tool()`, `watch(agent, protection=)` on generic agents, the fail-closed legacy gate, and the LangChain, LangGraph, smolagents and AutoGen tool integrations.
- `POST_EXECUTION_OBSERVATION`: the callback and event adapters (LangChain callbacks, LangGraph, AutoGen, smolagents observability, CrewAI, OpenAI Agents SDK, AutoGPT, OpenClaw, generic CLI, and the Claude Code stream-json adapter). Each is labelled "observation only" in code and docs, and a test enforces the label.

**Trace:** `watch()` → `GenericAdapter._wrap` → `enforce_call` for tool-like methods; for framework agents, `watch(agent, protection=)` → `protect_agent` → the framework's execution entry point (`StructuredTool.func/coroutine` or `BaseTool._run/_arun`, `ToolNode.tools_by_name`, `Tool.forward`, `FunctionTool.run_json`). `protect_agent` raises when it finds no tools, so asking for enforcement never silently gives observation only.

**Changes:** `panoptis/protect/integrations.py`, `panoptis/protect/support.py`, `panoptis/core/watcher.py` (`protection=`); streaming (`_inspect_stream` / `_ainspect_stream`) and cancellation recording in `panoptis/protect/pipeline.py`.

**Tests:**

- `tests/protect/test_framework_integrations.py` (26): each test drives the framework's own execution code with a side-effect counter.
  - A blocked call never runs; an allowed call runs once and returns its result.
  - Sanitized output reaches the agent; a failing pipeline blocks.
  - Escalated (approved or rejected) and mutated calls, per framework.
  - `watch(agent, protection=)` on built agents.
- `tests/protect/test_enforce.py`: sync and async contract, tool exceptions propagate unchanged, streamed results sanitized chunk by chunk (sync and async), and cancellation runs the tool once and records the failure.
- `tests/protect/test_support_matrix.py` (4): the matrix covers every adapter module, every enforcement claim names an existing test, observation entries say so, and the docs match the code.

**Commands and results:**

- `pytest tests/protect tests/test_watcher_fail_closed.py`: 147 passed

**Commits:** `bbf9750` `17e96b7` `b555a36` `25fc32a` `2035770` `f872cdf`, plus this ledger commit.

### TO-013, TO-014, TO-015

**Status:** DONE for LangGraph (TO-013), AutoGen (TO-014) and smolagents (TO-015).

**Execution boundary:**

- **LangGraph:** every tool of each `ToolNode`, found in the compiled graph's nodes.
- **AutoGen:** `FunctionTool.run_json`, on the agent's tools and its workbench.
- **smolagents:** `Tool.forward`; `final_answer` is skipped because it has no side effect.

The decision is made before the underlying function runs, and the output is inspected before the framework hands it back to the model.

**Cases tested, per framework, through the framework's own execution path** (`tests/protect/test_framework_integrations.py`):

| Case | LangGraph | AutoGen | smolagents |
|---|---|---|---|
| allowed: runs once, result returned | compiled graph `invoke` | `AssistantAgent.run` (replay model) | `execute_tool_call` |
| blocked: never runs | error ToolMessage, or `ActionBlocked` without error handling | `is_error` tool result | wrapped tool error |
| escalated and approved: runs once after one approval | yes | yes | yes |
| escalated and rejected: never runs | yes | yes | yes |
| mutated: the tool receives redacted arguments | yes | yes | yes |
| failed check: blocked, never runs | yes (via LangChain) | yes | yes |
| sanitized output reaches the agent | yes | yes | yes |

**Preserved semantics:**

- LangGraph: streaming (`graph.stream`) and LangChain tool callbacks still fire once per call.
- LangChain tool schema and name are unchanged.
- smolagents `Tool` metadata (`inputs`) is unchanged.
- smolagents and AutoGen tool exceptions surface exactly as they do unprotected (tested against an unprotected tool).

**Versions tested** (documented in `docs/ADAPTER_SUPPORT.md`): langchain-core 1.2.17, langgraph 1.0.10, smolagents 1.26.0, autogen-agentchat/core/ext 0.7.5.

**Unsupported (documented):**

- LangGraph custom nodes that call functions directly instead of through a `ToolNode`.
- smolagents `CodeAgent` interpreter code that is not a `Tool` call.
- AutoGen 0.2 (`pyautogen`), which is observation only.
- AutoGen MCP workbenches, which go through the MCP gateway (TO-023).

**Commits:** `bbf9750` `17e96b7` `2035770` `f872cdf`, plus this ledger commit.

### TO-012

**Status:** BLOCKED. The implementation and the hook-contract tests are complete. The acceptance criterion, a real Claude Code hook test proving a blocked command did not run, has not passed yet.

**Blocker (needs the owner):** the standalone `claude` CLI on this machine (2.1.229) is not signed in. `claude auth status` reports `"loggedIn": false`, and the E2E runs got `401 Not logged in · Please run /login`. Using an `ANTHROPIC_API_KEY` instead would bill the API, which was not approved, so it was not done. To unblock:

```text
claude                      # then /login, once
set AGENTWATCH_E2E_CLAUDE=1
pytest tests/e2e_claude -m external -v
```

**Changes:**

- `panoptis/protect/claude_hook.py`: the PreToolUse, PostToolUse and PostToolUseFailure handler. Verdicts map to Claude Code's documented hook output:
  - BLOCK → `deny`;
  - ESCALATE_HITL → `ask`;
  - MUTATE_AND_ALLOW → `ask` (or opt-in `allow`) with `updatedInput`;
  - ALLOW → no decision, so the CLI's own permission rules still apply.
  - PostToolUse returns `updatedToolOutput` with the sanitized result; executions and failures are recorded under `call_id` `cc-<tool_use_id>`.
- `panoptis/protect/claude_hook_entry.py`: a stdlib-only entry script, so a broken install exits 2 (block) instead of 1 (which Claude Code treats as non-blocking).
- `ProtectionPipeline.record_execution()`: public, for runtimes that run the tool themselves.
- CLI: `panoptis protect claude-hook install | uninstall | print`, with `--on-failure-block` for Claude Code 2.1.295+.
- `panoptis/adapters/claude_code.py`: a violation read from the stream-json output is now a detection (`policy_violation`, `observation_only`, failed `SAFETY_CHECK` with `detected_after_execution`). Before, it was a `BLOCKED` event and `SAFETY_BLOCK`, although the CLI had already run the tool.
- Docs: `docs/CLAUDE_CODE_HOOK.md` covers installation, the verdict table, failure behaviour, settings, tool-name mapping, limitations and the tested version. The support matrix gains the hook row.

**Tests:**

- `tests/protect/test_claude_hook.py` (23):
  - every verdict mapping, including that an escalation is denied when no approval channel is configured;
  - `updatedInput` keeps the other fields;
  - Write to a blocked path is denied;
  - PostToolUse sanitization keeps the Bash output shape, and execution and output records share the call id;
  - failure recording;
  - the installed command string run through a shell denies and allows;
  - a shadowed, broken `panoptis` package makes the real entry script exit 2;
  - bad input (4 cases), a bad policy and a pipeline exception each exit 2;
  - install merge, idempotence and uninstall; `onFailure` is opt-in; the CLI.
- `tests/test_adapter_claude_code.py`: a stream violation is not a block. This test fails against the previous adapter code.
- `tests/e2e_claude/test_claude_code_hook_e2e.py`: the real CLI. Not yet passing; see the blocker.

**Known limitation, documented:** if the hook command cannot start or times out, Claude Code below 2.1.295 runs the tool anyway. Each hook call is a fresh process, so loop, rate and breaker state does not carry over between calls.

**Commits:** `de4328d` `0f0cb5b` `c976ddb` and the docs/E2E commit, plus this ledger commit.

### TO-016

**Status:** DONE. Docs: [`docs/PROTECT_TOOL.md`](PROTECT_TOOL.md).

**Vulnerabilities found and fixed:** each of these left a caller who asked for enforcement with nothing enforced. Each one is reproduced by a test that failed before its fix.

1. `watch()` caught any instrumentation error and returned the agent **unmodified**, logging a warning. With `protection=` it now raises.
2. A read-only (slotted) tool-like method was skipped at debug level. With `protection=` this is now a `TypeError`.
3. An agent with no tool-like method was watched with nothing enforced. With `protection=` this is now a `ValueError`.

**Changes:**

- `panoptis/core/watcher.py`: the three fixes above.
- `panoptis/protect/pipeline.py`:
  - `ProtectionConfig(mode="observe")`: decided and recorded, but the call runs with its original arguments and unsanitized output.
  - It is visible in three places: `mode` / `would_block` in decision records, `status()["mode"]`, and a warning at creation.
  - The kill switch and suspensions still block; an unknown mode is refused.
  - Execution records say whether the arguments that actually ran were mutated.
- `panoptis/protect/enforce.py`: nested protected calls record `parent_call_id` through a context variable.
- `panoptis/protect/registry.py`: `ProtectedToolRegistry`. Every registration path (item assignment, `update`, `setdefault`, `register`) protects the tool; framework tools are protected in place; protection is idempotent.
- `docs/PROTECT_TOOL.md`: the contract, identity, nested tools, runtime registration, double wrapping, `watch()` refusals, and observe mode.

**Tests:**

- `tests/protect/test_generic_and_direct.py` (15): observe mode (sync and async, records, visibility, no approval wait, kill switch still blocks, unknown mode refused), every registry path, idempotent registration, framework tools in the registry, `watch()` refusals, watching twice, wrapper failure blocks, and tool exceptions pass through.
- `tests/protect/test_protect_tool.py` (12): decorator forms, blocked never runs, async, mutation mapped back to positional and keyword arguments, double wrap, context identity, exceptions, varargs, `require_context`, and nested sync and async parent ids. The nested tests fail without the change.
- `tests/protect/test_enforce.py`: the shared boundary contract (sync/async, streaming, cancellation, approvals).

**Commands and results:**

- `pytest tests/protect/test_generic_and_direct.py`: 15 passed
- `pytest tests/` at `5d5de77`: **2582 passed**, 9 skipped, 2 failed (the pre-existing `test_cli_animator` failures on `main`, scheduled for TO-032)

**Commits:** `ef8b62e` `284f958` `7159e6a` `1c7ebd1` `a32d908` `5d5de77`, plus this ledger commit.

### TO-017

**Status:** DONE. Docs: [`docs/THREAT_ANALYSIS.md`](THREAT_ANALYSIS.md).

**Method:** I probed the analyser with bypass-oriented inputs for each family before changing anything. Every probe below was allowed, or produced no finding, on the previous code:

| Input | Before | Now |
|---|---|---|
| `sudo su -`, `sudo -i` | **command discarded**: the wrapper consumed the whole argv | `PRIVILEGE_ESCALATION`, escalate |
| `x=rm; $x -rf /` | no finding | resolved: `FS_DELETE_CRITICAL`, block |
| `$CMD …` (unresolvable) | no finding | `DYNAMIC_COMMAND`, escalate |
| `echo <b64> \| base64 -d \| sh` | no finding | decoded: `FS_DELETE_CRITICAL`, block |
| `chmod 4755` / `g+s` | no finding (numeric) | `SETUID_BIT`, block |
| `usermod -aG sudo\|wheel`, `tee …/sudoers` | no finding | `PRIVILEGE_GRANT`, escalate |
| `printenv \| curl -d @- …`, `env \| nc …` | no finding | `EXFIL_ENVIRONMENT`, block |
| `os.remove('/etc/passwd')` (Python) | `PY_DELETE`, allow | `PY_DELETE_SYSTEM`, escalate |
| metadata as `2852039166`, `0xa9fea9fe`, octal, `::ffff:`-mapped | allow or no finding | canonicalised: `SSRF_METADATA`, block |
| `file:///etc/hosts` on a fetch tool | no finding (no host, skipped) | `LOCAL_FILE_URL`, escalate |

**Also added:**

- `THREAT_RULES_VERSION`, a content hash of the rule code, on every decision next to the RBAC `policy_version`.
- Tests that a crashing detector fails closed, and that `fail_closed=False` still cannot authorise unanalysed arguments (`NO_PROFILE`).
- `docs/THREAT_ANALYSIS.md`: parsing per content type, the detector-family table with default verdicts (each one verified against the code), the risk-score formula, evidence fields, failure behaviour, and the false-positive policy with its known limits.

**Tests:** `tests/protect/test_threat_analysis.py` (71).

- 18 attack cases and 10 benign cases.
- 17 bypass cases, each asserting the code and the verdict, and that every finding explains itself.
- 11 benign near-misses that must neither block nor escalate.
- 7 host-canonicalisation cases and the unassigned-variable case.
- Rule-version evidence, and the crashing detector.

24 of these fail on the previous analyser.

**Commands and results:**

- `pytest tests/protect/test_threat_analysis.py`: 71 passed
- `pytest tests/protect tests/test_watcher_fail_closed.py tests/test_adapter_claude_code.py tests/test_safety*.py`: 337 passed

**Commits:** `fa9e317` `5cc9dae` `ce2054d`, plus this ledger commit.
