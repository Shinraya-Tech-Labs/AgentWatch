# AgentWatch v3 — Target Architecture

Status: **IMPLEMENTED on `architecture/v3`** (see section 9 for deviations from the original proposal) · Companion documents: [CURRENT_ARCHITECTURE](CURRENT_ARCHITECTURE.md), [MIGRATION_MAP](MIGRATION_MAP.md), [RESEARCH_HYPOTHESES](RESEARCH_HYPOTHESES.md), [ADRs](adr/), [PHASE1_PLAN](PHASE1_PLAN.md)

---

## 0. What v3 is

AgentWatch v3 is **an instrument**: a passive sidecar system that reconstructs, from heterogeneous runtime observations, a queryable, versioned, uncertainty-annotated model of how an AI-native software system behaves. It uses that model to explain, compare, replay and (experimentally) forecast behaviour.

The research question that governs every component:

> Can the behaviour of a complex AI-native software system be reconstructed from observable events into a causal, temporal and partially executable system model that can explain past behaviour and estimate future or counterfactual trajectories?

The project's research framing is **System Mechanistic Interpretability**: studying how computation emerges *across* an AI-native system (models, tools, memory, services, humans), as opposed to inside one model. AgentWatch uses this as a framing and does not claim to have originated a field.

Every component in this document is tied to **a capability** and **a way to measure whether that capability works**. Components that fail that test are not in this document.

### Non-goals

- **Not a controller.** v3 core never blocks, routes, caches, retries or rolls back the observed system ([ADR-0002](adr/0002-passive-sidecar.md)).
- Not an agent framework, a guardrail, a prompt firewall, or an LLM-as-judge scoring service.
- Not a replacement for Jaeger, Tempo or Grafana. OTel is one input among many ([ADR-0004](adr/0004-computational-event-model.md)).
- Not a microservice fleet ([ADR-0012](adr/0012-modular-monolith.md)).

---

## 1. Architectural planes

```text
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ F. INTERFACE PLANE        API (REST/WS/MCP) · CLI · Frontend (LIVE MAP TIMELINE LAB   │
│                           GENOME COMPARE QUERY) · Query engine                       │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ E. EXPERIMENT PLANE       Replay L0–L4 · Timelines/branches · Counterfactual ·        │
│                           AWBench perturbation harness                               │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ D. MODEL PLANE            Latent state · Motifs · Genome · Drift · Causal hypotheses  │
│  (derived, versioned)     · Forecasting · Behavioural distance                        │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ C. STRUCTURE PLANE        Execution graph · Information graph · Causal graph          │
│  (derived, versioned)     · Provenance · Temporal index · Runs / SystemVersions       │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ B. INTERPRETATION PLANE   Normalizers (obs → ComputationalEvent) · Entity/Artifact    │
│  (derived, versioned)     resolution · Run segmentation                               │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ A. EVIDENCE PLANE         Sensors → Observation envelope → append-only, hash-sealed   │
│  (immutable)              ObservationStore + content-addressed BlobStore              │
└──────────────────────────────────────────────────────────────────────────────────────┘
       ▲ strictly one-way data dependency: each plane reads only planes below it
```

Rules that apply across planes:

1. **Only the Evidence plane is immutable.** Every other plane is a *function* of evidence plus a versioned configuration. You can delete everything above plane A and rebuild it ([ADR-0003](adr/0003-immutable-evidence-versioned-interpretation.md)).
2. **Every derived record names what produced it.** The record carries `derived_by = (component, version, config_hash)` and references to the evidence it rests on.
3. **Every inferred quantity carries its uncertainty and basis** ([ADR-0007](adr/0007-evidence-classes-and-uncertainty.md)).
4. **Every analyzer has a maturity label:** `EXPERIMENTAL`, `VALIDATED` or `PRODUCTION`. The label is enforced in code and surfaced by the API ([ADR-0010](adr/0010-maturity-labels.md)).
5. **Planes are packages, not services.** One engine process plus optional workers.

### A. Evidence plane (was: "Observation plane")

*Capability:* acquire signals from any source without perturbing the observed system, and keep them exactly as received.
*Measured by:* sensor overhead (added latency p50/p99, CPU, memory), loss rate under load, and hash-chain verification.

- **Sensors** are plugins that turn a source into `Observation` envelopes. A sensor's only job is faithful capture plus declared identifiers (span ids, run ids, tool-use ids). Sensors never interpret.
- **Transport:** in-process buffer → batched HTTP/OTLP → ingest endpoint. The alternatives are file-based (JSONL, OTLP dump, Claude Code transcripts) for offline import, or an embedded mode that writes directly to a local store.
- **Store:** an append-only `ObservationStore` plus a content-addressed `BlobStore` for large payloads. Observations are grouped into sealed, hash-chained segments.
- **Edge redaction:** redaction happens *in the sensor or at ingest, before storage*. Each observation records a `redaction_manifest` (which fields were redacted, by which detector version). Stored evidence is never mutated after the fact ([ADR-0011](adr/0011-privacy-redaction-and-erasure.md)).

Sensor families. Phase 1 builds the families marked ●. The rest are designed for but not built.

| Family | Source | Declared identifiers used for linkage |
|---|---|---|
| ● `otel` | OTLP/HTTP (protobuf + JSON), OTLP file dumps; GenAI semantic conventions mapped | trace_id, span_id, parent_span_id, links |
| ● `legacy` | v0.2 `AgentEvent` (all existing adapters keep working) | session_id, event ids (parents usually absent — see D1) |
| ● `langchain` / `langgraph` | Callback handler (rewritten) | run_id, parent_run_id |
| ● `claude_code` | stream-json / transcript files | session_id, message ids, tool_use_id |
| ◐ `openai`, `anthropic` | SDK client wrapping (httpx event hooks) | request ids, tool_call ids |
| ○ `mcp` | MCP client/server message tap | JSON-RPC ids |
| ○ `autogen`, `crewai`, `openai_agents`, `smolagents` | framework callbacks | framework ids |
| ○ `http`, `grpc`, `postgres`, `redis`, `vector_db`, `queue` | client library hooks / proxies | request ids, W3C traceparent |
| ○ `process`, `filesystem`, `container`, `kubernetes`, `gpu` | OS/cluster telemetry | pid, cgroup, pod uid |

### B. Interpretation plane

*Capability:* turn raw observations into a common event stream and resolve who and what is involved.
*Measured by:* normalization coverage (% of observations mapped to a non-`UNKNOWN` kind), linkage completeness (% of events whose source declared a parent that was resolved), entity-resolution precision and recall on AWBench ground truth.

- **Normalizers** are pure functions `list[Observation] → list[ComputationalEvent]`, versioned. They pair start and end signals into interval events, map source vocabularies onto the event algebra, and extract effects (reads and writes of artifacts).
- **Entity resolvers** map identifiers onto `Entity` and `Artifact` records, with a confidence for each mapping.
- **Run segmentation** groups events into `Run`s using declared roots (trace root, session, run id) or inferred boundaries, and assigns each run to a `SystemVersion`.

### C. Structure plane

*Capability:* reconstruct each run as computational structure, and answer where information came from and what depended on it.
*Measured by:* graph recovery F1 against AWBench ground-truth dependency graphs, provenance-query correctness on synthetic lineage, query latency.

- One **relation store** holding typed, possibly n-ary relations (hyperedges), partitioned into three **views**: `EXECUTION`, `INFORMATION` and `CAUSAL` ([ADR-0005](adr/0005-three-views-one-relation-store.md)).
- **Provenance engine:** lineage queries over the INFORMATION view (origin, derivation depth, transformation chain, dependents).
- **Temporal index:** interval tree / sorted index per run for time-window queries and `TEMPORALLY_PRECEDES` computation (never materialised as O(n²) edges).

### D. Model plane

*Capability:* describe behaviour at a level above individual runs: states, motifs, genomes, drift, causal hypotheses, forecasts.
*Measured by:* each analyzer's AWBench task (see [RESEARCH_HYPOTHESES](RESEARCH_HYPOTHESES.md)). An analyzer cannot be promoted from `EXPERIMENTAL` without that measurement.

### E. Experiment plane

*Capability:* test hypotheses by replaying, branching and perturbing executions.
*Measured by:* replay fidelity at each level, reproducibility confidence calibration, and counterfactual prediction error on held-out interventions.

### F. Interface plane

API, CLI, frontend and query engine. Every answer includes evidence references and confidences. No free-text explanation is ever returned without the structured evidence behind it ([ADR-0009](adr/0009-deterministic-first-llm-bounded.md)).

---

## 2. Domain model

The model has three layers of *records*, each with its own identity and lifecycle.

```text
EVIDENCE (immutable)      Observation ──► BlobRef
                               │ derived_from
INTERPRETATION (versioned)     ▼
                          ComputationalEvent ──► EntityRef / ArtifactRef
                               │                     ▲
STRUCTURE / MODEL (versioned)  ▼                     │
                          Relation (hyperedge) ──────┘   Run · SystemVersion
                          StateEstimate · MotifInstance · Genome · CausalHypothesis
EXPERIMENT                Timeline · Branch · Experiment · Intervention · ReplayResult
```

Terminology note: the prompt's semantic category "observation" (a system perceiving external input) is renamed **`EXTERNAL_INPUT`** so that it does not collide with `Observation`, the raw evidence record.

### 2.1 Evidence

```python
class Observation:                       # immutable; never updated, never deleted except by crypto-shred
    obs_id: ULID                         # time-sortable
    tenant_id: str
    sensor: SensorRef                    # (sensor_type, sensor_version, instance_id)
    source_kind: str                     # "otlp.span", "langchain.callback", "claude_code.stream_json", "legacy.agent_event"...
    source_seq: int | None               # per-sensor-instance monotonic sequence → ordering + loss detection
    idempotency_key: str                 # sha256(sensor.instance_id, source_seq | payload_sha256)
    observed_at: Timestamp               # when the source says it happened
    received_at: Timestamp               # when AgentWatch received it
    clock: ClockInfo                     # source clock id (host/process), declared precision, skew estimate if known
    content_type: str                    # "application/json", "application/x-protobuf"...
    payload: InlineBytes | BlobRef       # exactly what was received (post edge-redaction)
    payload_sha256: str
    declared_ids: dict[str, str]         # trace_id, span_id, parent_span_id, run_id, parent_run_id, tool_use_id…
    redaction_manifest: RedactionManifest | None
    sampling: SamplingInfo | None        # if the sensor sampled, record the decision + rate (statistics must reweight)
    segment_id: str                      # sealed hash-chained segment membership
```

### 2.2 Interpretation: the computational event algebra

```python
class ComputationalEvent:
    event_id: UUID                       # deterministic: uuid5(normalizer.name, sorted(obs_ids), local_index)
                                         # stable across normalizer *versions* when derivation is unchanged
    interpretation: InterpretationRef    # (normalizer, version, config_hash)
    derived_from: list[ObsId]            # provenance to raw evidence (≥1)
    run_id: RunId | None

    kind: EventKind                      # core vocabulary (below); extensible via namespaced kinds "x.vendor.kind"
    facets: set[str]                     # multi-label semantic categories: "inference", "planning", "retrieval"…
    actor: EntityRef | None              # who performed it (process, service, model endpoint, human)
    operation: str                       # namespaced verb: "llm.chat", "tool.call:search_web", "sql.select"…
    object: EntityRef | None             # principal target (tool, db, memory store, peer)

    inputs:  list[ArtifactRef]           # content-addressed values consumed
    outputs: list[ArtifactRef]           # content-addressed values produced
    effects: list[Effect]                # READ | WRITE | CREATE | DELETE | TRANSFORM | SEND | RECEIVE on an entity/artifact

    time: TemporalContext                # start, end (None = point/open), uncertainty_ms, ordering_key (sensor, seq)
    context: ExecutionContext            # trace_id, span_id, process, host, container, deployment, system_version
    parents: list[DeclaredLink]          # parent references *declared by the source* (e.g., parent_span_id) – resolved later

    status: OK | ERROR | CANCELLED | TIMEOUT | UNKNOWN
    error: ErrorInfo | None
    resources: ResourceUsage | None      # tokens in/out, cost (with pricing-table version), latency, bytes, gpu-s

    confidence: EventConfidence          # observation (did it happen as described?) + attribution (is actor/object right?)
    attributes: dict[str, JSON]          # namespaced, source-specific remainder — never dropped
```

**Core `EventKind` vocabulary** (closed core, open extension). The core is kept small on purpose. Semantic richness goes into `facets` and `effects`, not into kind proliferation.

| Kind | Meaning | Typical effects |
|---|---|---|
| `MODEL_INVOCATION` | Call to a generative/embedding/classifier model | READ inputs, CREATE outputs |
| `TOOL_INVOCATION` | Invocation of a declared tool/function | READ/WRITE on tool object |
| `RETRIEVAL` | Query over an index/store returning items | READ store, CREATE result set |
| `MEMORY_ACCESS` | Read/write of an application-level memory | READ/WRITE memory entity |
| `MESSAGE` | Communication between components (incl. human) | SEND/RECEIVE |
| `DELEGATION` | Hand-off of a task/goal to another component | SEND + ownership transfer |
| `TRANSFORMATION` | Deterministic data transformation (parse, summarise-by-code, chunk) | READ → CREATE |
| `STATE_MUTATION` | Change to durable state outside memory (db, file, config) | WRITE/DELETE |
| `EXTERNAL_IO` | Interaction with external system (HTTP, API, browser, shell) | SEND/RECEIVE |
| `EXTERNAL_INPUT` | Input entering the system boundary (user prompt, webhook, sensor) | CREATE |
| `SYNCHRONIZATION` | Wait/join/lock/barrier/queue hand-off | — |
| `LIFECYCLE` | Start/stop of process, run, component | — |
| `FAILURE` | Error surfaced as its own event (not just status) | — |
| `RECOVERY` | Retry/fallback/repair action | — |
| `UNKNOWN` | Normalizer could not classify; attributes preserved | — |

Facets carry categories that span kinds, such as `inference`, `planning`, `artifact_creation`, `retry` and `human_in_loop`. A `MODEL_INVOCATION` can carry facet `planning`, for example. Facets are assigned by normalizers (declared) or by analyzers (inferred, versioned, with confidence).

### 2.3 Entities and artifacts

```python
class Entity:                            # things that persist across events
    entity_id: UUID
    kind: EntityKind                     # PROCESS, SERVICE, MODEL, MODEL_ENDPOINT, TOOL, MEMORY_STORE, INDEX, DATASET,
                                         # DATABASE, QUEUE, FILE, EXTERNAL_ENTITY, HUMAN, ENVIRONMENT, COMPONENT
    canonical_key: str                   # deterministic identity key, e.g. "model:anthropic/claude-sonnet-5"
    display_name: str
    attributes: dict[str, JSON]
    first_seen / last_seen: Timestamp

class EntityResolution:                  # versioned mapping; an entity can be split/merged by a later resolver version
    event_id, role ("actor" | "object" | "effect_target"), entity_id
    confidence: float; basis: EXACT_KEY | HEURISTIC | MODEL; resolver: (name, version)

class Artifact:                          # immutable values: prompts, completions, tool args/results, documents, chunks
    artifact_id: str                     # HMAC-SHA256(tenant_key, canonical_bytes) — dedup per tenant, no cross-tenant oracle
    media_type: str; size_bytes: int
    blob: BlobRef; preview: str | None   # preview is redacted
    fingerprints: {minhash?: bytes, simhash?: int}   # for near-duplicate detection (INFORMATION view), versioned
```

Node types from the brief map onto this model as follows:

- `Process`, `Service`, `Resource`, `Dataset`, `ExternalEntity`, `Environment`, `HumanInteraction` → `Entity` kinds.
- `ModelInvocation`, `ToolInvocation`, `Message`, `Failure` → `ComputationalEvent` kinds.
- `Artifact`, `Evidence`, `Memory` items → `Artifact` (plus `MEMORY_STORE` entity).
- `Belief`, `Decision`, `Goal`, `State` → **derived** nodes produced by Model-plane analyzers, with confidence. They are never assumed to be directly observable.

### 2.4 Relations (hyperedges) and the three views

```python
class Relation:
    rel_id: UUID
    view: EXECUTION | INFORMATION | CAUSAL
    type: RelationType                   # see table
    tail: list[NodeRef]                  # ≥1 sources  (hyperedge when len > 1)
    head: list[NodeRef]                  # ≥1 targets
    basis: DECLARED | CONTENT_MATCH | TEMPORAL | HEURISTIC | STATISTICAL | INTERVENTIONAL | MODEL
    evidence_class: EvidenceClass | None # required for CAUSAL view (ADR-0007)
    confidence: float                    # [0,1]; `calibrated: bool` flag alongside
    causal_strength: Estimate | None     # effect size + interval, CAUSAL view only
    valid_from / valid_until: Timestamp | None
    evidence: list[EvidenceRef]          # obs ids, event ids, relation ids, experiment ids, analysis run ids
    derived_by: DerivationRef            # (component, version, config_hash)
```

`NodeRef` points to an event, entity, artifact or derived node. A hyperedge such as `{memory_12, prompt_4, model_version_8, retrieval_91} → decision_42` is a single `Relation` with four tail members.

| View | Question | Relation types | Typical basis |
|---|---|---|---|
| **EXECUTION** | What happened, in what structure? | `CONTAINS` (span nesting), `TRIGGERS`, `RESPONDS_TO`, `DELEGATES_TO`, `RETRIES`, `SYNCHRONIZES_WITH`, `TEMPORALLY_PRECEDES` (computed on demand) | DECLARED, TEMPORAL |
| **INFORMATION** | How did information move and change? | `DERIVES_FROM`, `COPIES`, `TRANSFORMS`, `RETRIEVES_FROM`, `WRITES_TO`, `READS_FROM`, `SUMMARIZES`, `QUOTES` | CONTENT_MATCH, DECLARED, HEURISTIC |
| **CAUSAL** | What appears to have influenced what? | `CAUSES`, `ENABLES`, `INFLUENCES`, `SUPPORTS`, `CONTRADICTS`, `DEPENDS_ON` | STATISTICAL, INTERVENTIONAL (+ DECLARED/CONTENT_MATCH as *OBSERVATIONAL* dependency evidence) |

**Why three views:** "A called B" (execution), "B's output contains text from document D" (information) and "if D had been different, B's decision would have changed" (causal) are different claims with different evidentiary standards. Merging them is how v0.2's `CausalGraph` ended up labelling asserted edges as causes.

### 2.5 Runs, systems, versions

```python
class System:         system_id, name, owner, declared_components
class SystemVersion:  version_id, system_id, fingerprint: ConfigFingerprint, labels (deployment, git sha, env)
class ConfigFingerprint:  models: set[canonical_key], prompt_artifacts: set[artifact_id], tool_set: set[key],
                          code_rev, orchestrator, memory_strategy, extra: dict   # observed, not only declared
class Run:            run_id, system_version_id, root: (kind, id), started_at, ended_at, outcome: Outcome | None,
                      segmentation: DerivationRef, completeness: float     # fraction of expected linkage present
```

The `SystemVersion` fingerprint is partly **observed**. If a run invokes a model that the declared config never mentions, the observed fingerprint differs, and that difference is itself a drift signal.

### 2.6 Model-plane records (defined now, built in later phases)

```python
class StateEstimate:     run_id, t_from, t_to, state: str, distribution: dict[str,float], model: DerivationRef
class MotifDefinition:   motif_id, name, version, kind: RULE | GRAPH_QUERY | STATISTICAL | DISCOVERED,
                         spec (pattern), maturity, benchmark_refs
class MotifInstance:     motif_id, run_id, bound_nodes: list[NodeRef], t_span, confidence
class MotifStats:        motif_id, scope (system_version | window), support, precision_est, outcome_association
class Genome:            scope, feature_vector: dict[feature_id, value], feature_defs_version, n_runs, ci per feature
class CausalHypothesis:  relation_id, proposer (analyzer | human | llm), status: PROPOSED|TESTED|SUPPORTED|REFUTED,
                         tests: list[ExperimentRef]
class Forecast:          run_id, at_event, horizon, outcome_distribution, calibration_model, n_neighbours
```

### 2.7 Experiment records

```python
class Timeline:      timeline_id, run_id | branch_id, event sequence (ordered refs)
class Branch:        branch_id, parent_timeline, fork_at_event, intervention: Intervention, replay_level: L0..L4,
                     status, reproducibility: ReproducibilityReport
class Intervention:  target: NodeRef, kind: REPLACE_OUTPUT | DROP | DELAY | DUPLICATE | CORRUPT | SUBSTITUTE_MODEL…,
                     spec
class ReproducibilityReport:  level, nondeterminism_sources: list, confidence: float, basis
class Experiment:    experiment_id, hypothesis_ref?, benchmark_ref?, branches, results, seed, env capture
```

Every experiment-derived value is labelled with one of `OBSERVED`, `SIMULATED`, `MODEL_ESTIMATED` or `UNKNOWN`.

### 2.8 Key interfaces (Python protocols)

```python
class Sensor(Protocol):
    sensor_type: ClassVar[str]; version: ClassVar[str]
    def start(self, sink: ObservationSink) -> None: ...
    def stop(self, flush_timeout_s: float = 5.0) -> None: ...

class ObservationSink(Protocol):          # in-process buffer, HTTP exporter, or direct store writer
    def emit(self, obs: ObservationDraft) -> None: ...   # non-blocking; drops are COUNTED and reported, never silent

class ObservationStore(Protocol):
    def append(self, batch: Sequence[Observation]) -> AppendResult: ...      # idempotent on idempotency_key
    def get(self, obs_id) -> Observation: ...
    def scan(self, tenant, *, since=None, sensor=None, declared_id=None) -> Iterator[Observation]: ...
    def seal_segment(self) -> Segment: ...; def verify(self, from_segment=None) -> VerifyReport: ...
    # deliberately NO update() and NO delete()

class Normalizer(Protocol):
    name: ClassVar[str]; version: ClassVar[str]
    def accepts(self, obs: Observation) -> bool: ...
    def normalize(self, window: ObservationWindow) -> NormalizeResult: ...   # events + unresolved/pending obs

class EntityResolver(Protocol):
    name: ClassVar[str]; version: ClassVar[str]
    def resolve(self, events: Sequence[ComputationalEvent], ctx: ResolutionContext) -> list[EntityResolution]: ...

class Analyzer(Protocol):                 # anything in planes C/D
    name: ClassVar[str]; version: ClassVar[str]; maturity: ClassVar[Maturity]
    inputs: ClassVar[set[str]]            # declared dependencies, for incremental recomputation
    def run(self, scope: AnalysisScope, store: DerivedStore) -> AnalysisOutput: ...
```

---

## 3. Data architecture

### 3.1 Logical stores

| Store | Contents | Mutability | Phase |
|---|---|---|---|
| **EVIDENCE** | `observations`, `evidence_segments` | append-only, hash-sealed | 1 |
| **BLOB** | payloads > inline threshold, artifacts | content-addressed, write-once | 1 |
| **INTERPRETATION** | `interpretations`, `events`, `event_sources`, `entities`, `entity_resolutions`, `artifacts`, `runs` | versioned (new version = new rows; old versions retained until GC policy) | 1 |
| **GRAPH** | `relations`, `relation_members`, `relation_evidence` | versioned by `derived_by` | 2 |
| **STATE / MODEL** | `state_estimates`, `motif_*`, `genomes`, `causal_hypotheses`, `forecasts`, learned model artefacts (blob) | versioned | 4–9 |
| **EXPERIMENT** | `experiments`, `branches`, `timelines`, `interventions`, `benchmark_runs` | append-only records | 3+ |
| **OPERATIONAL** | tenants, api keys, users, audit log (existing) | normal CRUD + hash-chained audit | kept from v0.2 |

### 3.2 Physical choices ([ADR-0006](adr/0006-storage-relational-first.md))

- **One relational database** behind narrow store protocols:
  - **SQLite** for embedded, local and test use. A developer can run `panoptis` on a laptop with no services.
  - **PostgreSQL** for server mode. Reuses the existing compose, Helm and pgvector image.
- **Blob store:** local filesystem (`<data>/blobs/ab/cd/<hash>`) or an S3-compatible bucket.
- **No graph database.** Runs are bounded (10²–10⁵ events). Per-run analysis loads the run's relations into an in-memory graph library (candidates: `rustworkx` or `networkx`, chosen by the Phase 2 benchmark). Cross-run questions go through aggregated model-plane records (motif stats, genomes), not through arbitrary multi-run traversals.
- **No columnar store in Phase 1.** A Parquet export and DuckDB analytics path is added once COMPARE/GENOME queries over more than 10⁶ events are measured to be too slow in Postgres. The trigger condition is written in ADR-0006.
- **Vector retrieval** stays optional (pgvector). It is used only for artifact similarity and the NL query front-end, never as the system of record.

### 3.3 Core tables (Phase 1–2), abridged DDL

```sql
-- EVIDENCE --------------------------------------------------------------
CREATE TABLE observations (
  obs_id            TEXT PRIMARY KEY,           -- ULID
  tenant_id         TEXT NOT NULL,
  sensor_type       TEXT NOT NULL, sensor_version TEXT NOT NULL, sensor_instance TEXT NOT NULL,
  source_kind       TEXT NOT NULL,
  source_seq        BIGINT,
  idempotency_key   TEXT NOT NULL,
  observed_at       TIMESTAMPTZ, received_at TIMESTAMPTZ NOT NULL,
  clock             JSONB,
  content_type      TEXT NOT NULL,
  payload_inline    BYTEA,                      -- ≤ 64 KiB (configurable), else NULL
  payload_blob      TEXT,                       -- blob hash
  payload_sha256    TEXT NOT NULL,
  declared_ids      JSONB NOT NULL DEFAULT '{}',
  redaction         JSONB, sampling JSONB,
  segment_id        TEXT,                       -- NULL until sealed
  UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX ON observations (tenant_id, received_at);
CREATE INDEX ON observations USING GIN (declared_ids);          -- trace_id / run_id lookups (PG); SQLite: side table

CREATE TABLE evidence_segments (
  segment_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
  first_obs TEXT, last_obs TEXT, n_obs INT,
  merkle_root TEXT NOT NULL, prev_segment_hash TEXT NOT NULL, segment_hash TEXT NOT NULL,
  sealed_at TIMESTAMPTZ NOT NULL
);
-- DB-level guard (PG): REVOKE UPDATE, DELETE ON observations FROM panoptis_app; trigger rejects UPDATE.

-- INTERPRETATION --------------------------------------------------------
CREATE TABLE interpretations (
  interp_id TEXT PRIMARY KEY, component TEXT, version TEXT, config_hash TEXT,
  created_at TIMESTAMPTZ, status TEXT CHECK (status IN ('active','superseded','experimental'))
);
CREATE TABLE events (
  event_id UUID, interp_id TEXT REFERENCES interpretations,
  tenant_id TEXT NOT NULL, run_id UUID,
  kind TEXT NOT NULL, facets TEXT[], operation TEXT,
  actor_ref TEXT, object_ref TEXT,
  t_start TIMESTAMPTZ, t_end TIMESTAMPTZ, t_uncertainty_ms REAL, ordering_key TEXT,
  status TEXT, error JSONB, resources JSONB, context JSONB,
  inputs TEXT[], outputs TEXT[], effects JSONB, declared_parents JSONB,
  conf_observation REAL, conf_attribution REAL,
  attributes JSONB,
  PRIMARY KEY (event_id, interp_id)
);
CREATE TABLE event_sources (event_id UUID, interp_id TEXT, obs_id TEXT, PRIMARY KEY (event_id, interp_id, obs_id));
CREATE TABLE entities (entity_id UUID PRIMARY KEY, tenant_id TEXT, kind TEXT, canonical_key TEXT,
                       display_name TEXT, attributes JSONB, first_seen TIMESTAMPTZ, last_seen TIMESTAMPTZ,
                       UNIQUE (tenant_id, kind, canonical_key));
CREATE TABLE entity_resolutions (event_id UUID, interp_id TEXT, role TEXT, entity_id UUID,
                                 confidence REAL, basis TEXT, resolver TEXT, resolver_version TEXT);
CREATE TABLE artifacts (artifact_id TEXT, tenant_id TEXT, media_type TEXT, size_bytes BIGINT,
                        blob TEXT, preview TEXT, fingerprints JSONB, first_seen TIMESTAMPTZ,
                        PRIMARY KEY (tenant_id, artifact_id));
CREATE TABLE runs (run_id UUID PRIMARY KEY, tenant_id TEXT, system_version_id UUID, root_kind TEXT, root_id TEXT,
                   started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, outcome JSONB, completeness REAL, interp_id TEXT);

-- GRAPH (Phase 2) -------------------------------------------------------
CREATE TABLE relations (rel_id UUID PRIMARY KEY, tenant_id TEXT, run_id UUID NULL, view TEXT, type TEXT,
                        basis TEXT, evidence_class TEXT NULL, confidence REAL, calibrated BOOLEAN,
                        causal_strength JSONB, valid_from TIMESTAMPTZ, valid_until TIMESTAMPTZ,
                        derived_by TEXT, attributes JSONB);
CREATE TABLE relation_members (rel_id UUID, role TEXT CHECK (role IN ('tail','head')), ordinal INT,
                               node_kind TEXT, node_id TEXT, PRIMARY KEY (rel_id, role, ordinal));
CREATE INDEX ON relation_members (node_kind, node_id);           -- cone traversal entry point
CREATE TABLE relation_evidence (rel_id UUID, evidence_kind TEXT, ref_id TEXT);
```

### 3.4 Evidence integrity

- Observations are appended freely, with no per-row serialization.
- A sealer closes a **segment** every N observations or T seconds. It computes a Merkle root over `(obs_id, payload_sha256, idempotency_key)` in `obs_id` order and chains each segment to the previous one.
- This reuses the hash-chain approach of `governance/audit_log.py`. It moves the chain from rows to segments, because the per-row chain serializes all writers.
- `panoptis evidence verify` recomputes the chain end to end. An individual observation can be proven to be included with a Merkle proof.

### 3.5 Derivation and reprocessing

```text
observations ──► (normalizer vN) ──► events@interp_k ──► (resolver vM) ──► resolutions ──► (graph builders) ──► relations
                         ▲
    reprocess: new normalizer version ⇒ new interp_id; old interpretation retained & marked superseded;
               downstream analyzers declare inputs ⇒ incremental invalidation
```

Event IDs are deterministic (§2.2). As a result, references in relations, motifs and bookmarks usually survive a re-interpretation. When they do not, the diff between interpretations is itself queryable.

### 3.6 Ingestion runtime

- **Server mode:** the ingest endpoint validates and appends observations. A durable cursor, `(tenant, last_obs_id)`, drives the normalize → resolve → segment pipeline in-process or in a worker (the existing Celery/Redis stack). Graph builders and analyzers are scheduled per run on run-close, or on a debounce for live runs.
- **Embedded mode:** the same pipeline runs synchronously against SQLite. It is used for tests, notebooks, AWBench and local CLI use.
- **Backpressure:** sensors drop *after* bounded buffers fill. Each drop increments a counter that is itself emitted as an observation (`sensor.health`), so every loss is visible in the evidence.

### 3.7 Tenancy and privacy

- `tenant_id` appears on every row, and the store protocols scope every query by it. This reuses the `TenantRepository` pattern.
- Artifact IDs are HMAC-keyed per tenant, so two tenants holding the same content cannot detect each other.
- **Erasure vs immutability:** a payload that may contain subject data is encrypted with a per-subject or per-tenant data key. Erasure destroys the key (crypto-shredding), and the segment hashes still verify ([ADR-0011](adr/0011-privacy-redaction-and-erasure.md)).

---

## 4. Signature capabilities: design notes

The details are in [RESEARCH_HYPOTHESES](RESEARCH_HYPOTHESES.md). Here is how each capability sits on the domain model:

| Capability | Built from | Deterministic core | Where learning may enter | First measurable claim |
|---|---|---|---|---|
| Provenance | INFORMATION view, artifacts, content fingerprints | Exact hash lineage, declared data deps | Near-duplicate / paraphrase linking (versioned, with confidence) | Lineage recall on AWBench synthetic RAG pipelines |
| Graph diff / COMPARE | EXECUTION+INFORMATION per run | Structural alignment by (operation, object, depth, content hash) | Learned alignment (later) | Localize injected perturbation within top-k divergence points |
| Motifs | Graph queries + temporal patterns over events | Rule/graph-query detectors (loops, ping-pong, retrieval echo) | Unsupervised discovery (Phase 4+ research) | Detector precision/recall on AWBench injected motifs |
| Genome | Motif stats + structural statistics per SystemVersion | Feature extraction, bootstrap CIs | Feature selection | Distinguishes known config changes; stable across same-config reruns |
| Drift | Genome/feature distributions over windows | Two-sample tests with multiple-comparison control | Change-point models | Detect injected model swap / prompt change at fixed FPR |
| Causal cones | Relations (any view) + evidence classes | Reachability with per-edge evidence filter | — | Cone contains injected root cause (recall) at acceptable size |
| Root-cause ranking | Cones + cross-run contrasts + interventions | Ranking by evidence class then effect estimate | Causal discovery (PC/NOTEARS variants) as hypothesis source | Top-1/top-3 localization on AWBench |
| Replay L0–L2 | Timelines + captured artifacts | Deterministic re-rendering / mock substitution | — | L2 fidelity = % events reproduced identically with mocked I/O |
| Latent state | Event sequences | Interpretable baselines (rule states, HMM) | HMM/SSM/temporal models | Held-out log-likelihood & state-outcome association vs baseline |
| Counterfactual | Branches + L3 re-execution + similarity model | Only L3 results are `SIMULATED`; others `MODEL_ESTIMATED` | Outcome models over neighbours | Calibration against actual interventions (Brier/ECE) |
| Forecasting | Partial trajectories + historical runs | kNN over trajectory features | Sequence models | Calibrated outcome probabilities (ECE, Brier) vs base rate |

---

## 5. Query engine

- **Structured first:** a small, typed query API. Examples: `runs(filter)`, `events(run, where)`, `cone(node, direction, views, min_evidence_class, depth)`, `lineage(artifact)`, `diff(run_a, run_b)`, `motifs(scope)`, `genome(scope)`, `drift(a, b)`.
- It is exposed through REST, the CLI, Python and MCP (by adapting the existing `protocol/mcp_server.py`).
- **Natural language as a compiler:** an optional LLM translates a question into a *structured query plan*. The plan is executed deterministically. The answer is rendered from the results *with evidence references*. The LLM never authors facts, numbers or causal claims ([ADR-0009](adr/0009-deterministic-first-llm-bounded.md)). If the plan cannot be grounded, the response says so.
- **Explanation contract:** every explanation response is a structured object. Prose is optional.

  ```json
  { "claim": "...", "claim_type": "OBSERVED|DERIVED|ESTIMATED", "evidence": [refs], "evidence_class": "...",
    "confidence": {"value": 0.7, "calibrated": false, "basis": "..."}, "maturity": "EXPERIMENTAL", "caveats": [...] }
  ```

---

## 6. Frontend information architecture

The frontend is rebuilt around seven views. The shell, data layer and WebSocket code from `frontend/` are reused, and the pages are replaced.

| View | Purpose | Primary visual | Phase available |
|---|---|---|---|
| **TIMELINE** | Navigate one run's history; raw ⇄ normalized ⇄ derived side by side | Swimlanes per actor/entity over time; event inspector with evidence chain | 1 (lite), 2 |
| **MAP** | Behavioural topology of a system/run; cones | Force/layered graph (WebGL for >5k nodes); fade-out outside selected cone | 2 (run), 5 (cones) |
| **COMPARE** | Two runs / two versions | Aligned structural diff + feature deltas with CIs | 3 |
| **GENOME** | Motif registry + fingerprint per SystemVersion | Dense matrix (versions × features), motif instance browser | 4 |
| **LIVE** | Current behaviour | Streaming event rate by kind/entity, active runs, current state estimates | 2 (basic), 7 |
| **LAB** | Replay, branch, intervene | Timeline tree (branches), side-by-side branch diff, reproducibility badge | 6, 8 |
| **QUERY** | Ask; see plan, evidence and answer | Query → plan → evidence table → rendered claim | 3 (structured), 5+ (NL) |

Design principles:

- High information density, a monospace data grammar, and restrained colour. Colour is reserved for **evidence class** and **uncertainty**.
- Uncertainty is always visible: opacity or hatching scales with confidence, and uncalibrated values are marked.
- Every number is clickable down to its evidence.
- Every derived artefact displays its maturity badge.
- No gauges, no "AI health score" and no sci-fi chrome.

v0.2 pages `safety-lab`, `sandbox`, `security`, `policies`, `compliance`, `costs`, `memory`, `benchmark` and `multiagent` are not carried forward. `sessions/[id]` and `replay` are superseded by TIMELINE and LAB.

---

## 7. Repository restructure

This refines the brief's suggested tree after studying the code. The main changes:

- **Sensors live inside the package**, but the base install is dependency-light ([ADR-0008](adr/0008-sensor-plugins-light-sdk.md)).
- **Motifs, genome and drift are grouped** under `behaviour/`, because they share feature extraction and the scope/SystemVersion machinery.
- **v0.2 code moves under `panoptis/legacy/`** instead of being deleted.

```text
panoptis/                         # import name unchanged; distribution panoptis
  __init__.py                       # v3 public API; legacy names re-exported with DeprecationWarning
  _version.py

  evidence/                         # PLANE A — Observation, envelope, ObservationStore protocol, segments, verify, blob store
    model.py  store.py  segments.py  blobs.py  wire.py (JSON/OTLP envelope codecs)
  sensors/                          # PLANE A — plugins; each subpackage imports its framework lazily
    base.py  registry.py  buffer.py  exporters.py
    otel/  legacy/  langchain/  claude_code/  openai/  anthropic/  mcp/  (…more later)
  events/                           # PLANE B — ComputationalEvent, EventKind, Effect, normalizers/, pairing, interpretation registry
  entities/                         # PLANE B — Entity, Artifact, resolvers/, canonical keys, artifact hashing
  runs/                             # PLANE B — run segmentation, System/SystemVersion fingerprints
  graph/                            # PLANE C — Relation, views, builders/ (execution, information), temporal index, traversal, cones
  provenance/                       # PLANE C — lineage queries, provenance metrics (EXPERIMENTAL)
  compare/                          # PLANE D — alignment, graph diff, behaviour statistics
  behaviour/                        # PLANE D — motifs/ (registry, detectors), genome/, drift/, distance/
  causality/                        # PLANE D — hypotheses, evidence classes, discovery/, ranking
  state/                            # PLANE D — latent state estimators (Phase 7)
  forecasting/                      # PLANE D — (Phase 9)
  replay/                           # PLANE E — timelines, branches, L0–L2 replayers, reproducibility
  counterfactual/                   # PLANE E — (Phase 8)
  experiments/                      # PLANE E — experiment records, intervention specs
  query/                            # PLANE F — structured query API, planner, NL compiler (optional extra)
  storage/                          # backends: sqlite/, postgres/; alembic migrations; DerivedStore
  runtime/                          # pipeline orchestration, cursors, scheduler, embedded engine
  analysis/                         # Analyzer protocol, maturity registry, DerivationRef helpers
  api/                              # FastAPI app factory + routers/ (v3) ; auth, tenancy, rate limiting (kept)
  cli/                              # Typer app split into command modules
  legacy/                           # v0.2 modules moved here (safety, governance, memory product, orchestration runtime…)

benchmarks/
  awbench/                          # AWBench: systems/, perturbations/, ground_truth/, tasks/, runner, reports
  overhead/                         # sensor overhead (from bench_overhead.py)
  legacy/                           # reasoning-auditor benchmark (kept for reference)
research/                           # notebooks + write-ups; each linked to a hypothesis id; no production imports from here
frontend/                           # Next.js app, rebuilt views
docs/v3/                            # these documents, ADRs, metric definitions
deploy/                             # helm/, grafana/, compose (moved in Phase 2; not Phase 1 to avoid churn)
tests/
  v3/ (unit, property, golden) · legacy/ (existing suite, moved)
```

**Dependency rules**, enforced by an import-linter contract in CI:

- `evidence` imports nothing from AgentWatch.
- Each plane may import only lower planes.
- No v3 package imports `panoptis.legacy`.
- `sensors/*` may import only `evidence` (plus its own framework), so sensors run inside observed apps with a minimal footprint.

---

## 8. Scientific discipline built into code

- An `@analyzer(name, version, maturity=EXPERIMENTAL)` registry. The API response of every derived artefact includes `maturity` and `derived_by`.
- Promotion `EXPERIMENTAL → VALIDATED` requires a checked-in AWBench result file that meets pre-registered thresholds (see `RESEARCH_HYPOTHESES.md`). Promotion `VALIDATED → PRODUCTION` additionally requires performance and robustness budgets.
- Every metric has a definition document in `docs/v3/metrics/` before it is exposed. A metric without a definition cannot be registered.
- Confidence values carry `calibrated: bool`. Uncalibrated confidences render as ordinal only: low, medium or high, never "0.82".


---

## 9. As built (2026-09-25)

The architecture above is implemented. Where the implementation deviates from the proposal, the code wins and the change is recorded here.

| Proposal | As built | Why |
|---|---|---|
| Packages `evidence, events, entities, runs, graph, provenance, compare, behaviour, causality, state, forecasting, replay, counterfactual, experiments, query, storage, runtime, analysis` | All present at the top level of `panoptis/`, except replay, branching and counterfactuals, which live in `panoptis/lab/` | `panoptis/replay` is the v0.2 module. v3 must not shadow it during migration. |
| Alembic migrations | `storage/schema.py` with SQLAlchemy Core, a `schema_version` row, and DB triggers (SQLite and PostgreSQL) | Only one schema version exists so far. Alembic is added with the first incompatible change. |
| Incremental normalization | Incremental rebuild of the *correlation groups* touched by new observations (native `run_id`, OTel `trace_id`, Claude Code and legacy `session_id`), with automatic fallback to a deterministic full rebuild when correctness cannot be guaranteed (unknown key, run-less events, unresolved links). Tests assert incremental output equals full-rebuild output. | Full rebuild grows linearly with the store. One new run into a 4k-event store: 0.78 s incremental vs 8.8 s full on the same (loaded) machine. Entity aggregation is still linear. |
| Separate sensor package / `[server]` extra | Sensors live in `panoptis/sensors`. The base install still carries the server dependencies. | Packaging split (ADR-0008) is deferred to migration stage M2. |
| Encryption and crypto-shredding (ADR-0011) | Edge secret redaction plus manifests, and retention by authorized segment purge. No per-subject encryption yet. | Known limitation; see IMPLEMENTATION_STATE. |
| Graph library chosen by benchmark | Pure-Python traversal (`graph/traverse.py`) | Per-run graphs (10^4 relations) build in 0.2 s and traverse in 17 ms. No dependency was justified. |
| Causal view | Derived on demand from hypotheses with computed evidence classes (`causality/hypotheses.py`); never from structure | ADR-0007 |
| Artifacts | Content-addressed per tenant. Content relations are per run, and traversal is time-respecting. | ADR-0015 (found by AWBench) |
| API re-execution | Off unless `AGENTWATCH_ALLOW_REEXECUTION=1` | ADR-0014 |

The data flow as built:

```text
sensors (native SDK, OTel, LangChain, Claude Code, OpenAI/Anthropic, MCP, LegacyTranslator)
  -> Store.append (edge redaction, idempotent, immutable; sealed Merkle segments)
  -> Engine.build: normalizers -> runs (declared ids only) -> entities (exact keys)
                   -> EXECUTION + INFORMATION relations -> analyzers (motifs, profiles)
  -> Store.write_interpretation (atomic, versioned by component versions)
  -> Workspace (single read model) -> CLI, /api/v3, frontend
  -> lab (observe, replay L0-L3, branches, counterfactuals) writes append-only experiment records
```
