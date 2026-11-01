# AgentWatch v0.2 — Current Architecture (Forensic Review)

Status: Phase 0 deliverable · Audited 2026-09-25 against `main` @ `1eeeb2c` plus the staged, uncommitted merge in the working tree.

This document describes what exists, not what the README or `VISION.md` says exists. Every claim links to code.

> **Dated snapshot.** This is the v0.2 system as audited on 2026-09-25, before v3. Several findings have since been addressed, for example the in-place HIPAA redaction race (regression test in `tests/v3/test_regressions.py`). For the current state see [IMPLEMENTATION_STATE.md](IMPLEMENTATION_STATE.md) and [MIGRATION_MAP.md](MIGRATION_MAP.md) §4.

---

## 1. Current system map

### 1.1 Size and shape

| Area | Size | Notes |
|---|---|---|
| `panoptis/` Python package | ~31k LOC, 30 subpackages, ~200 modules | Largest files: `cli/main.py` (2,616), `api/server.py` (1,872), `core/safety.py` (923), `core/models.py` (763), `core/watcher.py` (738) |
| `tests/` | 89 files, ~940 test functions | Unit tests only for most modules; 1 integration test; locust load test |
| `frontend/` | Next.js 14, 13 pages | react-query hooks per feature area, WebSocket live feed |
| `benchmarks/` | Reasoning-auditor benchmark + overhead benchmark | The heuristic auditor scores **0% F1** (documented in `benchmarks/README.md`) |
| Deploy | Dockerfile.api/worker, docker-compose (pgvector PG16, Redis, Jaeger), Helm chart, Fly/Railway/Render configs, Grafana dashboards | |
| `agentwatch-landing/` | Separate embedded repository | Out of scope for v3 |

Modules are commonly tagged with feature IDs from `panoptis_masterlist.md` (`SAF-004`, `MEM-002`, `MAG-006`, `RSN-008`...). Most are 50–200 lines. That points to checklist-driven growth: many independent features, each with its own small model of the world, and no shared model.

### 1.2 Runtime data flow (as built)

```text
  Observed agent process                                 AgentWatch API process (FastAPI, uvicorn)
 ┌───────────────────────────────────────┐              ┌───────────────────────────────────────────────┐
 │ watch(agent)  core/watcher.py         │              │ POST /api/v1/events  (AgentEvent JSON)        │
 │   ├─ framework detection (MRO sniff)  │              │        │                                      │
 │   ├─ adapters/* (callbacks / wraps)   │   HTTP       │        ▼                                      │
 │   ├─ SafetyEngine.check  ◄── BLOCKS   │ ───────────► │ EventBus (in-memory, 10k ring buffer)         │
 │   └─ EventBus.publish_sync            │ core/http_   │   ├─ TraceCollector.ingest  (in-memory,       │
 │        └─ HttpEventForwarder          │ forwarder.py │   │     500 traces, JSON flush every 30 s)    │
 └───────────────────────────────────────┘              │   ├─ _after_publish → _pg_write_event         │
                                                        │   │     (Postgres; failures only logged)      │
                                                        │   ├─ alerting, cost, governance hooks         │
                                                        │   └─ WebSocket fan-out /ws/events             │
                                                        │                                               │
                                                        │ Read APIs (/sessions/*, /replay, /simulate,   │
                                                        │ /confidence, /reasoning ...) read from the    │
                                                        │ IN-MEMORY collector, not from Postgres        │
                                                        └───────────────────────────────────────────────┘
```

### 1.3 Core abstractions

| Abstraction | Location | What it is |
|---|---|---|
| `AgentEvent` | `panoptis/core/schema.py:320` | One flat Pydantic model with seven optional payload slots (`tool_call`, `tool_result`, `safety`, `memory`, `confidence`, `agent_message`, `checkpoint`). Requires `session_id` and `agent_id`. |
| `EventType` | `panoptis/core/schema.py:20` | Closed enum of ~35 types. It mixes things that were observed (`tool.call`, `memory.read`) with things AgentWatch concluded (`safety.block`, `confidence.score`, `goal.drift`, `reasoning.style_swap`, `anomaly.detected`). |
| `AgentFramework` | `panoptis/core/schema.py:87` | Closed enum. LangGraph, AutoGen and Smolagents are all mapped to `CUSTOM` (`core/watcher.py:160-171`). |
| `EventBus` | `panoptis/core/event_bus.py` | Process-local pub/sub. Handlers are dispatched concurrently with `asyncio.gather`. `publish_sync` falls back to `asyncio.run` for each event when no loop is running. |
| `TraceCollector` | `panoptis/tracing/collector.py` (a near-copy exists at `telemetry/collector.py`) | The primary store behind the read APIs. Bounded, in-memory, with a periodic JSON flush. |
| ORM + `Repository` | `panoptis/core/models.py` | Tables `agent_sessions`, `agent_events`, `checkpoints`, `memory_entries`, `plugins`, `task_nodes`, `audit_log`. Schema is created with `create_all`; there is no migration tool. |
| Hash-chained audit log | `panoptis/governance/audit_log.py`, `SqlAlchemyAuditStore` | A genuinely append-only, tamper-evident record. It covers governance actions, not observations. |

### 1.4 Capability inventory by package

| Package | What it actually does |
|---|---|
| `core/` | Schema, event bus, `watch()`, a regex- and pattern-based `SafetyEngine`, policy DSL and loader, blast-radius regex scoring, loop and recursion detectors, config, HTTP forwarder |
| `adapters/` | LangChain callback handler, LangGraph, AutoGen, CrewAI, AutoGPT, OpenAI Agents, Smolagents, OpenClaw, and a Claude Code `stream-json` parser plus subprocess wrapper |
| `telemetry/` | OTel **export** of AgentWatch's own spans and metrics, a duplicate collector, a per-tenant batching ingestion pipeline, and a structured execution logger |
| `tracing/` | Collector, span conversion, `trajectory` (loop, repeat and dead-end detection over event labels), samplers (head, tail, reservoir, failure-always), the live WebSocket hub, and a tool audit log (retry storms, "hallucinated" arguments) |
| `replay/` | Step-through replay built from stored events, session comparison, and a "counterfactual" engine |
| `rollback/` | Filesystem tarball snapshots and git stash/branch checkpoints, with restore. **This changes the user's working directory.** |
| `memory/` | A memory product for agents (episodic, semantic and procedural storage, decay, contradiction resolver, NL query) plus `causal_graph` and `graph_query` |
| `orchestration/` | A multi-agent **runtime** (`engine.py` runs subagents), an inter-agent DAG, Shapley attribution, and deadlock, race-condition, consensus, spawning and trust modules |
| `reasoning/` | Reasoning auditor (heuristic or LLM judge), hallucination classifier, style fingerprint, goal and semantic drift, calibration, adversarial, dual-eval and trust score |
| `scoring/` | Composite confidence score, embedding drift with a hashed-vector fallback, silent-failure detector |
| `cost/` | Token/$ tracker, reporting, anomaly, k-NN cost predictor, ROI, **model routing, semantic cache, budget enforcement** |
| `governance/` | Hash-chained audit log, GDPR erasure, HIPAA, EU AI Act and ISO 42001 reports, RBAC, residency, and a "causal compliance attribution" module |
| `security/` | PII/PHI redaction (Presidio or regex), encryption, key storage, webhook signing, licensing and entitlements, OWASP checks, red-team payloads, exfiltration detection, sandbox |
| `lattice/`, `circuit_breaker/`, `hitl/` | Pre-execution simulation (shadow filesystem, attention scatter), circuit-breaker state machine, human-in-the-loop approval |
| `api/` | A single 1,872-line FastAPI app with ~40 routes, module-level singletons, API-key and tenant auth, a rate limiter, entitlements and a WebSocket |
| `cli/` | A single 2,616-line Typer app with ~30 commands |
| `platform/`, `protocol/`, `plugins/`, `infrastructure/`, `models/`, `monitoring/`, `validation/` | Cloud and sharing stubs, a "ReasoningTrace v1" JSON schema, an MCP server exposing AgentWatch queries, a plugin registry and sandbox, an LLM router, a tenant model, Prometheus metrics, and a JSON-schema validator |

### 1.5 What is solid engineering

These pieces work, are tested, and are worth keeping:

- **Auth and tenancy:** API-key auth, tenant-scoped repository (`TenantRepository`), rate limiter middleware.
- **Hash-chained audit log** with a SQL store (`governance/audit_log.py`, `core/models.py:472`). This is the seed of the immutable evidence layer.
- **Redaction library** (`security/redaction.py`) with a Presidio/regex fallback and a single source of patterns.
- **Claude Code stream-json parser** (`adapters/claude_code.py`), which correlates results to calls by `tool_use_id`.
- **Deployment surface:** Docker, compose profiles, Helm, CI (lint → tests → integration → frontend → docker), release workflow, Dependabot, CODEOWNERS.
- **Frontend foundations:** Next.js app shell, react-query data layer, WebSocket reconnect logic.
- **Tail and failure-always samplers** and the overhead benchmark (`benchmarks/bench_overhead.py`).
- **Test infrastructure:** conftest mocks for heavy ML dependencies, and a CI coverage gate.

---

## 2. Architectural debt

These are assumptions built into v0.2 that block the v3 architecture. They are ordered by how strongly each one blocks v3.

### D1. No structural linkage is captured (blocking)

`AgentEvent.parent_event_id` exists (`core/schema.py:332`), but **no code in the repository ever sets it**:

```text
$ grep -rn "parent_event_id\s*=" panoptis
panoptis/core/models.py:88:    parent_event_id = Column(String(36))
```

The LangChain adapter receives `run_id` and `parent_run_id` on every callback and throws the parent away. It records `run_id → event_id` into `_run_map` (`adapters/langchain.py:50,65`) but never reads that map back. Start and end callbacks become two unrelated events, and `on_llm_end` does not even pass its `run_id` (`adapters/langchain.py:102`).

As a result, the only structure v0.2 can recover is *temporal adjacency within a session*. Execution graphs, information flow, provenance and causal cones all need declared or inferable dependencies. This is the single largest gap. It is also cheap to fix at the sensor level.

### D2. Raw evidence is mutable and lossy (blocking)

- **In-place redaction.** In HIPAA mode, `TraceCollector.ingest` redacts the event's fields in place (`tracing/collector.py:121-135`). The same object is shared with the other bus handlers, and those handlers are dispatched concurrently from a `set` of handler IDs (`core/event_bus.py:241-253`). The Postgres writer (`api.post_publish` → `_pg_write_event`) may therefore persist either redacted or raw content, depending on dispatch order. This is a privacy bug as well as an evidence-integrity bug.
- **Silent persistence failure.** A failed Postgres write is logged as a warning and dropped (`api/server.py:236-253`).
- **Eviction.** The primary read store holds at most 500 traces (`tracing/collector.py`, `max_traces`) and evicts the oldest. The `EventBus` keeps a 10k-event ring buffer.
- **No separation between observation and interpretation.** Safety verdicts, confidence scores and anomaly flags are written into the same row as the observation (`agent_events.risk_score`, `.confidence_score`, `.anomaly_flags`). When a detector improves, history cannot be re-interpreted.

### D3. The observer is in the control path (blocking)

`watch()` wraps methods and runs the `SafetyEngine` inline. It can raise `AgentWatchBlockedError` (`core/watcher.py:55`). `circuit_breaker/`, `hitl/`, `rollback/` and `cost/` (routing, semantic cache, budget enforcement) all change what the observed system does.

Two consequences:

1. It contradicts the sidecar principle.
2. It is an **observer effect** that confounds every behavioural measurement: latency, retries and the path taken all change because AgentWatch is present. A scientific instrument must be able to prove that it was passive.

`adapters/base.py` makes this concrete. A semantic-cache hook sits in the adapter base, so the observation layer can return cached model responses.

### D4. Agent-centric, closed-vocabulary schema

- `session_id` and `agent_id` are required on every event. Many AI-native systems have no "agent" (RAG services, pipelines, batch jobs, MCP servers), and many have no session.
- `EventType` and `AgentFramework` are closed enums. Every new source requires a change to the core schema, and the enum mapping already loses identity (LangGraph, AutoGen and Smolagents all become `CUSTOM`).
- Payloads are mutually exclusive optional slots, one per v0.2 feature. There is no general notion of an actor, an operation, the objects it reads or writes, inputs, outputs or effects.

### D5. Weak time model

Each event has a single `timestamp` that defaults to `datetime.now()` when the event is **constructed**. For live callbacks that is roughly when the event happened. For post-hoc parsers it is not: the Claude Code parser stamps events at parse time (`adapters/claude_code.py:105-114`). `step_number` is a per-adapter counter.

v0.2 does not record:

- the clock source,
- clock uncertainty,
- a per-sensor sequence number for ordering,
- the difference between *occurred* and *received*.

Cross-process ordering, which is required for temporal-causal graphs, is therefore undefined.

### D6. Graph models are ad hoc and mutually incompatible

There are four separate graph notions:

| Module | Model | Problem |
|---|---|---|
| `memory/causal_graph.py` | Nodes and edges with edge kinds (`caused_by`, `produced`...) | Edges are asserted by callers and never inferred or tested. The graph holds at most 1,000 nodes and silently evicts the oldest. It carries no confidence or evidence. |
| `orchestration/dag.py` | Inter-agent DAG | **Rejects cycles.** Real systems cycle constantly (A→B→A), so a DAG is only valid over *time-indexed* nodes. |
| `tracing/trajectory.py` | Graph over event labels | Label-based, so structurally different executions collapse together. |
| `replay/engine.py` `compare_sessions` | Index-aligned list diff | No structural alignment. |

None of them are hypergraphs. None separate execution, information and causality. None carry uncertainty.

### D7. Components that overclaim

The v3 hard rules forbid arbitrary scores and causality from correlation. These components currently break those rules:

- `replay/counterfactual.py`: with no `step_fn` (which is how `/api/v1/sessions/{id}/simulate` calls it), the engine copies the *original* suffix unchanged after swapping one value, then reports a divergence step. The output is labelled a counterfactual, but it is the observed timeline with one field edited.
- `governance/causal.py`: signs a "causal chain" that is simply an upstream BFS over the unvalidated `CausalGraph`, and appends fixed remediation steps.
- `api/server.py` `/replay`: `"hallucination_risk": 1.0 - audit_summary.average_score  # Simple heuristic for UI`.
- `scoring/confidence.py`, `core/blast_radius.py` (regex → 0..100 score), `reasoning/trust_score.py`: composite scores without calibration or benchmark validation.
- `platform/intelligence.py`: reports "insights" such as day-of-week failure patterns with no significance testing.
- `benchmarks/`: the only analytic benchmark reports 0% F1 for the default configuration.

### D8. Monoliths with process-local singletons

`api/server.py` creates module-level singletons (`_collector`, `_replay_engine`, `_cost_tracker`, ...). Every read endpoint serves data from the **in-memory** collector. Run uvicorn with more than one worker, or run more than one replica under Helm, and each process sees a different subset of sessions. That is a correctness problem, not a style issue. `cli/main.py` (2,616 lines) mixes every product area into one module.

### D9. The sensor SDK is heavy

Base dependencies are `fastapi`, `uvicorn`, `sqlalchemy`, `asyncpg`, `redis`, `celery`, `litellm` and `mcp`. An application that only wants to be *observed* has to install the whole server stack. That discourages adoption as a sidecar, and it increases the observer effect through import time and memory.

### D10. OpenTelemetry is export-only

`telemetry/otel.py` exports AgentWatch's *own* spans and metrics. There is no OTLP receiver, so AgentWatch cannot ingest the most common instrumentation format in the ecosystem, including the OTel GenAI semantic conventions.

### D11. Duplication and dead weight

- `telemetry/collector.py` duplicates `tracing/collector.py`.
- Two `otel.py` files.
- Three routers: `cost/router.py`, `cost/complexity_router.py` and `infrastructure/router.py`.
- `adapters/langchain.py` builds `_run_map` and never reads it.
- Repository root contains `how HEAD:__main__.py`, which looks like the accidental output of a `git show` redirect, plus `demo.py`, `real_agent.py`, `issues.md`, `panoptis_masterlist.md`, `MASTERLIST_STATUS.md` and `PROGRESS.md`.

### D12. Scope sprawl defines the identity

About half the package serves goals that v3 explicitly excludes:

- guardrails: safety, lattice, OWASP, red-team;
- an agent runtime: orchestration engine, memory product;
- a model router: cost routing and caching;
- compliance report generation;
- licensing.

These are not bad code. They are a different product, and their abstractions (risk levels, blocking, policies, entitlements) are threaded through the core schema and storage.

### D13. No schema migrations

Tables are created with `Base.metadata.create_all`, and pgvector DDL is issued ad hoc inside `init_db` (`core/models.py:675-713`). An event-sourced store with versioned interpretations needs real migrations (Alembic).

### D14. Repository state at audit time

`main` is mid-merge: `.dockerignore` and `panoptis/cli/demo.py` are conflicted, and the local branch has diverged from `origin/main` (1 local commit, 37 remote). Resolve this before creating the v3 branch. See `MIGRATION_MAP.md` §3.

---

## 3. Summary judgement

v0.2 is a broad **agent guardrail and governance toolkit** whose central abstraction is *a governed agent action*. Its engineering scaffolding is reusable: API infrastructure, auth, tenancy, deployment, CI, the frontend shell, redaction, the hash-chained log and several parsers.

Its data model is not reusable as the v3 core:

- it records no dependencies (D1),
- it mixes interpretation into evidence and can lose or alter that evidence (D2),
- it perturbs the system it measures (D3),
- it cannot represent non-agent systems (D4),
- its time model cannot order events across processes (D5).

Because of D1–D3, **v3 must start from a new evidence and event core**. Existing adapters can be kept alive through a translation sensor so that no working integration is lost (see `MIGRATION_MAP.md`).
