# 03 — Computational Event Algebra & Normalizers

> **Implementation Files:**  
> - [`panoptis/events/model.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/model.py) (The v3 Computational Event Algebra)  
> - [`panoptis/evidence/model.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/model.py) (Raw Observation & Envelope)  
> - [`panoptis/evidence/canonical.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/canonical.py) (RFC 8785 Canonical JSON & Content Addressing)  
> - [`panoptis/adapters/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/adapters/) (Framework Ingest Adapters)  

---

## 📐 The Computational Event Algebra

A `ComputationalEvent` is the foundational normalized primitive of AgentWatch. It is **not raw evidence** (which is immutable `RawObservation`) and **not graph analysis** (which lives in Plane C). It is a structured, normalized computation unit derived by a versioned normalizer.

### The Missing Fact Invariant
> *"Missing facts stay missing: actor, run_key, timestamps, and parents are `None` or empty when the source did not provide them. Normalizers must never invent them."*

---

## 🧩 Event Anatomy (`panoptis/events/model.py`)

A `ComputationalEvent` contains:

```python
@dataclass(frozen=True)
class ComputationalEvent:
    event_id: str                      # Deterministic UUID derived from evidence hash
    tenant_id: str                     # Tenant isolation namespace
    interp_id: str                     # Normalizer interpretation run identifier
    normalizer: str                    # Normalizer name and version (e.g., "otel_genai@1.2")
    derived_from: tuple[str, ...]      # List of raw observation IDs producing this event
    kind: EventKind                    # High-level taxonomy of the computation
    operation: str                     # Concrete operation (e.g., "llm.chat", "tools.execute")
    time: TemporalInfo                 # Start, end, and uncertainty interval
    status: EventStatus                # OK, ERROR, or UNKNOWN
    attributes_json: str               # Canonical JSON serialized attributes
    actor: str | None = None           # Resolved entity (agent or human)
    links: tuple[DeclaredLink, ...] = () # Declared causal & parent-child relationships
```

### Event Taxonomy (`EventKind`)
- `MODEL_INVOCATION`: Direct LLM/SLM inference calls (prompts, completions, token usage).
- `TOOL_INVOCATION`: External tool, function, or API execution (name, arguments, output).
- `RETRIEVAL`: Vector store query, RAG chunk lookup, or memory access.
- `AGENT_STEP`: Autonomous decision loop iteration or thought cycle.
- `STATE_TRANSITION`: Formal state change in an agent state machine.
- `HUMAN_INPUT`: User prompt, HITL approval, or intervention signal.
- `SYSTEM_EVENT`: Internal system messages, timeouts, or lifecycle hooks.

---

## ⏱️ Temporal Precision & Uncertainty Bounds (`TemporalInfo`)

In distributed multi-agent systems, clocks drift. A timestamp without an uncertainty interval is inherently misleading.

```python
@dataclass(frozen=True)
class TemporalInfo:
    start: datetime
    end: datetime | None = None
    uncertainty_ms: float = 0.0        # Clock drift bound (+/- delta in ms)
```

- When evaluating temporal orderings ($A \prec B$):
  - If $t_{start}(B) - t_{end}(A) > \delta_A + \delta_B$, then $A$ strictly preceded $B$.
  - If intervals $[t_A \pm \delta_A]$ and $[t_B \pm \delta_B]$ overlap and no declared causal link exists, they are **concurrent** ($A \parallel B$).

---

## 🔗 Declared Causal Links (`DeclaredLink`)

Events record relationships declared explicitly by the source framework:
- `PARENT`: Hierarchical containment (e.g., span parent).
- `FOLLOWS`: Sequential dependency.
- `RESPONDS_TO`: Request-reply pairing.
- `CAUSAL`: Explicit informational dependence.

---

## 🔄 Normalizer Ecosystem

Normalizers are pure, deterministic functions mapping raw payloads to `ComputationalEvent`s:

1. **OpenTelemetry GenAI Semantic Conventions** (`panoptis/sensors/otel/`):
   - Ingests OTLP protobuf/JSON traces.
   - Maps `gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` directly to standard attributes.
2. **LangChain & LangGraph** (`panoptis/adapters/langchain.py`):
   - Hooks into `on_chain_start`, `on_tool_end`, `on_llm_start`.
   - Reconstructs state graph execution trees.
3. **Claude Code / Anthropic Transcripts** (`panoptis/sensors/claude_code/`):
   - Ingests JSONL stream outputs and conversation transcripts.
   - Maps subagent calls (`invoke_subagent`), tool executions, and turn completions.
4. **Legacy Adapter** (`panoptis/adapters/generic.py`):
   - Converts v0.2 `AgentEvent` payloads into v3 `ComputationalEvent`s seamlessly.
