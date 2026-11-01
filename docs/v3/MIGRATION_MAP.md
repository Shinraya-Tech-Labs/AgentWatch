# AgentWatch v0.2 → v3 Migration Map

Status: M0-M2 done, M3 done for the UI, M4 merged (packaging split and release tag pending), M5 pending (see section 4)

Classification key:

| Label | Meaning |
|---|---|
| **KEEP** | Used in v3 as-is, or with cosmetic changes (a move or rename) |
| **ADAPT** | Logic reused. Interface changed to fit the v3 domain model. |
| **REWRITE** | The capability is needed in v3, but the implementation cannot carry v3 semantics. It is replaced, and the old code stays as reference. |
| **DEPRECATE** | Not v3 core. Moved to `panoptis/legacy/`, kept importable with a `DeprecationWarning`, maintained only for fixes, and removed after a published deprecation window. Some become **benchmark baselines** or **candidate motif detectors**. |
| **REMOVE** | Dead, duplicated, accidental, or actively misleading. Deleted once references are gone. |

The principle behind every row is that v3 core never depends on anything below KEEP/ADAPT. A DEPRECATE module can survive for a long time without distorting the architecture, because it lives behind the `legacy/` boundary.

---

## 1. Salvage map

### 1.1 Core

| Module | Class | Reasoning |
|---|---|---|
| `core/schema.py` (`AgentEvent`, `EventType`, …) | **ADAPT** → `sensors/legacy` input format | This is not the v3 event model (D4, D5). Keeping it as a *wire format* means every existing adapter and `POST /api/v1/events` client keeps working through the legacy translation sensor. |
| `core/models.py` ORM tables | **DEPRECATE** (tables) / **KEEP** (`TenantRepository` pattern, `SqlAlchemyAuditStore`, `get_database_url`) | The tables encode v0.2 features (D2, D13). The tenant-scoping pattern, the audit store and the secure DB URL builder are good. |
| `core/event_bus.py` | **ADAPT** | Useful as the in-process fan-out inside the sensor buffer and for LIVE streaming. Changes: drop the 10k in-memory log as a data source, replace per-event `asyncio.run` in `publish_sync` with a bounded queue plus background flusher, make dispatch order deterministic, and pass immutable (frozen) objects to handlers so that no handler can mutate evidence (fixes the D2 race). |
| `core/watcher.py` `watch()` + framework detection | **ADAPT** → `sensors/registry.auto_attach()` | Framework detection by MRO is useful for one-line setup. The generic method-wrapping adapter stays *only as a passive recorder*: the inline safety check and `AgentWatchBlockedError` are removed from the v3 path (D3). |
| `core/safety.py`, `policy_dsl.py`, `policy_loader.py`, `risk.py`, `injection.py` | **DEPRECATE** → `legacy/guard` | This is a guardrail product, and it sits in the control path. Its pattern rules can later serve as *labelled detectors* in AWBench (baseline for "dangerous operation" facets). |
| `core/blast_radius.py` | **DEPRECATE** | It is an arbitrary 0–100 regex score (D7). The regex table could become a facet classifier (`destructive_operation`) with no score. |
| `core/loop_detector.py`, `core/recursion_depth_detector.py` | **ADAPT** → `behaviour/motifs` rule detectors | These are the natural first baselines for loop and recursion motifs. They must be re-expressed over `ComputationalEvent` and relations, and evaluated on AWBench. |
| `core/http_forwarder.py` | **ADAPT** → `sensors/exporters.HttpExporter` | Batch instead of one POST per event, retry with jitter, count drops. |
| `core/config.py` | **KEEP** (extend) | pydantic-settings based. |

### 1.2 Observation and ingestion

| Module | Class | Reasoning |
|---|---|---|
| `adapters/claude_code.py` | **ADAPT** → `sensors/claude_code` | The stream-json parsing and `tool_use_id` correlation are valuable. Split it into a *sensor* that captures raw lines plus declared ids (with original timestamps where present), and a *normalizer*. The subprocess wrapper stays as an optional launcher. |
| `adapters/langchain.py` | **REWRITE** → `sensors/langchain` | Callback coverage is fine, but it drops `parent_run_id`, never reads `_run_map`, and does not correlate start and end (D1). The new sensor emits raw callback payloads with `run_id` and `parent_run_id`, and the normalizer pairs them. |
| `adapters/langgraph.py`, `autogen.py`, `crewai.py`, `openai_agents.py`, `smolagents.py`, `autogpt.py`, `openclaw.py` | **ADAPT** (in priority order: langgraph, openai_agents, autogen, crewai, smolagents; autogpt and openclaw last or DEPRECATE) | Hook points are reusable knowledge. Each becomes a sensor emitting raw payloads plus declared ids. Until rewritten, they keep flowing through `sensors/legacy`. |
| `adapters/base.py` semantic cache hooks | **REMOVE** from adapters | A cache in the observation path changes model outputs, which breaks passivity (D3). If the product still wants caching, it lives in `legacy/cost`. |
| `telemetry/otel.py` | **KEEP** (as self-telemetry) + **new** OTLP *receiver* in `sensors/otel` | Export of AgentWatch's own metrics stays. Ingesting OTel is new work (D10). |
| `telemetry/ingestion.py` `TenantIngestionPipeline` | **ADAPT** → `runtime/ingest` | Per-tenant batching and rate limits are reusable. The output target becomes `ObservationStore.append`. |
| `telemetry/collector.py` | **REMOVE** | Duplicate of `tracing/collector.py` (D11). |
| `telemetry/execution_logger.py` | **DEPRECATE** | A structured logger for AgentWatch itself. Replace it with standard logging plus OTel self-telemetry. |
| `tracing/collector.py` `TraceCollector` | **REWRITE** | A bounded in-memory store that mutates evidence and is inconsistent across processes (D2, D8). It is replaced by the evidence store and run queries. It stays alive for the legacy API until cut-over. |
| `tracing/live.py` `LiveStreamHub` | **ADAPT** → LIVE view stream | Fan-out and backlog logic reusable; payload becomes normalized events and state updates. |
| `tracing/sampling.py` | **ADAPT** → `sensors/buffer` sampling policies | Tail and failure-always sampling are valuable. Every sampling decision **must be recorded** in `Observation.sampling` so that statistics can reweight; unrecorded sampling biases the genome and drift. |
| `tracing/spans.py` | **DEPRECATE** | A span view of `AgentEvent`. Superseded by `ComputationalEvent`. |
| `tracing/trajectory.py` | **ADAPT** → `behaviour/motifs` + `compare` features | Loop, repeat and dead-end detection become motif detectors. Label-based graphs are replaced by the EXECUTION view. |
| `tracing/audit.py` | **ADAPT** (retry-storm → motif) / **DEPRECATE** (hallucinated-arguments heuristic, which moves to provenance, see below) | |
| `validation/schema_validator.py` | **ADAPT** → envelope validation | JSON-schema validation of the observation envelope, *not* of payloads (payloads are stored as received). |

### 1.3 Analysis-like modules

| Module | Class | Reasoning |
|---|---|---|
| `replay/engine.py` | **REWRITE** → `replay/` L0–L1 | `ReplaySession`, failure-point marking and `compare_sessions` are useful reference behaviour. They are replaced by timeline and graph replay plus structural diff in `compare/`. |
| `replay/counterfactual.py` | **REWRITE** (and relabel immediately in legacy API) | Presents an edited copy of the observed timeline as a counterfactual (D7). v3 counterfactuals are branches with explicit replay levels and `OBSERVED` / `SIMULATED` / `MODEL_ESTIMATED` / `UNKNOWN` labels. **Immediate fix in v0.2**: label `/simulate` output as `"method": "value_substitution_only", "simulated": false`. |
| `rollback/` | **DEPRECATE** → `legacy/rollback` | It acts on the user's filesystem and git. Out of sidecar scope. Some of its snapshot capture ideas inform L4 environment capture later (read-only). |
| `memory/causal_graph.py` | **REWRITE** → `graph/` | No evidence or confidence, bounded with silent eviction, callers assert edges (D6). |
| `memory/graph_query.py` | **ADAPT** (ideas) → `query/` | The "parse question → pick entry node → traverse upstream/downstream" pattern is a good deterministic NL fallback for cone queries. Reuse the direction and edge-filter heuristics. |
| `memory/engine.py`, `decay.py`, `temporal_decay.py`, `identity.py`, `resolver.py`, `health.py`, `governance.py`, `nlquery.py`, `visualization.py` | **DEPRECATE** → `legacy/memory` | This is a memory *product for agents*. v3 observes memory; it does not provide it. |
| `orchestration/engine.py` | **DEPRECATE** (candidate for REMOVE) | A multi-agent *runtime*, which v3 explicitly is not. It may serve as an **AWBench reference system** (a multi-agent app to observe) if it runs standalone. |
| `orchestration/dag.py` | **REWRITE** → `graph/` | The acyclicity constraint is wrong for untimed nodes (D6). |
| `orchestration/shapley.py` | **KEEP** (algorithm) → `causality/attribution` as EXPERIMENTAL | An exact and Monte Carlo Shapley implementation is sound mathematics. It needs a value function derived from interventions (AWBench ablations). Without one it must not be exposed. |
| `orchestration/race_condition.py`, `deadlock.py` | **ADAPT** → `behaviour/motifs` (contention, deadlock) | Interval-overlap logic on shared resources maps directly onto `effects` (READ/WRITE on the same entity) and the temporal index. |
| `orchestration/consensus.py`, `spawning.py`, `trust.py`, `crew_context.py`, `propagation.py` | **DEPRECATE** | Runtime-coordination features. |
| `reasoning/fingerprint.py` | **ADAPT** → `behaviour/genome` feature family ("output style") | Observable style features are legitimate genome features. The "model swap" alert becomes a drift test with FPR control. |
| `reasoning/hallucination.py` | **ADAPT** → `provenance/unsupported` (EXPERIMENTAL) | "Tool input references identifiers never seen upstream" is really a **provenance** question: unsupported-information detection. Re-express it over INFORMATION-view lineage and evaluate it. |
| `reasoning/goal_drift.py`, `semantic_drift.py` | **ADAPT** → `behaviour/drift` candidates (EXPERIMENTAL) | Only as features with defined distances. No verdicts. |
| `reasoning/calibration.py` | **ADAPT** → `analysis/calibration` | Tracking FP/FN rates and threshold fitting is needed for calibrating confidences and forecasts. |
| `reasoning/auditor.py`, `trust_score.py`, `quality.py`, `adversarial.py`, `dual_eval.py`, `explainer.py`, `benchmark.py` | **DEPRECATE** | LLM-judge and heuristic scoring of reasoning. The auditor becomes an **AWBench baseline** for comparison, nothing more. |
| `scoring/confidence.py` | **DEPRECATE** | A composite "confidence" score without calibration (D7). |
| `scoring/drift.py` (`embed`, `cosine`, hashed-vector fallback) | **ADAPT** → `entities/fingerprints` | Deterministic hashed embeddings are useful as a dependency-free artifact similarity baseline. |
| `scoring/silence.py` | **ADAPT** → `behaviour/motifs` ("silent success" candidate, EXPERIMENTAL) | The idea of structural outliers relative to the system's own history is right. It needs FPR evaluation. |
| `lattice/shadow_filesystem.py`, `attention_scatter.py` | **DEPRECATE** | Pre-execution simulation for blocking. The attention-scatter entropy could be re-proposed as a genome feature, but only with a definition and a benchmark. |
| `circuit_breaker/`, `hitl/` | **DEPRECATE** → `legacy/` | Control path (D3). |
| `platform/intelligence.py` | **REMOVE** | Pattern "insights" with no statistical testing (D7). |
| `governance/causal.py` | **REMOVE** | Signs non-causal chains as causal (D7). Replaced by cones with evidence classes. |

### 1.4 Cost, governance, security

| Module | Class | Reasoning |
|---|---|---|
| `cost/tracker.py`, `reporting.py`, `comparator.py` | **ADAPT** → `ResourceUsage` + COMPARE features | Tokens and dollars are resources on events. The pricing table must be versioned and recorded in `resources.pricing_version`. |
| `cost/anomaly.py`, `predictor.py`, `roi.py` | **DEPRECATE** (anomaly and predictor are candidate forecasting baselines) | |
| `cost/router.py`, `complexity_router.py`, `semantic_cache.py`, `caching.py`, `governance.py` (budget enforcement) · `infrastructure/router.py` | **DEPRECATE** | These are interventions: routing, caching and enforcement. Three routers is duplication (D11). |
| `governance/audit_log.py` | **KEEP** (+ generalize) | A hash chain → basis of evidence segment sealing (§3.4 of V3_ARCHITECTURE). Keeps covering operational and admin actions. |
| `governance/gdpr.py` | **ADAPT** | Erasure moves to crypto-shredding (ADR-0011). The subject-discovery logic is reusable. |
| `governance/engine.py`, `compliance_reporter.py`, `reports.py`, `eu_ai_act.py`, `iso42001.py`, `hipaa.py`, `residency.py` | **DEPRECATE** → `legacy/governance` | Compliance reporting is a product line, not the instrument. It could later be rebuilt *on top of* v3 queries. |
| `governance/rbac.py` | **ADAPT** → `api/auth` | Roles for API access. |
| `security/redaction.py` | **KEEP** (move to sensor edge) | Good library. It changes from mutating stored events to producing a `RedactionManifest` at capture or ingest (ADR-0011). |
| `security/encryption.py`, `key_storage.py`, `webhook_signing.py` | **KEEP** | Needed for per-tenant keys, crypto-shredding and signed webhooks. |
| `security/license.py`, `entitlement_store.py`, `checkout.py`, `abuse_detection.py` · `api/entitlement.py` | **DEPRECATE** from core (retain in the API layer only if the commercial model needs it) | Commercial concerns must not shape the domain model. |
| `security/owasp.py`, `redteam.py`, `exfiltration.py`, `sandbox.py`, `report.py`, `payloads.json` | **DEPRECATE** | Guardrail product. The red-team payloads may seed AWBench "invalid external data" perturbations. |

### 1.5 Interface and infrastructure

| Module | Class | Reasoning |
|---|---|---|
| `api/server.py` | **REWRITE** as an app factory + `api/routers/*` | A 1,872-line monolith with process-local singletons (D8). Legacy routes move into `api/routers/legacy_v1.py` unchanged, served from the same app during migration. |
| `api/auth.py`, `tenant_auth.py`, `middleware/rate_limiter.py` · `models/tenant.py` | **KEEP** | Solid, tested. |
| `cli/main.py` | **REWRITE** (split) | Split into `cli/commands/{evidence,runs,events,graph,compare,replay,server,doctor,legacy}.py`. Keep `doctor`, `check-env`, `server start`/`status`, `version`. The legacy command group stays under `panoptis legacy …` with aliases for one release. |
| `cli/_utils`, `cli/verify_env.py`, `cli/animator.py` | **KEEP** | |
| `cli/demo.py` | **ADAPT** → generates an AWBench-style demo run | Currently in merge conflict. Resolve before branching. |
| `cli/mcp.py`, `protocol/mcp_server.py` | **ADAPT** → `query/mcp` | Exposing AgentWatch queries via MCP is aligned with QUERY. The tool set is replaced by v3 structured queries. |
| `protocol/schema_v1.py` (ReasoningTrace), `badge.py`, `benchmark.py` | **DEPRECATE** | |
| `plugins/registry.py`, `plugins/sandbox.py` | **ADAPT** → `sensors/registry` + analyzer registry (Python entry points) | Manifest and signing concepts are reusable. The purpose changes from "auditor models and rule packs" to sensors and analyzers. |
| `platform/cloud.py`, `sharing.py`, `prompts.py`, `eval_builder.py` | **DEPRECATE** | |
| `eval/runner.py` | **ADAPT** → `benchmarks/awbench/runner` scaffolding | "Import an agent callable, run a dataset" is the shape AWBench needs. Scoring is replaced. |
| `monitoring/metrics.py` | **KEEP** | Prometheus self-metrics. |
| `models/cache.py` | **REMOVE** (after confirming it is unused) | Very small helper (14 lines) with no clear owner. |
| `tasks.py` (Celery) | **KEEP** | The worker hosts `runtime` pipeline stages in server mode. |
| `_version.py` | **KEEP** | |

### 1.6 Non-package assets

| Asset | Class | Reasoning |
|---|---|---|
| `frontend/` shell, `lib/api`, `QueryProvider`, `useLiveEventSocket`, `wsReconnect`, Tailwind config, Jest setup | **KEEP** | Foundations |
| `frontend/pages/*` | **REWRITE** | New IA (V3_ARCHITECTURE §6) |
| `benchmarks/bench_overhead.py` | **ADAPT** → `benchmarks/overhead` | Sensor overhead is a first-class metric (the observer effect) |
| `benchmarks/run_eval.py`, `generate_cases.py`, `test_cases.json`, `results/` | **DEPRECATE** → `benchmarks/legacy` | The reasoning-auditor benchmark is not AWBench |
| `tests/` (~940 tests) | **KEEP** → `tests/legacy/` | They protect legacy behaviour while it lives. New suite in `tests/v3/`. |
| `Dockerfile.api`, `Dockerfile.worker`, `docker-compose.yml`, `helm/`, CI workflows, `Makefile`, Dependabot, CODEOWNERS | **KEEP / ADAPT** | Add an SQLite-only dev profile. Helm: set replicas > 1 only after the D8 fix. |
| `grafana/` | **ADAPT** | Self-metrics dashboards (ingest rate, lag, drops, analyzer runtimes) |
| `docs/` (existing) | **KEEP** then rewrite progressively | `docs/v3/` is authoritative for v3 |
| `VISION.md` | **REWRITE** after Phase 1 | It describes a control plane, which v3 rejects |
| `how HEAD:__main__.py` | **REMOVE** | Accidental file |
| `demo.py`, `real_agent.py` (root) | **ADAPT** → `benchmarks/awbench/systems/` or REMOVE | |
| `issues.md`, `panoptis_masterlist.md`, `MASTERLIST_STATUS.md`, `PROGRESS.md` | **REMOVE** (archive in `docs/archive/`) | Checklist artefacts. The masterlist framing drove the sprawl (D12). |
| `agentwatch-landing/` | Out of scope | Separate repository. Messaging must change after v3 capabilities are validated, not before. |

### 1.7 Tally

| Class | Approx. modules | Share of Python LOC (approx.) |
|---|---|---|
| KEEP | ~20 | ~12% |
| ADAPT | ~40 | ~30% |
| REWRITE | ~8 | ~22% (incl. server.py, cli/main.py) |
| DEPRECATE | ~90 | ~34% |
| REMOVE | ~6 | ~2% |

These figures are estimates from module sizes. Recompute them with `scripts/migration_tally.py` (Phase 1 task) once `legacy/` moves are done.

---

## 2. Migration architecture: strangler around a new core

```text
                         ┌───────────────────────── one FastAPI app ─────────────────────────┐
 v0.2 adapters ─────────►│ /api/v1/events ──► legacy route ──┬──► (old) EventBus → TraceCollector│── /api/v1/* reads
 (AgentEvent JSON)       │                                   │                                  │   (unchanged, legacy)
                         │                                   └──► sensors/legacy translator     │
                         │                                          │ Observation(source_kind=   │
 OTel SDKs ─────────────►│ /v1/traces (OTLP) ──────────────────────►│ "legacy.agent_event")     │
 v3 sensors ────────────►│ /v3/observations ───────────────────────►▼                           │
                         │                              ObservationStore (append-only)          │── /v3/* reads
                         │                                     │ normalize/resolve/segment      │
                         │                                     ▼                                │
                         │                              events · entities · runs · relations    │
                         └──────────────────────────────────────────────────────────────────────┘
```

Stages:

| Stage | What changes | Legacy impact | Exit condition |
|---|---|---|---|
| **M0 — Stabilize** (before branching) | Resolve the in-progress merge on `main`; sync with `origin/main`; ship two low-risk v0.2 fixes: freeze events passed to bus handlers (D2 redaction race), and relabel `/simulate` output (D7) | Bug fixes only | `main` green in CI |
| **M1 — Parallel core** (Phase 1) | Create `architecture/v3` from `main`. Add `evidence/`, `events/`, `entities/`, `runs/`, `sensors/`, `storage/`, `runtime/`, `/v3` routes and the OTLP receiver. The legacy `/api/v1/events` route **tees** into the legacy translator. | None; additive | Phase 1 exit criteria (PHASE1_PLAN §5) |
| **M2 — Quarantine** | Move DEPRECATE modules to `panoptis/legacy/…`, with shim modules at old paths that re-export and warn. Split the base install: `panoptis` (light, sensors) plus `panoptis[server]` (ADR-0008). | Import paths warn; `pip install panoptis` users who run the server must add `[server]` (documented, and the CLI prints the fix) | CI import-linter contract passes; legacy tests pass from `tests/legacy` |
| **M3 — Read cut-over** (Phases 2–3) | New UI views read `/v3`. Legacy UI pages are hidden behind a "legacy" nav section. The in-memory `TraceCollector` is no longer the source of any v3 read. | Legacy pages still work | TIMELINE, MAP and COMPARE are usable on real data |
| **M4 — Merge to main** | `architecture/v3` merges to `main` as 0.3.0 (pre-1.0, EXPERIMENTAL). The legacy API is still served. | Deprecation notices published | Release checklist, AWBench v0 results published with honest numbers |
| **M5 — Retire** | After one or two minor releases: remove `legacy/` modules that have no users, drop the `/api/v1` write path (the translator remains available as a sensor). | Breaking; major-version gated | Telemetry or issue tracker shows no usage; a migration guide exists |

Guarantees during migration:

1. No working integration is broken before M5. Every v0.2 adapter feeds v3 via `sensors/legacy` from day one of M1.
2. **No destructive data migration.** Old `agent_events` rows can be *imported* as observations (export them as JSONL, then `panoptis ingest events.jsonl`; as built, there is no separate `import-legacy` command). They are marked `source_kind="legacy.agent_event.pg"` with `observation_confidence` reflecting that timestamps and parents are unreliable (D1, D5).
3. The legacy code is frozen for features. Bug and security fixes only.

---

## 3. Branch and repository procedure

The working tree is mid-merge at audit time, so creating a branch now would carry unresolved conflicts into v3. The steps:

```bash
# 1. finish or abort the in-progress merge on main (conflicts: .dockerignore, panoptis/cli/demo.py)
git status
# 2. integrate origin/main (local has diverged: 1 local vs 37 remote commits); main is PR-protected → do it via a PR branch
# 3. once main is clean and green:
git switch -c architecture/v3
git add docs/v3 && git commit -m "docs(v3): Phase 0 architecture audit, target architecture, ADRs"
```

`architecture/v3` is a long-lived integration branch. Phase work lands in it through PRs (`v3/phase1-evidence-store`, …). `main` receives only M0 fixes until M4.


---

## 4. Migration status (2026-09-27)

| Stage | Status | Notes |
|---|---|---|
| M0 Stabilize | **done** | Interrupted merge completed as a merge commit on `architecture/v3`. The shared-event redaction race is fixed, with a regression test. `/simulate`, `hallucination_risk` and `governance/causal.py` are relabelled. |
| M1 Parallel core | **done** | v3 packages, `/api/v3`, the OTLP receiver, and the legacy `/api/v1/events` tee (`AGENTWATCH_V3_TEE`). |
| M2 Quarantine | **done (logical)** | Enforced by `tests/v3/test_architecture_contracts.py` in CI; the physical move is replaced by deletion at M5 (ADR-0016). Packaging split moved to M4. |
| M3 Read cut-over | **done for the UI** | The frontend reads only `/api/v3`. v0.2 dashboard pages are removed; the v0.2 API remains. |
| M4 Merge to main | **merged** (2026-09-27) | `architecture/v3` merged into `main` with `--no-ff` (17d667d), keeping its individual commits. Not yet done: the packaging split (ADR-0008; needs owner approval, the default `pip install panoptis` is unchanged) and a release tag/version bump (tags publish to PyPI). |
| M5 Retire | pending | After a deprecation window. |

CLI changes: v3 commands are canonical, and the v0.2 `compare` moved to `panoptis legacy compare`. v0.2 data in Postgres `agent_events` can be exported as JSONL and imported with `panoptis ingest file.jsonl`. It goes through `LegacyTranslator` with loss reporting.
