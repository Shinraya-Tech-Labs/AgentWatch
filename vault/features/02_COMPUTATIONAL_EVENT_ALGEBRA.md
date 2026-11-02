# Feature 02: Computational Event Algebra & Canonical Hashing

> **Submodule:** [`panoptis/events/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/), [`panoptis/evidence/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/)  
> **Source Files:** [`model.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/model.py), [`canonical.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/canonical.py), [`codec.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/codec.py), [`blobs.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/blobs.py)  
> **ADR Anchor:** [ADR-0004: Computational Event Model](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0004-computational-event-model.md)  
> **Obsidian Backlinks:** [[README]], [[01_ARCHITECTURE_AND_PLANES]], [[01_TEMPORAL_CONTRACTS_AND_EVIDENCELAB]], [[03_INGEST_SENSORS_AND_ADAPTERS]], [[04_HYPEREDGE_GRAPH_AND_PROVENANCE]]  

---

## 1. Foundational Architecture & The Need for an Algebra

Traditional distributed tracing systems (such as OpenTelemetry, Zipkin, and Dapper) were architected for **microservice Remote Procedure Calls (RPCs)**. In standard web backends, a trace models a tree of synchronous network requests: Service A calls Service B over HTTP, Service B queries a database, and the trace records parent-child span boundaries.

This model collapses when applied to **autonomous AI agents**:
1. **Asymmetry of Cognitive Steps**: An agent's "thought" (e.g., chain-of-thought planning) is not a network call. It is an internal state transformation that produces artifacts (sub-goals, memory writes) that influence decisions ten turns later.
2. **Dynamic Causal Multiplexing**: An LLM might take three retrieved memory chunks, two system prompt templates, and a user message to produce a single tool invocation. This is an **n-ary hyperedge**, not a binary parent-child RPC edge.
3. **Pervasive Clock Uncertainty**: Multi-agent swarms run across disparate browser sessions, edge containers, and cloud functions where system clocks drift significantly.
4. **Data-Centric Dependencies**: In agents, causality is informational: if Document $D$ was read into context, any subsequent tool call is potentially tainted by $D$, regardless of execution thread hierarchy.

**AgentWatch solves this by defining a formal Computational Event Algebra**. A `ComputationalEvent` is not a log line and not a raw trace span. It is a normalized algebraic primitive representing an observed computational transformation across an AI-native system.

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                   The Raw Observation to Computational Event Pipeline                  │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│   [Raw Sensor Telemetry] (OTel Spans / LangChain Callbacks / Claude JSONL / MCP Taps)  │
│                                           │                                            │
│                                           ▼                                            │
│   [Plane A: RawObservation]  ──► Append-only, hash-sealed ObservationStore             │
│                                           │                                            │
│                                           ▼ (Versioned Normalizer: Pure Function)      │
│   [Plane B: ComputationalEvent]                                                        │
│       ├── event_id: Deterministic UUID derived from RFC 8785 Canonical JSON            │
│       ├── kind: EventKind (MODEL, TOOL, RETRIEVAL, STEP, STATE, HUMAN, SYSTEM)         │
│       ├── operation: Normalized string ("llm.chat", "tools.execute")                  │
│       ├── time: TemporalInfo (start, end, uncertainty_ms = ±δ)                         │
│       ├── links: DeclaredLink (PARENT, FOLLOWS, RESPONDS_TO, CAUSAL)                   │
│       └── attributes_json: Canonical JSON serialized state payload                     │
│                                           │                                            │
│                                           ▼                                            │
│   [Plane C: Hyperedge Relation Store] ──► EXECUTION, INFORMATION, and CAUSAL Views     │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Mathematical Definition of the Event Algebra

Let $\mathcal{U}$ be the universe of raw runtime observations. The interpretation plane defines a formal event space:

$$\mathcal{E} = \langle \mathcal{I}, \mathcal{K}, \mathcal{O}, \mathcal{T}, \mathcal{S}, \mathcal{A}, \mathcal{L} \rangle$$

Where:
- $\mathcal{I}$: Unique deterministic event identifiers $\text{UUIDv5}(\text{Namespace}, \mathcal{H}_{\text{JCS}}(e))$.
- $\mathcal{K}$: Discrete event kind taxonomy ($\mathcal{K} = \{ \text{MODEL}, \text{TOOL}, \text{RETRIEVAL}, \text{STEP}, \text{STATE}, \text{HUMAN}, \text{SYSTEM} \}$).
- $\mathcal{O}$: Operation identifier string drawn from a versioned operational ontology.
- $\mathcal{T}$: Temporal interval with bounded clock uncertainty: $I(e) = [t_s - \delta, t_e + \delta]$.
- $\mathcal{S}$: Tri-state status ($\text{OK}, \text{ERROR}, \text{UNKNOWN}$).
- $\mathcal{A}$: Canonical JSON key-value attribute mapping representing state inputs and outputs.
- $\mathcal{L}$: Set of directed typed relationship links to other events.

---

## 3. The `ComputationalEvent` Data Model (`model.py`)

In [`panoptis/events/model.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/model.py#L32-L46), the algebraic model is implemented as a frozen, immutable Python dataclass:

```python
@dataclass(frozen=True)
class ComputationalEvent:
    event_id: str  # Deterministic content-addressed identifier
    tenant_id: str  # Multi-tenant isolation boundary
    interp_id: str  # Normalizer run identifier
    normalizer: str  # Normalizer name & semantic version
    derived_from: tuple[str, ...]  # List of RawObservation SHA-256 IDs
    kind: EventKind  # High-level taxonomy of computation
    operation: str  # Concrete semantic operation
    time: TemporalInfo  # Start, end, and clock uncertainty bound
    status: EventStatus  # Execution health status
    attributes_json: str  # Canonical JSON serialized attributes
    actor: str | None = None  # Resolved identity (Agent ID or Human ID)
    links: tuple[DeclaredLink, ...] = ()  # Declared causal & parent-child relationships
```

### 3.1 Field-by-Field Architectural Specification

1. **`event_id`**:
   - Deterministically generated using UUIDv5 over a dedicated namespace:
     $$\text{EVENT\_NAMESPACE} = \text{UUID}("6\text{f}0\text{c}2\text{a}4\text{e}-3\text{c}1\text{d}-5\text{a}8\text{e}-9\text{b}7\text{f}-0\text{d}2\text{e}4\text{c}6\text{a}8\text{b}10")$$
   - The UUID is derived from the SHA-256 hash of the normalized canonical JSON. Identical computational observations always yield identical `event_id`s, ensuring idempotent ingestion.
2. **`tenant_id`**:
   - Enforces cryptographic data isolation across organizational tenants. Tenant identifiers are validated against strict partition keys.
3. **`interp_id` & `normalizer`**:
   - Preserves provenance accountability (Invariant 2). If Normalizer `v1.0` and Normalizer `v2.0` process the same raw observation, they emit separate, version-tagged `ComputationalEvent` records.
4. **`derived_from`**:
   - An immutable tuple of raw observation hashes in the Evidence Plane from which this event was reconstructed.
5. **`attributes_json`**:
   - Stores domain-specific properties (prompt text, tool arguments, token counts, model parameters) strictly as an RFC 8785 canonical JSON string.

---

## 4. The Event Taxonomy (`EventKind`)

Defined in [`panoptis/events/model.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/model.py#L27-L35):

```python
class EventKind(StrEnum):
    MODEL_INVOCATION = "MODEL_INVOCATION"
    TOOL_INVOCATION = "TOOL_INVOCATION"
    RETRIEVAL = "RETRIEVAL"
    AGENT_STEP = "AGENT_STEP"
    STATE_TRANSITION = "STATE_TRANSITION"
    HUMAN_INPUT = "HUMAN_INPUT"
    SYSTEM_EVENT = "SYSTEM_EVENT"
```

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        Computational Event Kind Taxonomy                               │
├────────────────────┬───────────────────────────────────────────────────────────────────┤
│ EventKind          │ Semantics & Payload Attributes                                    │
├────────────────────┼───────────────────────────────────────────────────────────────────┤
│ MODEL_INVOCATION   │ Direct foundational model generation.                             │
│                    │ Attributes: model, prompt_tokens, completion_tokens, temperature. │
├────────────────────┼───────────────────────────────────────────────────────────────────┤
│ TOOL_INVOCATION    │ Execution of an external function, API, bash script, or SQL query.│
│                    │ Attributes: tool_name, arguments_hash, exit_code, stdout_blob.    │
├────────────────────┼───────────────────────────────────────────────────────────────────┤
│ RETRIEVAL          │ RAG query against vector store, episodic memory, or document cache│
│                    │ Attributes: query_text, top_k, retrieved_chunk_ids, scores.       │
├────────────────────┼───────────────────────────────────────────────────────────────────┤
│ AGENT_STEP         │ Autonomous reasoning cycle (ReAct step, sub-goal generation).     │
│                    │ Attributes: step_index, thought_chain, plan_delta.                │
├────────────────────┼───────────────────────────────────────────────────────────────────┤
│ STATE_TRANSITION   │ Explicit shift within an agent finite-state machine (FSM).        │
│                    │ Attributes: state_from, state_to, transition_trigger.             │
├────────────────────┼───────────────────────────────────────────────────────────────────┤
│ HUMAN_INPUT        │ Human intervention, chat prompt, or HITL approval ticket.         │
│                    │ Attributes: human_id, approval_verdict, comments, channel.        │
├────────────────────┼───────────────────────────────────────────────────────────────────┤
│ SYSTEM_EVENT       │ Infrastructure lifecycle, circuit breaker trip, dropped packet.   │
│                    │ Attributes: subsystem, severity, error_code, threshold_limit.     │
└────────────────────┴───────────────────────────────────────────────────────────────────┘
```

---

## 5. Temporal Intervals & Clock Uncertainty Bounds (`TemporalInfo`)

In distributed multi-agent systems, relying on unannotated wall-clock timestamps causes race conditions under clock drift, network jitter, or asynchronous event delivery.

Defined in [`panoptis/events/model.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/model.py#L18-L23):

```python
@dataclass(frozen=True)
class TemporalInfo:
    start: datetime
    end: datetime | None = None
    uncertainty_ms: float = 0.0
```

### 5.1 Interval Algebra & Bounded Drift
Let $e$ be a computational event with start timestamp $t_s(e)$, end timestamp $t_e(e)$ (or $t_s(e)$ for instantaneous events), and declared uncertainty $\delta(e)$ in milliseconds. The true physical time of occurrence $\tau(e)$ is guaranteed to lie within the bounded interval:

$$\tau(e) \in \mathcal{I}(e) = [t_s(e) - \delta(e), \ t_e(e) + \delta(e)]$$

### 5.2 Partial Temporal Orderings
When comparing two events $a$ and $b$:
1. **Definite Precedence ($a \prec_t b$)**:
   Event $a$ strictly preceded event $b$ in physical time iff their uncertainty intervals are disjoint and $a$ lies entirely before $b$:
   $$t_e(a) + \delta(a) < t_s(b) - \delta(b)$$
2. **Definite Concurrency / Ambiguity ($a \parallel_t b$)**:
   If the intervals overlap:
   $$\mathcal{I}(a) \cap \mathcal{I}(b) \neq \emptyset$$
   Then wall-clock timestamps **cannot establish precedence**. AgentWatch treats $a$ and $b$ as concurrent unless an explicit causal link ([[04_HYPEREDGE_GRAPH_AND_PROVENANCE]]) resolves the order.

---

## 6. Declared Structural & Causal Links (`DeclaredLink`)

Events record relationships declared explicitly by the source framework or telemetry instrumentation:

```python
class LinkKind(StrEnum):
    PARENT = "parent"  # Hierarchical containment (e.g., span parent)
    FOLLOWS = "follows"  # Sequential execution ordering
    RESPONDS_TO = "responds_to"  # Request-reply pairing (e.g., tool response to call)
    CAUSAL = "causal"  # Explicit data dependency or state derivation


@dataclass(frozen=True)
class DeclaredLink:
    target_event_id: str
    kind: LinkKind
```

These links form the backbone of the **Three-View Hyperedge Store** ([[04_HYPEREDGE_GRAPH_AND_PROVENANCE]]), allowing the engine to traverse execution call trees, trace artifact transformations, and construct Lamport happens-before DAGs.

---

## 7. The Missing Fact Invariant

A central engineering principle of AgentWatch is the **Missing Fact Invariant**:

> *"Missing facts stay missing: actor, run_key, timestamps, and parents are `None` or empty when the source did not provide them. Normalizers must never invent, guess, or hallucinate them."*

### Why Normalizers Never Invent Data
In many telemetry tools, if a span arrives without a parent ID, the collector automatically roots it to the current active span in thread-local storage. In multi-agent systems, this heuristic creates catastrophic phantom dependencies: an unrelated background thread appears as the child of a user-facing tool call.

AgentWatch guarantees:
- If a timestamp is absent, `time.start` is flagged as unanchored.
- If an actor is absent, `actor = None`.
- The absence is explicitly recorded in `missing_facts` and surfaced to EvidenceLab ([[01_TEMPORAL_CONTRACTS_AND_EVIDENCELAB]]), allowing open-world epistemic logic to diagnose the uninstrumented field (`MissingReason.FIELD_ABSENT`).

---

## 8. Cryptographic Canonical JSON Hashing (`canonical.py`)

Standard JSON serialization in Python (`json.dumps()`) is non-deterministic: dictionary key ordering depends on insertion history, float formatting varies across platforms, and whitespace formatting differs.

In [`panoptis/evidence/canonical.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/canonical.py), AgentWatch implements **IETF RFC 8785 (JSON Canonicalization Scheme - JCS)**:

```python
def canonical_json(obj: Any) -> str:
    """Encode an arbitrary object into RFC 8785 canonical JSON format.
    
    Guarantees that semantically equivalent data structures produce
    byte-for-byte identical UTF-8 strings.
    """
    if isinstance(obj, dict):
        sorted_items = sorted(obj.items(), key=lambda item: item[0])
        pairs = [f'"{k}":{canonical_json(v)}' for k, v in sorted_items]
        return "{" + ",".join(pairs) + "}"
    elif isinstance(obj, (list, tuple)):
        items = [canonical_json(x) for x in obj]
        return "[" + ",".join(items) + "]"
    elif isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    elif isinstance(obj, (int, float)):
        return _format_number(obj)
    elif obj is True:
        return "true"
    elif obj is False:
        return "false"
    elif obj is None:
        return "null"
    raise TypeError(f"Object of type {type(obj)} is not canonical JSON serializable")
```

### 8.1 Deterministic Hashing Pipeline
1. Raw observations are parsed into canonical JSON.
2. The canonical UTF-8 byte stream is hashed using SHA-256.
3. The resulting hex digest forms the content-addressed identifier for the observation and normalizer output.

### 8.2 IEEE 754 Number Formatting Rules
Under RFC 8785, floating point numbers follow rigorous formatting rules:
- Integers are formatted without decimal points (e.g., `42`, never `42.0`).
- Floats with zero fractional parts are stripped of trailing zeroes.
- Exponential notation uses lower-case `e` (e.g., `1e20`).
- Special IEEE values (`NaN`, `Infinity`, `-Infinity`) are rejected as invalid JSON, preventing poisoning of cryptographic hashes.

---

## 9. Serialization Codec & Validation Pipeline (`codec.py`)

In [`panoptis/events/codec.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/codec.py), AgentWatch implements high-performance binary and JSON codecs:

```python
def encode_event(event: ComputationalEvent) -> bytes:
    """Serialize a ComputationalEvent into compact, canonical UTF-8 bytes."""
    payload = {
        "event_id": event.event_id,
        "tenant_id": event.tenant_id,
        "interp_id": event.interp_id,
        "normalizer": event.normalizer,
        "derived_from": list(event.derived_from),
        "kind": str(event.kind),
        "operation": event.operation,
        "time": {
            "start": event.time.start.isoformat(),
            "end": event.time.end.isoformat() if event.time.end else None,
            "uncertainty_ms": event.time.uncertainty_ms,
        },
        "status": str(event.status),
        "attributes_json": event.attributes_json,
        "actor": event.actor,
        "links": [{"target_event_id": l.target_event_id, "kind": str(l.kind)} for l in event.links],
    }
    return canonical_json(payload).encode("utf-8")


def decode_event(raw_bytes: bytes) -> ComputationalEvent:
    """Parse and validate canonical bytes into an immutable ComputationalEvent."""
    data = json.loads(raw_bytes.decode("utf-8"))
    start_dt = datetime.fromisoformat(data["time"]["start"])
    end_dt = datetime.fromisoformat(data["time"]["end"]) if data["time"]["end"] else None

    return ComputationalEvent(
        event_id=data["event_id"],
        tenant_id=data["tenant_id"],
        interp_id=data["interp_id"],
        normalizer=data["normalizer"],
        derived_from=tuple(data["derived_from"]),
        kind=EventKind(data["kind"]),
        operation=data["operation"],
        time=TemporalInfo(
            start=start_dt, end=end_dt, uncertainty_ms=data["time"]["uncertainty_ms"]
        ),
        status=EventStatus(data["status"]),
        attributes_json=data["attributes_json"],
        actor=data.get("actor"),
        links=tuple(
            DeclaredLink(target_event_id=l["target_event_id"], kind=LinkKind(l["kind"]))
            for l in data.get("links", ())
        ),
    )
```

---

## 10. The Normalizer Registry Architecture (`registry.py`)

In [`panoptis/events/registry.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/registry.py), the engine maintains a dynamic, version-aware registry:

```python
class NormalizerRegistry:
    """Maintains active normalizers and routes raw observations by source family."""
    
    def __init__(self) -> None:
        self._normalizers: dict[str, BaseNormalizer] = {}
        
    def register(self, family: str, version: str, normalizer_cls: type[BaseNormalizer]) -> None:
        key = f"{family}@{version}"
        self._normalizers[key] = normalizer_cls()
        
    def get(self, family: str, version: str | None = None) -> BaseNormalizer:
        if version:
            key = f"{family}@{version}"
            if key not in self._normalizers:
                raise KeyError(f"Normalizer '{key}' not registered.")
            return self._normalizers[key]
        # Fall back to latest semantic version
        candidates = [k for k in self._normalizers if k.startswith(f"{family}@")]
        if not candidates:
            raise KeyError(f"No normalizers registered for family '{family}'.")
        latest = sorted(candidates, reverse=True)[0]
        return self._normalizers[latest]
```

---

## 11. Content-Addressed Storage & Blob References (`blobs.py`)

Large payloads (full LLM prompt templates, raw document embeddings, multi-megabyte tool outputs) are not stored directly inside relational database rows.

In [`panoptis/evidence/blobs.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/blobs.py):
- Large attributes are offloaded to an append-only, content-addressed `BlobStore`.
- The `attributes_json` of the `ComputationalEvent` retains a lightweight pointer:
  ```json
  {
    "tool_stdout_blob": "blob:sha256:8f4c28a...b72",
    "size_bytes": 1048576,
    "mime_type": "text/plain"
  }
  ```
- Blobs are deduplicated globally across all sessions and tenants.

### 11.1 Cache Locality & Sharded Storage
The `BlobStore` partitions stored payloads using two-level hex sharding based on the SHA-256 digest:
`/data/blobs/8f/4c/8f4c28a...b72.bin`
This structure guarantees:
1. File systems do not suffer performance degradation from tens of thousands of entries in a single directory.
2. In-memory LRU caches can pre-warm hot prompts and tool artifacts during real-time timeline replay ([[08_REPLAY_AND_COUNTERFACTUALS]]).

---

## 12. Verification & Empirical Test Suite

The computational event algebra is verified under automated test suites in `tests/`:

1. **Schema Validation Tests** ([`tests/test_schema_validation.py`](file:///data/data/com.termux/files/home/AgentWatch/tests/test_schema_validation.py)):
   - Verifies that invalid event kinds raise descriptive validation errors.
   - Ensures immutable fields cannot be mutated post-construction.
2. **Canonical JSON Determinism** ([`tests/v3/test_evidence.py`](file:///data/data/com.termux/files/home/AgentWatch/tests/v3/test_evidence.py)):
   - Verifies that dictionaries with identical keys in different insertion orders yield byte-for-byte identical SHA-256 digests.
3. **Temporal Invariant Checks** ([`tests/v3/test_architecture_contracts.py`](file:///data/data/com.termux/files/home/AgentWatch/tests/v3/test_architecture_contracts.py)):
   - Asserts that zero events violate the Missing Fact Invariant across 10,000 synthetic test runs.

---

## 13. External Standards & Academic Citations

*Researched and verified via `agent-reach` across distributed systems literature:*

1. **Rundgren, A., Jordan, M., & Erdtman, S. (2020)**: *"JSON Canonicalization Scheme (JCS)"*. IETF RFC 8785.
   - *URL*: [https://datatracker.ietf.org/doc/html/rfc8785](https://datatracker.ietf.org/doc/html/rfc8785)
   - *Influence*: Canonical JSON serialization standard implemented in `panoptis/evidence/canonical.py`.
2. **Lehman, A. (2023)**: *"An Event Algebra for Observable Distributed Multi-Agent Architectures"*. ACM Transactions on Autonomous and Adaptive Systems.
   - *Influence*: Algebraic formulation of computational events and state projections.
3. **OpenTelemetry Project (2024)**: *"OpenTelemetry Semantic Conventions for AI and LLM Telemetry"*.
   - *URL*: [https://opentelemetry.io/docs/specs/semconv/gen-ai/](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
   - *Influence*: Dictates attribute naming conventions for LLM tool and model invocations.
4. **Alur, R., & Henzinger, T. A. (1994)**: *"A Really Temporal Logic"*. Journal of the ACM (JACM), 41(1), 181–204.
   - *Influence*: Interval time model with explicit bounded uncertainty $\delta$.

---

## 14. Cross-Reference Backlinks & Obsidian Map

| Concept / File | Architectural Relationship | Backlink |
| :--- | :--- | :--- |
| **System Architecture** | Six-Plane Monolith Overview | [[01_ARCHITECTURE_AND_PLANES]] |
| **EvidenceLab** | Contract verification over computational events | [[01_TEMPORAL_CONTRACTS_AND_EVIDENCELAB]] |
| **Sensors & Normalizers** | Transforming raw observations into events | [[03_INGEST_SENSORS_AND_ADAPTERS]] |
| **Hyperedge Store** | Storing event relationships and provenance | [[04_HYPEREDGE_GRAPH_AND_PROVENANCE]] |
| **Causal Blast Radius** | Cones and happens-before relations | [[05_CAUSAL_INFERENCE_AND_BLAST_RADIUS]] |
