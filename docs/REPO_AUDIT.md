# AgentWatch Repository Audit

> **Package rename (2026-11-02):** `main` renamed the package `agentwatch` → `panoptis` (commit `db95c31`). This branch was merged onto that layout in `4c2025d`; This audit is a snapshot of `8d5cf12` and is kept as written. File paths below that start with `agentwatch/` now live under `panoptis/`.

**Date:** 2026-11-01 · **Commit audited:** `main` @ `8d5cf12` · **Auditor:** Claude (automated checks + targeted manual review)

Not audited: the untracked work in progress on `feat/protect-runtime-security` (`agentwatch/protect/`, `tests/protect/`) and the uncommitted edit to `PROJECT_TASKS_TO_BE_DONE.md`.

## Method and limits

The audit ran every check available locally and read the security-sensitive paths in full: API auth and tenancy, erasure, enforcement wrappers, adapters, CI/release workflows and Compose. Each finding below was checked against the code; findings already in `PROJECT_TASKS_TO_BE_DONE.md` are cross-referenced by their `TO` id.

**Not done:**

- This is not a line-by-line review of all ~92 k Python lines.
- `bandit`, `pip-audit` and `semgrep` are not installed locally and were not installed for this audit. A manual pattern sweep stood in for them.
- The landing app received only `npm audit`.
- Helm, Fly, Render and Railway configs were not reviewed.

## Baseline

| Check | Result |
|---|---|
| Python tests (`pytest tests/`, Python 3.14, Windows) | **2226 passed, 8 skipped, 0 failed** (8 m 41 s) |
| Coverage, code on `main` | **81.1 %** overall (`agentwatch` 80.7 %, CI gate 70 %; `panopti` 88 %) |
| `ruff check .` (whole repo) | **49 findings**, all outside `agentwatch/` |
| `ruff check agentwatch/` (what CI runs) | clean on `main` |
| `ruff format --check .` | 13 files would be reformatted (`panopti/`, tests, 1 notebook) |
| mypy (`make typecheck` scope) | clean |
| Frontend: `tsc --noEmit` · `next lint` · Jest | clean · clean · **22/22 passed** |
| `npm audit --omit=dev`, frontend | 1 critical, 3 high |
| `npm audit --omit=dev`, landing | 1 critical, 4 high, 1 moderate |
| Dangerous-pattern sweep of shipped code (`shell=True`, `os.system`, `eval`/`exec`, pickle, `yaml.load`, `verify=False`, f-string SQL) | **none found** |

## Findings

Severity: **Critical**, a security control demonstrably does not hold; **High**, data isolation, a misleading security claim, or the release path; **Medium**, hardening; **Low**, hygiene.

### Critical

**C1 — The generic `watch()` adapter fails open when the safety check errors.**
`agentwatch/core/watcher.py:377` (async) and `:469` (sync). An exception raised inside the safety evaluation is caught, logged at **debug** level ("Safety check failed (suppressed)"), and the tool call then runs. Any bug, bad custom regex or unexpected argument type in the safety engine silently disables enforcement for that call.
*Fix:* in both branches, replace the swallow with `raise AgentWatchBlockedError(...)`. Add a test in which the safety engine raises and the wrapped tool must not run.

**C2 — The Claude Code adapter's "block" happens after the tool has run.**
`agentwatch/adapters/claude_code.py:378-400`. The adapter launches `claude -p … --output-format stream-json` (`:415-441`) and reads the event stream. Claude Code runs its own tools, so by the time a `TOOL_CALL` event is read, nothing can stop it. The code marks the event blocked and emits `SAFETY_BLOCK`, but there is no PreToolUse hook and no permission gate. The comment "skip execution" is wrong; this path is detection only.
*Fix:* enforce through a Claude Code `PreToolUse` hook (or permission mode) that consults the safety engine before the tool runs, or document this adapter as detection-only and stop reporting "blocked".

**C3 — Session-scope GDPR erasure ignores the tenant (TO1, confirmed).**
`agentwatch/api/server.py:1797-1804`. The tenant filter is added to `where`, but the `SESSION_ID` branch calls `delete_events_by_session(identifier)` and `delete_session(identifier)` without it. In cloud mode a tenant can erase another tenant's session by id. Lines 1810-1811 compute values and discard them (`_ = …`), which suggests unfinished tenant handling.

**C4 — v3 tenant is caller-selected (TO2, confirmed).**
`agentwatch/api/server.py:685-696`. Outside cloud mode `_v3_tenant` returns the `X-Tenant-ID` header verbatim. In cloud mode it also falls back to the header if the key does not resolve to a tenant. `_require_api_key` rejects invalid keys first, so the fallback is reached only if the two disagree. That defence depends on two functions staying consistent.
*Fix:* single-tenant mode always uses `default`; cloud mode uses only the key's tenant.

### High

**H1 — Attestation seals use a public default HMAC key (TO17, confirmed).**
`panopti/attest.py:88` and `:127` default `secret_key="panopti-seal-default-key"`. Anyone can forge a seal that verifies under the default.
*Fix:* require a key (env or argument) and refuse to seal or verify without one.

**H2 — README security claims exceed the implementation.**

- *Contradiction:* `README.md:50` says v3 "does not block or control" agents, while `:92`, `:129`, `:143` and `:201` say every action is intercepted before it executes and that this "wraps any agent framework".
- *What actually blocks:*
  - the generic adapter (subject to C1);
  - the `SafetyEngine` API;
  - the `PanoptiGuard` decorator.
- *What only observes:* the LangChain, LangGraph, AutoGen and smolagents adapters (they emit events but never gate a tool), and the Claude Code adapter (C2).
- *Pattern count:* `:227` and `:333` claim "40+ dangerous patterns". There are **15** built-in patterns (5 critical, 6 high, 4 medium), of which **5** block by default, plus 4 evasion-resistant detector functions.

*Fix:* state per integration whether it enforces or only observes, and use the real numbers.

**H3 — Releases publish without tests, from two overlapping workflows (TO7, confirmed and extended).**

- `publish-pypi.yml` runs on any `v*` tag. It builds, runs `twine check` and publishes to PyPI. It runs no tests and does not require green CI.
- `release.yml` also runs on version tags and creates a GitHub Release. Its "Build and Test" job is an `echo`.
- Both workflows create a release.

*Fix:* one release workflow that runs the test, lint and build jobs, or `needs:` a CI run on the tagged SHA, before publishing. Give `contents: write` and `id-token` only to the publish job.

**H4 — Compose publishes every service on all interfaces (TO6, confirmed and extended).**
`docker-compose.yml` publishes Postgres `5432` (fixed password `agentwatch`), **Redis `6379` with no authentication**, the API, the frontend and the Jaeger ports, all on `0.0.0.0`. Redis is the larger exposure: anyone who reaches the host can read or write the Celery broker and cache.
*Fix:* bind to `127.0.0.1`, or drop host publishing for postgres and redis (only the api needs them, on the compose network); require a Redis password.

**H5 — Cloud tenants and API keys live only in memory (TO3, confirmed).**
`agentwatch/api/tenant_auth.py:33-34` stores tenants and keys in plain dicts. They are lost on restart and inconsistent across replicas. This module, the cloud-mode authentication path, has only **19 %** test coverage.

**H6 — The API silently runs non-durable in production (TO4, confirmed).**
`agentwatch/api/server.py:644-649`: an `init_db()` failure logs a warning and continues in memory, and `/health` still reports healthy.

**H7 — Unauthenticated policy oracle.**
`POST /api/v1/safety/check` (`agentwatch/api/server.py:1247`) is one of only three routes without auth. The other two are `/health` and `/metrics`. It is stateless, but it returns which policy patterns match, including the deployment's custom patterns. That lets anyone who can reach the API probe the rules for gaps, and run regex evaluation at will.
*Fix:* require the API key (the dashboard proxy already attaches it), or strip pattern detail from unauthenticated responses.

**H8 — The dashboard's Next.js has known advisories.**
`frontend/package.json` pins `next` `14.2.30`. `npm audit` lists DoS (Server Components), middleware/redirect SSRF, cache-poisoning and request-smuggling advisories. Several are fixed within 14.x (≤ 14.2.35); others need 15.5.x. Some affect only features the dashboard does not use (App Router, `next/image` remote patterns).
*Fix:* move to `14.2.35` now (no API change), and plan 15.x. The landing app (`next` 16.2.6) also reports one critical and four high advisories (`next`, `sharp`, `postcss`, `nanoid`).

### Medium

| # | Finding | Evidence | Fix |
|---|---|---|---|
| M1 | CI lints and measures coverage only for `agentwatch/`. `panopti/`, shipped in the same wheel (`pyproject.toml: packages = ["agentwatch", "panopti"]`), is never linted; 49 ruff findings and 13 unformatted files have accumulated outside the lint scope. | `ci.yml:26-28` | Lint `agentwatch panopti tests`; add `--cov=panopti` |
| M2 | Dependency vulnerability scanning cannot fail CI. | `ci.yml:273-274` (`pip-audit … continue-on-error: true`) | Make it blocking, with an allowlist for accepted advisories |
| M3 | `jsonschema` is imported but not declared (TO19); `api/server.py` imports the validator. | `agentwatch/validation/schema_validator.py:11` | Add it to `dependencies` |
| M4 | Memory erasure target imports a function that does not exist (TO18), so right-to-erasure always reports partial failure. | `server.py:1849` imports `drop_memory_entries`; no such definition in the package | Implement it or remove the target |
| M5 | Release script interpolates the tag-derived version into shell (TO5). Only tag pushers control it. | `publish-pypi.yml:40`, `:64` | Pass it through `env:` and validate it with a regex |
| M6 | Fork PRs control both the target PR number and the outcome strings of the PR test report comment, so a fork can post or overwrite the report on a different PR. Comment spoofing only; the workflow runs no fork code. | `report-on-pr.yml` reads `pr_results/*.txt` from the fork's artifact when `pull_requests` is empty | Accept only `success`/`failure`; take the PR number from `workflow_run.head_sha` via the API, not from the artifact |
| M7 | No request body size limit; an authenticated client can POST arbitrarily large observation batches. | no limit in `agentwatch/api` | Add a `Content-Length` cap middleware, or enforce it at the proxy |
| M8 | Security-relevant code has low coverage: Claude Code adapter 19 %, `api/tenant_auth.py` 19 %, `rollback/engine.py` 32 %, `api/server.py` 58 %. | coverage report | Add tests first to the auth and enforcement paths |
| M9 | `/metrics` is unauthenticated. Common for Prometheus, but it exposes internal counters to anyone who can reach the API. | `server.py:849` | Bind it to an internal port or put it behind the key |

### Low

| # | Finding | Evidence |
|---|---|---|
| L1 | Duplicated subsystems with overlapping responsibilities. | Two loop detectors (`core/loop_detector.py`, used only by tests, and `circuit_breaker/loop_detector.py`); two secret redactors with different patterns (`security/redaction.py`, `evidence/redaction.py`); two OTel modules and two collectors (`tracing/`, `telemetry/`); three decay modules; three drift modules |
| L2 | Two-way dependency between packages: `panopti` imports `agentwatch` in 21 places, and `agentwatch` re-exports `panopti` modules. | `agentwatch/security/shield.py`, `security/egress.py`, `runtime/killswitch.py` |
| L3 | 54 modules (~6,500 lines) are importable, but no CLI command, API route or adapter reaches them. 4 are not even tested. | Untested: `security/encryption.py` (202 lines), `telemetry/ingestion.py` (217), `cost/caching.py`, `cost/router.py`. Tested-only examples: `orchestration/*`, most of `reasoning/*`, `tracing/{audit,live,sampling,spans,trajectory}.py`, `sensors/mcp.py` |
| L4 | Some workflows reference third-party actions by tag rather than commit SHA (others already pin SHAs). | `ci.yml`, `publish-pypi.yml` (`softprops/action-gh-release@v2`), `labeler.yml` |
| L5 | The local Python environment contains a broken `~gentwatch-ai` distribution (a leftover from an interrupted install). | `pip check` warning; environment-only, not a repo defect |

## What is in good shape

- **Test suite:** large and green, with real coverage (81 %) above the 70 % gate.
- **Typing:** mypy clean over the v3 modules.
- **Frontend:** type-check, lint and Jest all clean.
- **Dangerous code patterns:** none of the classic ones appear in shipped code.
- **API authentication:**
  - 35 of 38 routes require the API key, compared in constant time;
  - WebSocket auth rejects with close code 4001;
  - CORS is an explicit allow-list;
  - a rate limiter is in place.
- **CI workflow hygiene:**
  - `labeler.yml` uses `pull_request_target` without checking out PR code;
  - `release.yml` defaults to `permissions: {}`;
  - PR tests run read-only.
- **Evidence store:** append-only, enforced by database triggers, with Merkle sealing, retention and crypto-shredding all tested (see `docs/v3/RELEASE_READINESS.md`).

## Suggested order of work

1. **C1, C3, C4, H1:** small code changes that close real holes (fail-open, cross-tenant delete, tenant spoofing, forgeable seals).
2. **H4, H7, H8:** deployment exposure. Bind the Compose ports, put auth on the safety oracle, and bump Next.js to 14.2.35.
3. **H3, M5:** a single tested release path, before the next tag.
4. **C2, H2:** decide the Claude Code enforcement model (a `PreToolUse` hook), then make the README match per-integration reality.
5. **H5, H6, M1–M4:** durability and CI scope.
6. **The rest.**
