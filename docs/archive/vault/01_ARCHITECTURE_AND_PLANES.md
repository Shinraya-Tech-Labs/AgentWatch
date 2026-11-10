# 01 — Architecture & The Six Planes

> **Specification Anchor:** [`docs/v3/V3_ARCHITECTURE.md`](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/V3_ARCHITECTURE.md)  
> **Design Rules:** [ADR-0002](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0002-passive-sidecar.md) (Passive Sidecar), [ADR-0003](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0003-immutable-evidence-versioned-interpretation.md) (Immutable Evidence), [ADR-0012](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0012-modular-monolith.md) (Modular Monolith)  

---

## 🏛️ The Six Architectural Planes

AgentWatch v3 is organized into six strictly stratified architectural planes. Information flows strictly upwards: higher planes read from lower planes, never the reverse.

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ F. INTERFACE PLANE        API (FastAPI REST/WS/MCP) · Typer CLI · Frontend Live Views  │
│                           Live Timeline · Lab · Genome · Compare · Query Console       │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ E. EXPERIMENT PLANE       Deterministic Replay (L0–L4) · Timeline Branching           │
│                           Counterfactual Perturbation · AWBench Invariant Harness      │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ D. MODEL PLANE            Latent State Clustering · Motifs · System Genome · Drift     │
│  (derived, versioned)     Forecasting · Behavioural Distance                           │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ C. STRUCTURE PLANE        Execution Graph · Information Graph · Causal Graph (DAG)     │
│  (derived, versioned)     Hyperedge Relation Store · Lineage & Taint Engine            │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ B. INTERPRETATION PLANE   Normalizers (RawObservation → ComputationalEvent)            │
│  (derived, versioned)     Entity & Artifact Resolution · Run Segmentation              │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ A. EVIDENCE PLANE         Sensors → Observation Envelope → Sealed Segments             │
│  (immutable)              Append-only ObservationStore + Content-addressed BlobStore   │
└────────────────────────────────────────────────────────────────────────────────────────┘
        ▲ Strictly one-way data dependency: each plane reads only planes below it.
```

---

## 🔒 The Five Governing Invariants

1. **Only the Evidence Plane is Immutable** ([ADR-0003](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0003-immutable-evidence-versioned-interpretation.md)):
   - Plane A contains raw data as received from the wire.
   - Every higher plane (B through F) is a deterministic projection of raw evidence combined with a versioned configuration. If any interpretation logic or model changes, planes B through F can be deleted and fully recomputed.
2. **Provenance Accountability (`derived_by`)**:
   - Every derived record in Planes B, C, D, and E carries a provenance header:
     `derived_by = (component_name, version, config_hash)`
     referencing the exact evidence records it rests upon.
3. **Explicit Uncertainty Annotation** ([ADR-0007](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0007-evidence-classes-and-uncertainty.md)):
   - Every inferred quantity carries an uncertainty bound ($\pm \delta$) and its empirical basis. Clock readings without synchronized hardware clocks declare their uncertainty interval $[t - \delta, t + \delta]$.
4. **Maturity Label Enforcement** ([ADR-0010](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0010-maturity-labels.md)):
   - Every analyzer is tagged with an immutable enum: `EXPERIMENTAL`, `VALIDATED`, or `PRODUCTION`. The API exposes these labels to prevent experimental algorithms from governing production decisions.
5. **Planes are Packages, Not Microservices** ([ADR-0012](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0012-modular-monolith.md)):
   - AgentWatch runs as a unified, modular monolith with Celery task queues and background workers. Zero distributed network overhead between internal planes.

---

## 🔎 Plane Breakdown & Core Modules

### Plane A: Evidence Plane (`panoptis/evidence/`)
- **Sensors**: Passive collectors that produce `RawObservation` envelopes. Sensors never interpret, filter, or evaluate business logic.
- **Store**: Append-only `ObservationStore` partitioned into SHA-256 hash-sealed segments. Large payloads (prompts, raw tool outputs, binaries) go to content-addressed `BlobStore`.
- **Edge Redaction** ([ADR-0011](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0011-privacy-redaction-and-erasure.md)): PII and secret redaction occurs at the sensor boundary or ingest gateway *before* persistent storage. Observations carry a `redaction_manifest`.

### Plane B: Interpretation Plane (`panoptis/events/`, `panoptis/entities/`)
- **Normalizers**: Pure functions `list[RawObservation] -> list[ComputationalEvent]` that pair start/end timestamps, resolve span identifiers, and map tool calls to formal operations.
- **Run Segmentation**: Identifies multi-agent session boundaries, trace roots, and assigns execution units to a `SystemVersion`.

### Plane C: Structure Plane (`panoptis/graph/`, `panoptis/causality/`, `panoptis/provenance/`)
- **Hyperedge Relation Store**: Single storage layer holding typed relations partitioned into three distinct views:
  - `EXECUTION`: Call tree, parent-child delegation, sequencing.
  - `INFORMATION`: Read/write lineage, artifact transformation, prompt injection flow.
  - `CAUSAL`: Counterfactual dependency ($A \prec B$).

### Plane D: Model Plane (`panoptis/state/`, `panoptis/behaviour/`, `panoptis/models/`)
- **Latent State Clustering**: Identifies agent cognitive modes (planning, tool execution, reflection, recovery).
- **Behavioral Genome & Drift**: Statistical permutation tests across bootstrap samples to detect reasoning drift.

### Plane E: Experiment Plane (`panoptis/replay/`, `panoptis/rollback/`, `benchmarks/awbench/`)
- **Replay Levels**:
  - `L0`: Static trace inspection.
  - `L1`: Mocked replay (cached tool responses).
  - `L2`: Sandbox replay with live tool APIs.
  - `L3`: Perturbed counterfactual execution (injecting synthetic errors).
  - `L4`: Live shadowed execution.

### Plane F: Interface Plane (`panoptis/api/`, `panoptis/cli/`, `frontend/`)
- **APIs**: FastAPI async endpoints, websocket telemetry broadcast, MCP protocol tap.
- **CLI**: Rich Typer CLI (`panoptis verify`, `panoptis replay`, `panoptis export`).
- **UI**: Live dashboard, interactive execution graphs, contract verification waterfall.
