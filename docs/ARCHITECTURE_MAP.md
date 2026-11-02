# Architecture map

What runs, from where, and what every module is for (TO-034). Generated evidence: `python scripts/module_map.py` (the import graph from the entry points, including lazy imports and modules named in strings) and `python scripts/classify_modules.py`. `tests/test_architecture.py` keeps this page honest: it fails if any module becomes unreachable, or if a module without a runtime entry point is missing from the table below.

## Entry points

| Entry point | What | Reaches |
|---|---|---|
| `panoptis` console script → `panoptis.cli.main` | 120+ commands (`tests/cli_surface.txt`); the protection commands are in [PROTECT_CLI.md](PROTECT_CLI.md) | CLI, protect, v3 engine, MCP server, gateway, discovery, controls, evidence |
| `panopti` console script → `panopti.cli` | the Panopti guard CLI (themes, bench, replay, doctor, playground) | `panopti.*` and the `panoptis` engines it wraps |
| `python -m panoptis` / `python -m panopti` | the same CLIs | — |
| `panoptis.api.server:app` (uvicorn, `Dockerfile.api`) | 109 operations on 102 paths: `/api/v1/*` (sessions, events, safety, tenants, GDPR, dashboard), `/api/v1/protect/*` (25: [PROTECT_API.md](PROTECT_API.md)), `/api/v3/*` (v3 evidence engine), `/v1/traces` (OTLP), `/health`, `/metrics` | API, tenancy, protect routers, v3 engine |
| `panoptis.tasks` (Celery, `Dockerfile.worker`) | sealing, alerting, retention tasks | storage, alerting |
| `panoptis.protocol.mcp_server` (`panoptis mcp`) | AgentWatch's own MCP server (safety checks, run queries) | core safety, v3 |
| `python -m panoptis.protect.mcp_gateway` | the enforcing MCP gateway ([MCP_GATEWAY.md](MCP_GATEWAY.md)) | protect pipeline |
| `panoptis/protect/claude_hook_entry.py` | the Claude Code PreToolUse/PostToolUse hook ([CLAUDE_CODE_HOOK.md](CLAUDE_CODE_HOOK.md)) | protect pipeline |
| `panoptis/evidence/verifier.py` | the standalone evidence verifier (stdlib only) | nothing (by design) |
| library: `panoptis.protect`, `panoptis.instrument`, `panoptis.core.watcher.watch`, framework integrations | the SDK | protect pipeline, sensors |
| dashboard (`frontend/`) | `/security/*` → `/api/v1/protect/*` ([DASHBOARD.md](DASHBOARD.md)); the v3 views → `/api/v3/*` | via the `/api/v1` and `/api/v3` proxies |

Integrations and what each enforces: [ADAPTER_SUPPORT.md](ADAPTER_SUPPORT.md) (`panoptis protect support`).

## Dependency direction

- **One decision flow.** Every enforcing integration calls `panoptis.protect` (`enforce_call` / `aenforce_call` / `pipeline.evaluate`). `tests/protect/test_single_boundary.py` checks it for every enforcing row of the support matrix.
- **The legacy gate sits inside the pipeline.** The SafetyEngine gate of `watch(agent)` without `protection=` is the pipeline's threat stage, not a second engine.
- **Layering.** `protect` depends on `core.safety` (legacy patterns), `evidence`/`storage` (ledger) and `security.owasp` (labels). Nothing in `protect` depends on the API, CLI or dashboard. The API and CLI depend on `protect`.
- **Evidence.** All evidence goes through `storage.store.Store` (append-only, sealed). The standalone verifier depends on nothing.

## Modules without a runtime entry point

Reachable only from tests: library code that no CLI command, API route, worker or SDK entry point uses.

| Category | Meaning | Modules | Lines |
|---|---|---|---|
| library API (documented) | public, documented, tested; for direct import | 3 | 820 |
| internal / re-export | support code of a runtime module | 2 | 69 |
| legacy (superseded) | kept for compatibility; the enforcing replacement is named | 7 | 806 |
| deprecated shim | old import path, warns | 2 | 29 |
| experimental | tested, **not part of the supported surface**, no runtime entry point; candidates to wire or retire per [v3/MIGRATION_MAP.md](v3/MIGRATION_MAP.md) | 65 | 8,139 |

**Removed in TO-034:**

- `security/encryption.py` (202 lines) and `security/key_storage.py` (58): unused and untested. The ADR that credited them with per-tenant encryption was corrected; that is `evidence/crypto.py` and the store.
- `telemetry/ingestion.py` (217): unused and untested. It duplicated the API's tenant-aware ingestion.

**Wired in TO-034:**

- `protect/support.py`, as `panoptis protect support`;
- the `__main__` modules, as entry points.

Not removed: experimental and legacy modules are tested and may be imported by users. A static scan cannot see external imports, so they are classified, not deleted.

| Module | Lines | Test files | Category | Purpose / evidence |
|---|---|---|---|---|
| `panoptis.adapters.base` | 62 | 1 | internal | base class of the observation adapters |
| `panoptis.core.loop_detector` | 93 | 1 | legacy (superseded) | earlier loop detector; enforcement uses `protect.loops` (docs/LOOP_DETECTION.md) |
| `panoptis.core.recursion_depth_detector` | 78 | 1 | legacy (superseded) | superseded by `protect.loops` and the circuit breaker |
| `panoptis.core.risk` | 101 | 1 | experimental | risk-score helpers; enforcement uses `protect.threats` |
| `panoptis.cost.anomaly` | 74 | 1 | experimental | cost analytics and routing; no runtime entry point |
| `panoptis.cost.caching` | 13 | 1 | deprecated shim | alias of `cost.semantic_cache`; warns on import (MIGRATION_MAP: deprecate) |
| `panoptis.cost.comparator` | 80 | 2 | experimental | cost analytics and routing; no runtime entry point |
| `panoptis.cost.complexity_router` | 213 | 1 | experimental | cost analytics and routing; no runtime entry point |
| `panoptis.cost.governance` | 189 | 2 | experimental | cost analytics and routing; no runtime entry point |
| `panoptis.cost.predictor` | 97 | 1 | experimental | cost analytics and routing; no runtime entry point |
| `panoptis.cost.roi` | 100 | 1 | experimental | cost analytics and routing; no runtime entry point |
| `panoptis.cost.router` | 16 | 1 | deprecated shim | alias of `infrastructure.router`; warns on import |
| `panoptis.evidence.shredding` | 169 | 1 | library API (documented) | per-subject encrypted witnesses: docs/v3/WITNESS_ATTESTATION.md |
| `panoptis.governance.causal` | 108 | 2 | experimental | governance reports; not exposed by API/CLI |
| `panoptis.governance.iso42001` | 131 | 1 | experimental | governance reports; not exposed by API/CLI |
| `panoptis.governance.reports` | 134 | 1 | experimental | governance reports; not exposed by API/CLI |
| `panoptis.governance.residency` | 92 | 1 | experimental | governance reports; not exposed by API/CLI |
| `panoptis.hitl` | 31 | 2 | legacy (superseded) | earlier HITL engine; tool-call approvals are `protect.approvals` (docs/APPROVALS.md) |
| `panoptis.hitl.decisions` | 102 | 1 | legacy (superseded) | earlier HITL engine; tool-call approvals are `protect.approvals` (docs/APPROVALS.md) |
| `panoptis.hitl.feedback` | 154 | 1 | legacy (superseded) | earlier HITL engine; tool-call approvals are `protect.approvals` (docs/APPROVALS.md) |
| `panoptis.hitl.orchestrator` | 295 | 1 | legacy (superseded) | earlier HITL engine; tool-call approvals are `protect.approvals` (docs/APPROVALS.md) |
| `panoptis.hitl.thresholds` | 53 | 0 | legacy (superseded) | earlier HITL engine; tool-call approvals are `protect.approvals` (docs/APPROVALS.md) |
| `panoptis.infrastructure` | 1 | 1 | experimental | model routing; no runtime entry point |
| `panoptis.infrastructure.config` | 51 | 1 | experimental | model routing; no runtime entry point |
| `panoptis.infrastructure.router` | 176 | 1 | experimental | model routing; no runtime entry point |
| `panoptis.lattice` | 29 | 3 | experimental | research prototypes |
| `panoptis.lattice.attention_scatter` | 198 | 1 | experimental | research prototypes |
| `panoptis.lattice.shadow_filesystem` | 200 | 1 | experimental | research prototypes |
| `panoptis.memory.causal_graph` | 130 | 3 | experimental | memory analytics; the engine itself is runtime (GDPR erasure) |
| `panoptis.memory.graph_query` | 403 | 1 | experimental | memory analytics; the engine itself is runtime (GDPR erasure) |
| `panoptis.memory.health` | 118 | 1 | experimental | memory analytics; the engine itself is runtime (GDPR erasure) |
| `panoptis.memory.identity` | 87 | 1 | experimental | memory analytics; the engine itself is runtime (GDPR erasure) |
| `panoptis.memory.resolver` | 76 | 1 | experimental | memory analytics; the engine itself is runtime (GDPR erasure) |
| `panoptis.memory.temporal_decay` | 58 | 1 | experimental | memory analytics; the engine itself is runtime (GDPR erasure) |
| `panoptis.memory.visualization` | 93 | 1 | experimental | memory analytics; the engine itself is runtime (GDPR erasure) |
| `panoptis.monitoring.context_guard` | 363 | 1 | experimental | context guard |
| `panoptis.orchestration.consensus` | 84 | 2 | experimental | multi-agent analysis (DAG, deadlock, consensus, Shapley); no runtime entry point |
| `panoptis.orchestration.crew_context` | 86 | 1 | experimental | multi-agent analysis (DAG, deadlock, consensus, Shapley); no runtime entry point |
| `panoptis.orchestration.dag` | 119 | 1 | experimental | multi-agent analysis (DAG, deadlock, consensus, Shapley); no runtime entry point |
| `panoptis.orchestration.deadlock` | 52 | 1 | experimental | multi-agent analysis (DAG, deadlock, consensus, Shapley); no runtime entry point |
| `panoptis.orchestration.propagation` | 57 | 1 | experimental | multi-agent analysis (DAG, deadlock, consensus, Shapley); no runtime entry point |
| `panoptis.orchestration.race_condition` | 134 | 1 | experimental | multi-agent analysis (DAG, deadlock, consensus, Shapley); no runtime entry point |
| `panoptis.orchestration.shapley` | 76 | 1 | experimental | multi-agent analysis (DAG, deadlock, consensus, Shapley); no runtime entry point |
| `panoptis.orchestration.spawning` | 80 | 1 | experimental | multi-agent analysis (DAG, deadlock, consensus, Shapley); no runtime entry point |
| `panoptis.orchestration.trust` | 61 | 1 | experimental | multi-agent analysis (DAG, deadlock, consensus, Shapley); no runtime entry point |
| `panoptis.platform.cloud` | 105 | 1 | experimental | cloud/platform prototypes |
| `panoptis.platform.eval_builder` | 90 | 1 | experimental | cloud/platform prototypes |
| `panoptis.platform.intelligence` | 176 | 1 | experimental | cloud/platform prototypes |
| `panoptis.platform.prompts` | 140 | 1 | experimental | cloud/platform prototypes |
| `panoptis.plugins` | 6 | 2 | experimental | plugin registry and sandbox; no loader wired |
| `panoptis.plugins.registry` | 102 | 1 | experimental | plugin registry and sandbox; no loader wired |
| `panoptis.plugins.sandbox` | 155 | 1 | experimental | plugin registry and sandbox; no loader wired |
| `panoptis.protocol.badge` | 81 | 1 | experimental | protocol schema/badge/benchmark helpers; the MCP server is runtime |
| `panoptis.protocol.benchmark` | 104 | 1 | experimental | protocol schema/badge/benchmark helpers; the MCP server is runtime |
| `panoptis.protocol.schema_v1` | 132 | 1 | experimental | protocol schema/badge/benchmark helpers; the MCP server is runtime |
| `panoptis.reasoning.adversarial` | 97 | 1 | experimental | reasoning-quality analytics (drift, calibration, trust); no runtime entry point |
| `panoptis.reasoning.benchmark` | 148 | 1 | experimental | reasoning-quality analytics (drift, calibration, trust); no runtime entry point |
| `panoptis.reasoning.calibration` | 109 | 1 | experimental | reasoning-quality analytics (drift, calibration, trust); no runtime entry point |
| `panoptis.reasoning.dual_eval` | 73 | 1 | experimental | reasoning-quality analytics (drift, calibration, trust); no runtime entry point |
| `panoptis.reasoning.explainer` | 88 | 1 | experimental | reasoning-quality analytics (drift, calibration, trust); no runtime entry point |
| `panoptis.reasoning.goal_drift` | 86 | 1 | experimental | reasoning-quality analytics (drift, calibration, trust); no runtime entry point |
| `panoptis.reasoning.quality` | 117 | 1 | experimental | reasoning-quality analytics (drift, calibration, trust); no runtime entry point |
| `panoptis.reasoning.semantic_drift` | 92 | 2 | experimental | reasoning-quality analytics (drift, calibration, trust); no runtime entry point |
| `panoptis.reasoning.trust_score` | 132 | 1 | experimental | reasoning-quality analytics (drift, calibration, trust); no runtime entry point |
| `panoptis.runtime.killswitch` | 7 | 1 | internal re-export | re-exports `panopti.killswitch` under `panoptis.runtime` |
| `panoptis.scoring.silence` | 143 | 1 | experimental | silence scoring |
| `panoptis.security.exfiltration` | 125 | 2 | experimental | security analytics (report, sandbox, exfiltration); enforcement is `protect` |
| `panoptis.security.report` | 125 | 1 | experimental | security analytics (report, sandbox, exfiltration); enforcement is `protect` |
| `panoptis.security.sandbox` | 153 | 1 | experimental | security analytics (report, sandbox, exfiltration); enforcement is `protect` |
| `panoptis.sensors.langchain` | 418 | 3 | experimental | sensors without a CLI/API entry point |
| `panoptis.sensors.mcp_error` | 74 | 1 | experimental | sensors without a CLI/API entry point |
| `panoptis.telemetry.collector` | 271 | 1 | experimental | telemetry collector library |
| `panoptis.tracing.audit` | 136 | 1 | experimental | trace analytics; the API uses `telemetry` and v3 |
| `panoptis.tracing.live` | 149 | 1 | experimental | trace analytics; the API uses `telemetry` and v3 |
| `panoptis.tracing.sampling` | 128 | 1 | experimental | trace analytics; the API uses `telemetry` and v3 |
| `panoptis.tracing.spans` | 186 | 1 | experimental | trace analytics; the API uses `telemetry` and v3 |
| `panoptis.tracing.trajectory` | 147 | 1 | experimental | trace analytics; the API uses `telemetry` and v3 |
