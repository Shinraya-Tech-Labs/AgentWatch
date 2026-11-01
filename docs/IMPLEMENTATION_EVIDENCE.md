# Implementation Evidence Ledger

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
| TO-004 … TO-040 | NOT STARTED unless listed below | — |

## Pre-task work already on the branch

| Commit | Change | Tests |
|---|---|---|
| `3a8897a` … `9cb58bd` | `agentwatch/protect/`: model, argument analysis, threat classifier, output inspector, loop detector, intent grounding, RBAC, ledger, control/approvals, pipeline | `pytest tests/protect`: 55 passed (threat analysis and output inspection only; the other modules have no tests yet) |
| `6d6bb4d` | `docs/REPO_AUDIT.md` | n/a |

## Task log

<!-- one section per task, appended as work completes -->

### TO-001

**Status:** DONE

**Root cause:** in `GenericAdapter._wrap`, both the sync and async safety gates caught every exception from the safety check, logged it at debug level and ran the tool. Two further fail-open paths were also found:

- a `SafetyEngine` that failed to construct made `watch()` return the agent unwrapped;
- a malformed verdict (for example a non-bool `blocked`) was read as "not blocked".

**Changes:** `agentwatch/core/watcher.py`

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

- **`agentwatch/protect/enforce.py`:**
  - `enforce_call` / `aenforce_call`: the single boundary. Order: inspect and decide → approval or mutation → execute once → inspect output → return. The tool's own exceptions pass through unchanged. A failure inside enforcement raises `ActionBlocked`, and a failure after execution withholds the result and records `executed: true`.
  - `protect_tool()`: the public API.
  - `protection_context()` and `current_context()` carry identity.
  - Default pipeline: `set_default_pipeline` / `get_default_pipeline`.
  - `bind_arguments`: maps mutated values back to positional, keyword and variadic parameters.
- **`agentwatch/protect/model.py`:**
  - `ToolAction` carries `tenant_id`, `trace_id`, `parent_call_id` and `defaulted_identity`.
  - `scope` (tenant/session) keys all session state.
  - Decisions record `mutation.changed_paths`.
- **`agentwatch/protect/pipeline.py`:**
  - Per-tenant sessions, loop windows and breakers.
  - `failure_decision`.
  - `protect.execution` evidence: outcome, duration, error type, whether arguments were mutated.
  - Evidence is recorded under the action's tenant, with the trace and parent ids declared.
  - `require_context` blocks calls that carry no supplied identity.
- **`agentwatch/protect/ledger.py`:** `record(..., tenant_id=)` and `KIND_EXECUTION`.
- **`agentwatch/core/watcher.py`:** `watch(agent, protection=pipeline)` and `GenericAdapter(protection=)` send tool-like calls through `enforce_call` / `aenforce_call`.
- **`agentwatch/core/http_forwarder.py` (performance fix found while testing):**
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

- `agentwatch/api/tenancy.py`: a pure resolver.
  - Single tenant → `default`.
  - `AGENTWATCH_TENANT_KEYS` key map → the key's tenant.
  - Cloud → the tenant store's key.
  - A header is only a selector: mismatch gets 403; no or invalid key in cloud mode gets 401, never a fallback.
- `agentwatch/api/server.py`:
  - `_principal`, `_request_tenant`, `_owner`, `_require_session_owner`, `_require_operator`;
  - v1 ingestion tagging and session guards;
  - 10 session routes scoped, and the session list filtered;
  - 14 operator-only routes, plus the tenant-record rule;
  - WebSocket authentication and per-tenant filtering;
  - the tee is tenant-aware;
  - `_key_matches` accepts mapped keys, compared in constant time.
- `agentwatch/tracing/collector.py`: a cross-tenant append to an existing session is dropped under the lock (race-safe).
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
