# Phase 1 — Observation Core: Implementation Plan

Status: Phase 0 deliverable · PROPOSED · Prerequisite: MIGRATION_MAP stage M0 (clean `main`) and creation of the `architecture/v3` branch.

**Deliverable:** heterogeneous applications produce a common, immutable, re-interpretable event stream.

**Non-goals for Phase 1:**

- relations and graphs (Phase 2);
- any analyzer beyond normalization and resolution;
- UI beyond TIMELINE-lite;
- moving legacy modules (M2 happens at the end of Phase 1, and only if time allows).

---

## 1. Work packages and dependency order

```text
WP0 scaffolding ─┬─► WP1 evidence ─┬─► WP3 sensors ─────────┐
                 │                 ├─► WP2 events/normalize ─┼─► WP5 runtime ─► WP6 API/CLI ─► WP8 TIMELINE-lite
                 │                 └─► WP4 entities/runs ────┘                     │
                 └─► WP7 AWBench skeleton (S1, S2 stubs) ◄────────────────────────┘ (consumes WP6 for end-to-end)
```

Each WP lands as one or more PRs into `architecture/v3`. Every PR must pass:

- the existing suite, unchanged, in `tests/`;
- the new `tests/v3/`;
- ruff and mypy (strict for new packages);
- the import-linter contract.

---

## 2. Work packages

### WP0: Scaffolding and guardrails

**Tasks:**

1. Create the empty v3 package skeletons with `__init__.py`: `panoptis/{evidence,events,entities,runs,sensors,storage,runtime,analysis}` and `panoptis/api/routers/`.
2. Add an `import-linter` config in `pyproject.toml` with these contracts:
   - `panoptis.evidence` is independent;
   - layers: `sensors → evidence`; `events, entities, runs → evidence`; `runtime → events, entities, runs, storage`; `api → runtime, storage`;
   - forbidden: v3 packages importing `panoptis.core.safety`, `panoptis.governance.engine` or any module listed as DEPRECATE in MIGRATION_MAP.
3. Set up Alembic (`panoptis/storage/migrations/`) with a baseline revision covering existing tables, so that `create_all` is no longer needed for v3 tables.
4. Enable `mypy --strict` for the new packages only (per-module override).
5. Add `tests/v3/` with shared fixtures: an in-memory SQLite store, a temporary blob directory, and a frozen clock.
6. Add the `hypothesis` dev dependency for property tests.

**Acceptance:** CI green; the contract fails when a deliberately bad import is added in a test branch.

### WP1: Evidence plane (`panoptis/evidence`, `panoptis/storage`)

**Files:**

- `evidence/model.py`: `Observation`, `ObservationDraft`, `SensorRef`, `ClockInfo`, `SamplingInfo`, `RedactionManifest`, `BlobRef`. Frozen pydantic models (`model_config = ConfigDict(frozen=True)`).
- `evidence/canonical.py`: canonical JSON (sorted keys, UTF-8, no insignificant whitespace, fixed float repr); `payload_sha256`; `idempotency_key`.
- `evidence/ulid.py`: monotonic ULID generator (stdlib only).
- `evidence/wire.py`: the envelope JSON codec (`application/vnd.agentwatch.observation+json;v=1`) and a batch format (NDJSON).
- `evidence/segments.py`: Merkle root over `(obs_id, payload_sha256, idempotency_key)`; `Segment`; chaining; `verify()`; inclusion proofs.
- `evidence/blobs.py`: `BlobStore` protocol, `FsBlobStore` (sharded dirs, atomic write via tmp + rename, write-once), inline threshold logic.
- `storage/protocols.py`: `ObservationStore`, `InterpretationStore`, `EntityStore`, `RunStore`.
- `storage/sqlite/…` and `storage/postgres/…`: implementations. PG uses SQLAlchemy Core (not ORM) for append throughput. The Alembic revision includes the `observations` table, `evidence_segments`, and the PG trigger rejecting UPDATE/DELETE on `observations`.
- `storage/sealer.py`: seals open observations into segments every N=10,000 observations or T=60 s (configurable).

**Tests:**

- property: append is idempotent (same key → same `obs_id`, one row);
- property: any bytes mutation in a sealed segment makes `verify()` fail;
- the store protocol exposes no mutation methods (reflection test);
- PG role/trigger test (integration, runs in CI's Postgres service);
- blob write-once (a second write of the same hash is a no-op; a hash mismatch raises).

**Acceptance:** a 1M-observation synthetic append plus seal plus verify completes. Throughput is **measured and recorded** in `benchmarks/overhead/results/` for SQLite and PG. No target is claimed before measurement. The initial *goal* is ≥ 5k obs/s batched on the dev laptop for SQLite.

### WP2: Event algebra and normalizers (`panoptis/events`)

**Files:**

- `events/model.py`: `ComputationalEvent`, `EventKind`, `Effect`, `EffectKind`, `TemporalContext`, `ExecutionContext`, `DeclaredLink`, `ResourceUsage`, `EventConfidence`, `ErrorInfo`, `InterpretationRef`.
- `events/ids.py`: deterministic `event_id = uuid5(NS, f"{normalizer}|{sorted obs ids}|{local_index}")`.
- `events/normalizers/base.py`: the `Normalizer` protocol, `ObservationWindow`, and `NormalizeResult(events, pending_obs, diagnostics)`.
- `events/normalizers/pairing.py`: generic start/end pairing keyed by a declared id, with a timeout. An unpaired start becomes an event with `end=None` and `status=UNKNOWN` plus a diagnostic.
- `events/normalizers/otel_genai.py`: OTLP span → event. Mapping table:
  - `gen_ai.operation.name` → `MODEL_INVOCATION`;
  - `gen_ai.tool.*` / `execute_tool` → `TOOL_INVOCATION`;
  - `db.*` → `STATE_MUTATION` or `RETRIEVAL` by `db.operation`;
  - `http.*` → `EXTERNAL_IO`;
  - `messaging.*` → `MESSAGE`;
  - anything else → `UNKNOWN` with attributes preserved.

  Inputs and outputs are drawn from GenAI content events when present. Tokens map to `ResourceUsage`.
- `events/normalizers/langchain.py`: callback pairs → events. `on_llm_*` → `MODEL_INVOCATION`; `on_tool_*` → `TOOL_INVOCATION`; `on_retriever_*` → `RETRIEVAL`; `on_chain_*` → `LIFECYCLE` with the `chain` facet.
- `events/normalizers/claude_code.py`: stream-json → events. Assistant message → `MODEL_INVOCATION` (outputs = message artifact); `tool_use` + `tool_result` paired by `tool_use_id` → `TOOL_INVOCATION`; `result` → `LIFECYCLE`; errors → `FAILURE`.
- `events/normalizers/legacy.py`: `AgentEvent` → event, mapping each `EventType`. Safety, confidence and anomaly event types are mapped to `x.agentwatch_legacy.*` kinds: they are **v0.2 interpretations, not observations**. `conf_observation` is lowered for timestamps without a clock source, and `declared_parents` is empty unless `parent_event_id` is set.
- `events/registry.py`: normalizer registry and interpretation records (`interpretations` table).

**Tests:**

- **golden files** per source in `tests/v3/golden/{otel,langchain,claude_code,legacy}/`: input observations → expected events (JSON). Regenerated only with `--update-golden` and reviewed in the PR;
- property: every observation is either represented in ≥ 1 event's `derived_from`, left pending with a diagnostic, or explicitly ignored with a reason. None are silently dropped;
- property: all source fields end up in mapped fields or `attributes` (round-trip retention, H1);
- determinism: normalizing twice yields identical event ids and content.

**Acceptance:** H1 kind-coverage measured on the golden corpora plus AWBench S1/S2 output, reported in a results file.

### WP3: Sensors (`panoptis/sensors`)

**Files:**

- `sensors/base.py`: the `Sensor` protocol and `SensorContext` (instance id, `source_seq` counter, clock info).
- `sensors/buffer.py`: bounded ring buffer, background flusher thread (**not** asyncio, so it works in sync hosts), drop counter, periodic `sensor.health` observation, and a sampling policy hook (adapted from `tracing/sampling.py`, with the decision recorded).
- `sensors/exporters.py`: `HttpExporter` (batched NDJSON POST, gzip, retry with jitter, adapted from `core/http_forwarder.py`), `FileExporter` (NDJSON, rotating), `DirectStoreExporter` (embedded mode).
- `sensors/registry.py`: entry-point discovery (`panoptis.sensors`); `auto_attach(obj)` reusing the MRO detection in `core/watcher.detect_framework_label`. Passive only.
- `sensors/otel/`:
  - `receiver.py`: decodes OTLP/HTTP protobuf and JSON into observations, one per span, carrying resource and scope attributes;
  - `span_processor.py`: an OTel SDK `SpanProcessor` for in-process capture without a collector;
  - `import_file.py`: OTLP JSON dumps.
- `sensors/langchain/handler.py`: a new callback handler emitting raw callback payloads with `run_id`, `parent_run_id`, tags and metadata. Includes the retriever callbacks, which v0.2 misses.
- `sensors/claude_code/`: a transcript and stream-json line reader (live tail and file import), keeping the original timestamps when present. Reuses the parsing knowledge in `adapters/claude_code.py`.
- `sensors/legacy/`: `AgentEvent` → observation translator. It is wired into the legacy `/api/v1/events` route as a **tee** (after the legacy publish; failures logged and counted, never raised to the client).
- Stretch: `sensors/openai/`, `sensors/anthropic/` (httpx event-hook wrappers on SDK clients).

**Tests:**

- each sensor, against recorded fixtures;
- buffer drop accounting under a stress test (produced = stored + dropped, exactly);
- a sensor never raises into the host (fault-injection test with a failing exporter);
- the import-linter contract: sensors do not import server dependencies.

**Overhead benchmark** (adapting `benchmarks/bench_overhead.py`): added p50/p99 latency per callback, and RSS delta, for the langchain and otel sensors. Results are recorded, and the budgets for later gating are proposed from them.

### WP4: Entities, artifacts, runs (`panoptis/entities`, `panoptis/runs`)

**Files:**

- `entities/model.py`: `Entity`, `EntityKind`, `EntityResolution`, `Artifact`, `ResolutionBasis`.
- `entities/artifacts.py`: canonical bytes for artifact values (text normalization rules documented); `artifact_id = HMAC-SHA256(tenant_key, bytes)`; preview (truncated, redacted); blob storage; optional minhash fingerprint (pure-python, versioned).
- `entities/resolvers/exact.py`: deterministic canonical keys:
  - `model:<provider>/<model>` (from GenAI attrs, LangChain serialized, Claude Code model field);
  - `tool:<namespace>/<name>`;
  - `service:<service.name>@<service.namespace>`;
  - `process:<host>/<pid>/<start_time>`;
  - `db:<system>/<name>`;
  - `human:<declared id>`.
  
  Confidence is 1.0 with basis `EXACT_KEY`.
- `entities/resolvers/heuristic.py`: a small, explicit rule set, such as unifying `gpt-4o-2024-08-06` with the `gpt-4o` alias *only as an alias relation*, never a merge. Confidence < 1 and basis `HEURISTIC`.
- `runs/segmentation.py`: declared roots in priority order (OTel trace root → LangChain root run → Claude Code session → legacy session_id). A run closes on an explicit end, or after inactivity T (configurable). `completeness` = the fraction of events whose declared parents resolved.
- `runs/fingerprint.py`: `ConfigFingerprint` observed from events (models, tools, prompt artifact ids for system prompts, `service.version` / git sha when present). `SystemVersion` upsert by fingerprint hash plus declared labels.

**Tests:**

- canonical key golden tests;
- a tenant-isolation test for artifact ids (same bytes, different tenants → different ids);
- run segmentation on interleaved multi-run fixtures;
- fingerprint stability (same config → same version id).

### WP5: Runtime pipeline (`panoptis/runtime`)

**Files:**

- `runtime/cursor.py`: a durable per-tenant, per-stage cursor over `obs_id` (a ULID, so it is ordered).
- `runtime/pipeline.py`: stages `normalize → resolve → segment_runs → persist`. Each stage is idempotent; windowing supports pairing; late-arriving observations are handled up to a configurable lateness, and later arrivals produce events via a re-normalization of the affected window under the *same* interpretation (deterministic ids make this an upsert of identical rows plus new ones).
- `runtime/embedded.py`: a synchronous engine for SQLite (used by the CLI, tests and AWBench).
- `runtime/worker.py`: a Celery task wrapper for server mode (reuses `panoptis/tasks.py`).
- `runtime/reprocess.py`: re-derives a tenant or time range under a new normalizer or resolver version. Creates a new `interp_id`, marks the old one superseded, and emits a diff summary (counts by kind, changed, added, removed).

**Tests:**

- idempotency: running the pipeline twice yields no duplicates;
- out-of-order and late arrival;
- a crash mid-batch followed by a resume;
- the reprocess diff on a golden corpus with an intentionally changed mapping.

### WP6: API and CLI

**API** (new `api/app.py` factory; legacy routes moved verbatim into `api/routers/legacy_v1.py`; `api/server.py` becomes a thin compatibility import):

| Method | Route | Notes |
|---|---|---|
| POST | `/v3/observations` | NDJSON or JSON batch of envelopes; auth and tenant as today; 202 with accepted, duplicate and rejected counts |
| POST | `/v1/traces` | OTLP/HTTP receiver (protobuf and JSON), standard path, so OTel exporters work unchanged |
| GET | `/v3/runs` | Filters: time, system_version, status. Cursor pagination. |
| GET | `/v3/runs/{run_id}` | Includes `completeness`, fingerprint and interpretation ids |
| GET | `/v3/runs/{run_id}/events` | `?interp=` (default: active); includes confidences |
| GET | `/v3/events/{event_id}` | With `derived_from` observations (payload access permission-checked) |
| GET | `/v3/observations/{obs_id}` | Raw evidence, plus the segment inclusion proof when sealed |
| GET | `/v3/entities`, `/v3/entities/{id}` | Includes resolutions |
| GET | `/v3/evidence/verify` | Admin role; runs verification over a range |
| GET | `/v3/interpretations` | Lists interpretations with their status |

Every response that includes derived data carries `derived_by` and `maturity`. Phase 1 normalizers and resolvers are registered as `VALIDATED` only after WP2's golden tests and the H1 results exist; until then they are `EXPERIMENTAL`.

**CLI** (new modules under `cli/commands/`, registered into the existing Typer app with no removals):

```text
panoptis ingest <path> [--format otlp-json|ndjson|claude-code|legacy-jsonl] [--store sqlite:///aw.db]
panoptis runs [--since …] [--store …]
panoptis events <run_id> [--interp …] [--format table|json]
panoptis show <event_id>              # event + raw evidence chain
panoptis evidence verify [--from-segment …]
panoptis evidence import-legacy       # agent_events → observations (source_kind legacy.agent_event.pg)
panoptis reprocess --normalizer <name>@<version> [--since …]
panoptis sensors list
```

### WP7: AWBench skeleton (`benchmarks/awbench`)

- `REGISTRY.yaml` schema, runner skeleton, process isolation between the system under test and the AgentWatch pipeline, and results writer (git SHA, seeds, environment).
- `stubs/model.py`: a deterministic stub chat model (seeded; scripted tool-call policy; configurable failure modes). `stubs/tools.py`, `stubs/retriever.py`.
- **S1** (raw SDK-style tool loop, OTel GenAI instrumentation via the in-process span processor) and **S2** (LangGraph RAG with memory write-back, LangChain sensor), both on stubs. Ground truth emitted: the true dependency graph (for Phase 2 H2) and event labels.
- Perturbations implemented in Phase 1: `tool_timeout`, `corrupted_retrieval`, `duplicate_memory`. They exist to exercise the harness, and are evaluated in later phases.
- Task `h1_normalization`: kind coverage and cross-source equivalence. S1's logic also runs under LangChain instrumentation to create the paired corpus.

**Acceptance:** `python -m benchmarks.awbench run --task h1_normalization` produces a committed results file offline in CI (< 5 min).

### WP8: TIMELINE-lite (frontend)

- A new route `/v3/runs` (list) and `/v3/runs/[id]`:
  - an event table with columns (t_start, duration, kind, operation, actor, object, status, tokens, confidences);
  - swimlanes by actor (simple SVG; no graph library yet);
  - an inspector drawer showing the normalized event ⇄ raw observations (JSON) ⇄ interpretation and maturity badge.
- Reuse `lib/api` and react-query. No changes to legacy pages.
- Jest tests for the inspector rendering, with fixtures from golden events.

---

## 3. Sequencing (indicative; single team)

| Week | Focus |
|---|---|
| 1 | M0 (clean `main`), WP0, WP1 models + canonical + SQLite store |
| 2 | WP1 segments/verify/blobs/PG + trigger; WP2 model + legacy normalizer; WP7 stubs |
| 3 | WP3 buffer/exporters + otel receiver/processor; WP2 otel_genai normalizer + golden |
| 4 | WP3 langchain + claude_code sensors; WP2 their normalizers; WP4 artifacts + exact resolvers |
| 5 | WP4 runs/fingerprints; WP5 pipeline + embedded engine + reprocess |
| 6 | WP6 API + CLI; WP7 S1/S2 end-to-end + H1 task; overhead benchmark |
| 7 | WP8 TIMELINE-lite; hardening; docs; Phase 1 exit review; optional M2 quarantine start |

---

## 4. Risks and mitigations

| Risk | Mitigation |
|---|---|
| OTel GenAI semantic conventions still evolving | Normalizer versioned, with a mapping table per semconv version; unknown attributes preserved; reprocess on change |
| Pairing ambiguity (missing end callbacks, crashes) | Explicit `UNKNOWN` status with a diagnostic; `completeness` metric surfaced per run |
| SQLite write contention in embedded mode | WAL, single writer thread, batching; documented as single-process |
| Throughput lower than hoped on PG | SQLAlchemy Core bulk insert / `COPY`; partition `observations` by month (deferred until measured) |
| Scope creep into Phase 2 (graphs) | Relations explicitly out of scope; `declared_parents` stored raw, to be resolved in Phase 2 |
| Legacy tee destabilizing `/api/v1/events` | The tee runs after the legacy publish, is wrapped, and is behind a feature flag `AGENTWATCH_V3_TEE` (default on in dev, off in prod until validated) |
| Payload privacy in new store | Edge redaction wired in WP3 buffer, using the existing `security/redaction.py`; encryption keys (ADR-0011) are a Phase 1 stretch. If deferred, v3 server mode is marked non-production for regulated data |

---

## 5. Phase 1 exit criteria

All of the following must hold, each with a linked artefact:

1. **Heterogeneous → common stream:** AWBench S1 (OTel), S2 (LangChain), a recorded Claude Code transcript, and a legacy `AgentEvent` JSONL all ingest into one store and are queryable through `/v3/runs/{id}/events` and `panoptis events`.
2. **Immutability:** `panoptis evidence verify` passes on the full corpus. The mutation property tests pass. The PG trigger test passes.
3. **Re-interpretation:** a normalizer version bump plus `panoptis reprocess` produces a new interpretation with a diff summary. Raw evidence is byte-identical before and after (hash comparison).
4. **Linkage captured:** for sources that declare parents (OTel, LangChain, Claude Code), ≥ 99% of declared parent ids are present in `declared_parents` (this is capture, not resolution). This proves D1 is fixed at the source.
5. **H1 measured:** results file committed with kind coverage and cross-source equivalence. The numbers are reported whatever they turn out to be. If the threshold is missed, the event algebra is revised before Phase 2.
6. **Overhead measured:** sensor p50/p99 overhead results committed. Budgets are proposed from them.
7. **Passivity:** no v3 code path can raise into, block or alter the observed application (fault-injection tests pass).
8. **Legacy intact:** the full existing test suite passes unchanged. The legacy API and UI still work.

---

## 6. Open decisions to settle during Phase 1

These do not block the start of work. Each is recorded as an ADR when decided.

- Pydantic vs `msgspec` for hot-path envelope encoding (measure in WP1).
- Artifact text canonicalization rules (whitespace and Unicode normalization) and their effect on content-match recall. Decide with the WP7 corpora.
- Whether the Claude Code sensor should also read the local transcript store (`~/.claude/projects/…`) as a passive file sensor. This needs a privacy review.
- Default payload capture policy for prompts and completions: full, hashed-only, or preview. It is a privacy versus provenance-power trade-off. Make it per-tenant configurable, and document the default.
