# 09 — Comprehensive Feature Matrix & Technical Architecture Dossier

> **Project:** [Shinraya-Tech-Labs/AgentWatch](https://github.com/Shinraya-Tech-Labs/AgentWatch)  
> **Platform Version:** v3.1 EvidenceLab Architecture  
> **Documentation Type:** Master Technical Specification & In-Depth Feature Matrix  
> **Obsidian Backlinks:** [[README]], [[01_ARCHITECTURE_AND_PLANES]], [[02_EVIDENCELAB_AND_TEMPORAL_CONTRACTS]], [[03_EVENT_ALGEBRA_AND_NORMALIZERS]], [[04_STORAGE_CAUSALITY_AND_GRAPH]], [[05_PLATFORM_API_AND_FRONTEND]], [[06_ROADMAP_AND_ISSUES_729_738]], [[07_SECURITY_BASELINE_AND_DEVSECOPS]], [[08_MOBILE_TERMUX_WORKFLOW]]  

---

## Executive Summary

AgentWatch is an **evidence-first, passive observability and formal verification platform for autonomous AI agent systems**. Unlike superficial LLM loggers, wrapper evaluators, or prompt firewalls, AgentWatch treats an AI agent not as a text-in/text-out black box, but as a **distributed computational system**. 

It passively reconstructs an agent's execution into a queryable, versioned, uncertainty-annotated system model. This model enables developers, enterprise auditors, and autonomous systems to explain past actions, verify formal temporal safety invariants, compute causal blast radii, and run counterfactual replays.

This document provides a exhaustive, line-by-line technical specification of every capability, submodule, mathematical model, and interface in the AgentWatch engine.

---

## Section 1: Epistemic Verification & Temporal Contracts (EvidenceLab Core)

> **Submodule:** [`panoptis/contracts/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/)  
> **Source Files:** [`schema.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/schema.py), [`evaluator.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/evaluator.py), [`remedies.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/remedies.py)  
> **Obsidian Backlink:** [[02_EVIDENCELAB_AND_TEMPORAL_CONTRACTS]]  

### 1.1 The Open-World Epistemic Model
Traditional observability frameworks (such as Jaeger, OpenTelemetry, and Prometheus) operate strictly under the **Closed-World Assumption (CWA)**: any event not observed in the trace is assumed never to have occurred. In distributed agent systems, this assumption leads to high false-alarm rates because sensors may be uninstrumented, network packets may drop, or dynamic sampling may shed spans.

AgentWatch operates under the **Open-World Assumption (OWA)**. It introduces a four-valued epistemic logic formalizing whether the available telemetry is sufficient to prove or refute a property:

$$\mathcal{V} \in \{\text{SUPPORTED}, \text{REFUTED}, \text{INCONCLUSIVE}, \text{CONFLICTING\_EVIDENCE}\}$$

- **`EvidenceVerdict.SUPPORTED`**: The trace contains positive, witnessed events satisfying the temporal rule and all join constraints.
- **`EvidenceVerdict.REFUTED`**: The trace proves the invariant was violated *under verified sensor coverage*. A contract is only refuted if the monitoring infrastructure actively monitored the required subsystem during the temporal window.
- **`EvidenceVerdict.INCONCLUSIVE`**: The trace lacks observations required to prove the obligation. The open-world assumption flags an **observational gap** rather than triggering an unverified security alarm.
- **`EvidenceVerdict.CONFLICTING_EVIDENCE`**: Telemetry contains mutually contradictory observations, such as overlapping interval timestamps without causal edges or contradictory state transitions from out-of-order ingestion.

### 1.2 Temporal Logic Operators (`ContractOperator`)
The evaluation engine natively executes first-order temporal operators over event streams:
1. **`PRECEDING_EVENT` ($P \mathcal{S} Q$)**:
   Asserts that whenever target event $Q$ occurs at time $t_Q$, a required event $P$ must have strictly preceded it within a sliding lookback window $W$:
   $$\forall e_Q \in Q, \exists e_P \in P \text{ s.t. } 0 < t(e_Q) - t(e_P) \le W \land \text{join}(e_Q, e_P)$$
2. **`EVENTUALLY_WITHIN` ($\Diamond_{\le W} Q$)**:
   Asserts that following trigger event $P$, response event $Q$ must occur within forward duration $W$:
   $$\forall e_P \in P, \exists e_Q \in Q \text{ s.t. } 0 \le t(e_Q) - t(e_P) \le W \land \text{join}(e_P, e_Q)$$
3. **`ALWAYS` ($\Box P$)**:
   A global safety invariant asserting predicate $P(e)$ holds across every event $e$ in the execution scope.

### 1.3 Minimal Witness Extraction
When an evaluation completes, [`evaluator.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/evaluator.py) extracts a **minimal witness graph** $\mathcal{W} \subseteq \mathcal{E}$. The witness is the smallest subset of events required to mathematically substantiate the verdict:
- For `SUPPORTED`, $\mathcal{W}$ contains the trigger event, the fulfilling event, and intermediate span links.
- For `REFUTED`, $\mathcal{W}$ contains the trigger event along with verified sensor coverage snapshots proving the fulfilling event was missing.
- For `INCONCLUSIVE`, $\mathcal{W}$ contains the trigger event and an explicit `MissingFact` record.

### 1.4 Telemetry Gap Remediation Engine (`remedies.py`)
When a contract is `INCONCLUSIVE`, [`remedies.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/remedies.py) executes automated gap diagnosis. It maps the `MissingReason` (`SENSOR_DISABLED`, `FIELD_ABSENT`, `SPAN_ORPHAN`, `SEQUENCE_GAP`, `CLOCK_AMBIGUITY`) to an actionable `RemediationRecommendation`:
- Identifies the uninstrumented library (e.g., `langchain.callbacks`, `fastapi.middleware`).
- Recommends the configuration patch or SDK wrapper needed to turn the inconclusive contract into a definitive proof.

---

## Section 2: The Computational Event Algebra & Canonical Hashing

> **Submodule:** [`panoptis/events/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/), [`panoptis/evidence/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/)  
> **Source Files:** [`model.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/model.py), [`canonical.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/canonical.py), [`codec.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/codec.py)  
> **Obsidian Backlink:** [[03_EVENT_ALGEBRA_AND_NORMALIZERS]]  

### 2.1 The `ComputationalEvent` Primitive
A [`ComputationalEvent`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/model.py#L32-L46) is the normalized unit of agent computation. It decouples raw wire protocols from higher-order graph algorithms:

```python
@dataclass(frozen=True)
class ComputationalEvent:
    event_id: str
    tenant_id: str
    interp_id: str
    normalizer: str
    derived_from: tuple[str, ...]
    kind: EventKind
    operation: str
    time: TemporalInfo
    status: EventStatus
    attributes_json: str
    actor: str | None = None
    links: tuple[DeclaredLink, ...] = ()
```

### 2.2 Taxonomy of Agent Computation (`EventKind`)
- **`MODEL_INVOCATION`**: Direct interactions with foundational model inference endpoints. Captures model name, prompt tokens, completion tokens, temperature, and raw completion metadata.
- **`TOOL_INVOCATION`**: Invocations of external functions, code interpreters, shell terminals, or SQL clients.
- **`RETRIEVAL`**: Lookups against vector databases, episodic memory indices, or document retrieval pipelines.
- **`AGENT_STEP`**: A discrete step in an agent's internal thought cycle (e.g., ReAct reasoning step, sub-goal generation).
- **`STATE_TRANSITION`**: An explicit transition between states in an agent state machine.
- **`HUMAN_INPUT`**: Human-in-the-loop decisions, approval tickets, or direct chat inputs.
- **`SYSTEM_EVENT`**: Internal timeouts, circuit breaker trips, or telemetry dropped-packet alerts.

### 2.3 Interval Timestamps with Uncertainty Bounds (`TemporalInfo`)
In distributed systems, wall-clock readings are subject to clock drift, NTP adjustments, and hypervisor pause jitter. AgentWatch annotates every timestamp with an explicit uncertainty parameter:
$$I(e) = [t_{\text{start}}(e) - \delta_e, t_{\text{end}}(e) + \delta_e]$$
If two events have overlapping uncertainty intervals and no explicit causal edge connects them, the evaluation engine treats them as concurrent ($a \parallel b$) rather than guessing their order based on arbitrary nanosecond timestamps.

### 2.4 RFC 8785 Canonical JSON Serialization
To guarantee deterministic cryptographic hashing across different languages and JSON encoders, [`canonical.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/canonical.py) implements **RFC 8785 (JSON Canonicalization Scheme - JCS)**:
- Keys are sorted lexicographically by UTF-16 code units.
- Whitespace outside strings is removed.
- Numbers are formatted using IEEE 754 representation without trailing zeroes.
- SHA-256 hashes generated from canonical JSON serve as content-addressed identifiers across the entire system.

---

## Section 3: Ingestion Adapters & Normalizer Ecosystem

> **Submodule:** [`panoptis/adapters/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/adapters/), [`panoptis/sensors/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/sensors/)  
> **Obsidian Backlink:** [[03_EVENT_ALGEBRA_AND_NORMALIZERS]]  

### 3.1 Normalization Architecture
A normalizer in AgentWatch is a **pure, versioned function**:
$$f_{\text{norm}}: \text{Sequence}[\text{RawObservation}] \longrightarrow \text{Sequence}[\text{ComputationalEvent}]$$
Normalizers extract structured effects (reads and writes of state), pair asynchronous start and end spans into interval events, and resolve framework-specific terminology into the standard event taxonomy.

### 3.2 Supported Sensor Families
1. **OpenTelemetry GenAI Semantic Conventions** (`otel`):
   Ingests OTLP/HTTP traces adhering to the OpenTelemetry GenAI working group specifications. Automatically maps `gen_ai.system`, `gen_ai.request.model`, and span links into `ComputationalEvent` attributes.
2. **LangChain & LangGraph Adapter** (`panoptis/adapters/langchain.py`):
   Hooks into LangChain `BaseCallbackHandler`. Maps `on_chain_start`, `on_tool_start`, and `on_llm_end` into structured event trees, tracking LangGraph state snapshots and edge routing.
3. **Claude Code & Anthropic Transcripts** (`panoptis/sensors/claude_code/`):
   Parses `stream-json` emissions and session transcripts. Ingests subagent delegations (`invoke_subagent`), bash tool executions, and multi-turn conversations.
4. **Model Context Protocol (MCP) Message Tap** (`panoptis/cli/mcp.py`):
   Sits as a transparent JSON-RPC proxy between MCP clients and servers, observing tool definitions, resource queries, and prompt template expansions.
5. **Multi-Agent Adapters** (`panoptis/adapters/crewai.py`, `openai_agents.py`):
   Specialized callback bridges for CrewAI task delegation, AutoGen conversation turns, and OpenAI Assistant API thread events.

---

## Section 4: Three-View Hyperedge Graph & Provenance Engine

> **Submodule:** [`panoptis/graph/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/graph/), [`panoptis/provenance/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/provenance/)  
> **Source Files:** [`model.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/graph/model.py), [`build.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/graph/build.py), [`lineage.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/provenance/lineage.py), [`ambiguity.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/provenance/ambiguity.py)  
> **ADR Anchor:** [ADR-0005: Three Views, One Relation Store](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0005-three-views-one-relation-store.md)  
> **Obsidian Backlink:** [[04_STORAGE_CAUSALITY_AND_GRAPH]]  

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Hyperedge Relation Store                        │
├───────────────────┬──────────────────────┬─────────────────────────────┤
│  EXECUTION VIEW   │   INFORMATION VIEW   │         CAUSAL VIEW         │
├───────────────────┼──────────────────────┼─────────────────────────────┤
│ • Span Call Trees │ • Artifact Lineage   │ • Happens-Before DAG        │
│ • Subagent Forks  │ • Prompt Taint Flow  │ • Counterfactual Dependence │
│ • Step Sequencing │ • Memory Read/Write  │ • Blast Radius Cones        │
└───────────────────┴──────────────────────┴─────────────────────────────┘
```

### 4.1 Hyperedge Relational Storage
Traditional graph stores struggle with multi-party interactions common in AI agents (e.g., a model taking three memory artifacts and a user prompt to generate two tool calls). AgentWatch models execution as an **n-ary hypergraph**:
$$\mathcal{H} = (\mathcal{V}, \mathcal{E}_H), \quad e_H = (S_{\text{inputs}}, S_{\text{outputs}}, \text{relation\_type}, \text{confidence})$$
This hypergraph is projected into three specialized relational views:
1. **`EXECUTION` View**: Reconstructs physical invocation trees, subagent spawning, and thread lifespans.
2. **`INFORMATION` View**: Tracks data provenance. Reconstructs which documents influenced which outputs and identifies information leakages.
3. **`CAUSAL` View**: Evaluates true counterfactual dependencies, pruning incidental temporal coincidence.

### 4.2 Information Lineage & Taint Propagation (`lineage.py`)
The provenance engine answers two core compliance and security questions:
- **Upstream Origin (Backward Derivation)**: Given an unauthorized financial transfer or SQL write, what exact prompt, document, or user message initiated the sequence?
- **Downstream Blast Radius (Forward Taint Propagation)**: Given an untrusted web page read containing an indirect prompt injection attack, what downstream tools, memories, and responses were exposed to the untrusted content?

### 4.3 Ambiguity Tracking (`ambiguity.py`)
When traces have gaps or uninstrumented spans, the graph engine does not guess connections. It records explicit `Ambiguity` hyperedges with probability bounds, preventing false certainty in security audits.

---

## Section 5: Causal Cones & Blast Radius Analysis

> **Submodule:** [`panoptis/causality/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/causality/)  
> **Source Files:** [`cones.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/causality/cones.py), [`hypotheses.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/causality/hypotheses.py)  
> **Obsidian Backlink:** [[04_STORAGE_CAUSALITY_AND_GRAPH]]  

### 5.1 Causal Cones
For any computational event $e$, the causality engine computes two topological cones:
1. **Past Causal Cone ($\mathcal{C}^-(e)$)**:
   The transitive closure of all events that causally contributed to $e$:
   $$\mathcal{C}^-(e) = \{ x \in \mathcal{E} \mid x \prec e \}$$
2. **Future Causal Cone ($\mathcal{C}^+(e)$)**:
   The transitive closure of all downstream events that were causally influenced by $e$:
   $$\mathcal{C}^+(e) = \{ y \in \mathcal{E} \mid e \prec y \}$$

### 5.2 Blast Radius Computation
When an agent suffers a catastrophic failure, prompt injection, or hallucinatory tool execution, [`cones.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/causality/cones.py) calculates the exact **blast radius**:
- Quantifies external state mutations (files written, API endpoints invoked, database records updated).
- Isolates unaffected execution branches, allowing safe partial rollbacks without restarting the entire multi-agent workflow.

### 5.3 Causal Hypothesis Testing (`hypotheses.py`)
Generates formal hypotheses regarding system failures: evaluates whether an error in step $N$ was caused by a specific retrieval failure in step $N-4$ versus an intrinsic model hallucination.

---

## Section 6: Latent Cognitive State Discovery & Motif Engine

> **Submodule:** [`panoptis/state/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/state/), [`panoptis/analysis/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/analysis/), [`panoptis/behaviour/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/behaviour/)  
> **Source Files:** [`latent.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/state/latent.py), [`motifs.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/behaviour/motifs.py), [`profile.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/behaviour/profile.py)  
> **Obsidian Backlink:** [[04_STORAGE_CAUSALITY_AND_GRAPH]]  

### 6.1 Latent Cognitive State Clustering
[`latent.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/state/latent.py) projects heterogeneous trace features (token distribution, tool diversity, transition rates, latency) into an embedding space. It clusters raw agent actions into high-level cognitive modes:
- **`PLANNING`**: High ratio of reasoning tokens, structured list generation, goal decomposition.
- **`EXPLORATION`**: High frequency of read-only tool calls, web searches, documentation lookups.
- **`ACTUATION`**: Invocation of state-mutating external tools (APIs, filesystem writes, payments).
- **`REFLECTION`**: Self-evaluation, error checking, critique passes.
- **`ERROR_RECOVERY`**: Backoff retries, exception handling, fallback tool selection.

```text
       [USER INPUT]
            │
            ▼
     ┌──────────────┐
     │   PLANNING   │◄───────────────────────┐
     └──────┬───────┘                        │
            │                                │
            ▼                                │
     ┌──────────────┐      Failure     ┌─────┴──────────┐
     │ EXPLORATION  ├─────────────────►│ ERROR_RECOVERY │
     └──────┬───────┘                  └─────┬──────────┘
            │                                │
            ▼ Success                        │ Fixed
     ┌──────────────┐                        │
     │  ACTUATION   │◄───────────────────────┘
     └──────┬───────┘
            │
            ▼
     ┌──────────────┐
     │  REFLECTION  │
     └──────────────┘
```

### 6.2 Reasoning Motifs (`motifs.py`)
Discovers recurrent subgraphs within execution trees:
- Identifies healthy motifs (e.g., `Verify-Before-Write`, `Double-Check-Approval`).
- Flags pathological motifs (e.g., `Hallucination-Retry-Loop`, `Thrashing-Search`, `Circular-Delegation`).

---

## Section 7: Behavioral Genome & Statistical Drift Detection

> **Submodule:** [`panoptis/behaviour/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/behaviour/)  
> **Source Files:** [`drift.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/behaviour/drift.py), [`profile.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/behaviour/profile.py)  
> **Obsidian Backlink:** [[04_STORAGE_CAUSALITY_AND_GRAPH]]  

### 7.1 The Agent Genome
An **Agent Genome** is a versioned behavioral fingerprint synthesizing an agent's structural tendencies:
- Transition probability matrix between cognitive states.
- Average branch factor of subagent forks.
- Tool preference entropy.
- Token consumption distribution.

### 7.2 Permutation-Based Drift Detection (`drift.py`)
When upstream model weights change, prompt templates are modified, or tools are updated, agent behavior can silently degrade. AgentWatch detects drift without relying on flaky LLM judges:
- Computes **Wasserstein Distance** and **Kolmogorov-Smirnov statistics** between production execution distributions and baseline genome snapshots.
- Executes Monte Carlo permutation tests ($N=1000$ permutations) to yield statistically rigorous $p$-values.
- Flags silent behavioral degradation (e.g., agent becomes 30% more risk-prone or skips reflection steps) before catastrophic errors occur in production.

---

## Section 8: Deterministic Replay (L0–L4) & Counterfactual Falsification

> **Submodule:** [`panoptis/replay/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/replay/), [`panoptis/rollback/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/rollback/)  
> **Source Files:** [`engine.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/replay/engine.py), [`counterfactual.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/replay/counterfactual.py)  
> **Obsidian Backlink:** [[01_ARCHITECTURE_AND_PLANES]]  

### 8.1 The Replay Hierarchy
AgentWatch implements five graduated levels of execution replay:

| Level | Mode | Behavior & Environment |
| :--- | :--- | :--- |
| **L0** | **Static Inspection** | Visual step-by-step trace walkthrough without executing any code. |
| **L1** | **Deterministic Mock** | Re-runs agent logic against sealed, recorded tool responses from the `ObservationStore`. |
| **L2** | **Sandboxed Live Tool** | Re-executes the agent within an isolated container, calling real sandbox tool APIs. |
| **L3** | **Counterfactual Perturbation** | Replays execution while injecting synthetic deltas (latency, tool errors, altered responses). |
| **L4** | **Live Shadowing** | Runs candidate agent models side-by-side with production streams to compare trajectories. |

### 8.2 Counterfactual Invariant Falsification (`counterfactual.py`)
Enables formal stress-testing of agent safety rules:
- Injects simulated API downtime or adversarial prompts at step $K$.
- Simulates whether downstream safety contracts ([[02_EVIDENCELAB_AND_TEMPORAL_CONTRACTS]]) hold under perturbed conditions.

---

## Section 9: Active Defense, Circuit Breakers & Human-in-the-Loop (HITL)

> **Submodule:** [`panoptis/circuit_breaker/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/circuit_breaker/), [`panoptis/hitl/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/hitl/)  
> **Source Files:** [`breaker.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/circuit_breaker/breaker.py), [`orchestrator.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/hitl/orchestrator.py), [`decisions.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/hitl/decisions.py)  
> **Obsidian Backlink:** [[01_ARCHITECTURE_AND_PLANES]]  

### 9.1 Safety Circuit Breakers (`breaker.py`)
Protects production infrastructure from runaway agent loops:
- **Consecutive Error Breakers**: Trips when an agent repeats failed tool invocations without altering arguments.
- **Budget Breakers**: Trips when an execution exceeds token, dollar, or wall-clock limits.
- **Recursion Depth Breakers**: Halts infinite subagent delegation chains.

### 9.2 Human-in-the-Loop Orchestration (`orchestrator.py`)
Manages human escalation when invariants require manual validation:
- Generates cryptographically signed approval tickets.
- Exposes approval interfaces via REST API, Webhooks, or Slack/Discord bots.
- Pauses agent thread execution until human decision is committed.

---

## Section 10: Cost Governance, Complexity Routing & Semantic Caching

> **Submodule:** [`panoptis/cost/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/cost/)  
> **Source Files:** [`governance.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/cost/governance.py), [`complexity_router.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/cost/complexity_router.py), [`caching.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/cost/caching.py), [`anomaly.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/cost/anomaly.py)  

### 10.1 Real-Time Cost Accounting
Calculates token expenditures across prompt tokens, completion tokens, and cached tokens using continuously updated model pricing tables.

### 10.2 Complexity-Aware Model Router (`complexity_router.py`)
Analyzes incoming reasoning requirements and routes simple tasks to cost-effective small models (SLMs) while reserving expensive frontier models for high-complexity planning steps.

### 10.3 Semantic Caching (`caching.py`)
Vector-similarity caching layer that identifies semantically equivalent prompts and returns validated prior completions, slashing latency and API expenses.

---

## Section 11: Enterprise Security, Redaction & Privacy Crypto-Shredding

> **Submodule:** [`panoptis/evidence/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/), [`panoptis/security/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/security/)  
> **Source Files:** [`redaction.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/redaction.py), [`crypto.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/evidence/crypto.py)  
> **Policy Anchor:** [`SECURITY-BASELINE.md`](file:///data/data/com.termux/files/home/AgentWatch/SECURITY-BASELINE.md)  
> **Obsidian Backlink:** [[07_SECURITY_BASELINE_AND_DEVSECOPS]]  

### 11.1 Zero-Baseline Security Architecture
AgentWatch strictly bans global baseline files (`bandit-baseline.json`). Every Bandit finding must be resolved or addressed with a scoped inline suppression:
```python
rng = random.Random(seed)  # noqa: S311  # nosec B311 — seeded statistics, not security
```
Every suppression is documented with an architectural justification in [`SECURITY-BASELINE.md`](file:///data/data/com.termux/files/home/AgentWatch/SECURITY-BASELINE.md).

### 11.2 Edge Redaction & Redaction Manifests
PII (emails, credit cards, SSNs) and API keys are redacted at the sensor edge *before* storage:
- Stores an immutable `redaction_manifest` detailing detector version and redacted field locations.
- Guarantees sensitive plaintexts never touch persistent disks.

### 11.3 Cryptographic Shredding (GDPR/HIPAA Compliance)
Artifacts are encrypted with per-user or per-session ephemeral keys:
- To execute a GDPR "Right to be Forgotten" erasure request, AgentWatch destroys the session encryption key.
- The underlying hash-sealed evidence segments remain immutable, but all associated payload blobs become permanently unrecoverable.

---

## Section 12: Developer Interfaces: FastAPI Server, Typer CLI & Web UI

> **Submodules:** [`panoptis/api/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/api/), [`panoptis/cli/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/cli/), [`frontend/`](file:///data/data/com.termux/files/home/AgentWatch/frontend/)  
> **Obsidian Backlink:** [[05_PLATFORM_API_AND_FRONTEND]]  

### 12.1 FastAPI High-Throughput Engine (`server.py`)
- Async REST endpoints for ingestion, contract verification, and graph traversal.
- Real-time WebSocket streaming (`/ws/v3/live`) broadcasting event arrivals and circuit breaker trips.

### 12.2 Typer Developer CLI (`main.py`)
Full terminal suite supporting headless CI verification, interactive replay, and offline lab analysis:
```bash
panoptis contract verify ./traces/run.json --contract ./contracts/safety.json
panoptis replay --run-id 8a4c-1290 --level L2
panoptis doctor
```

### 12.3 Next.js Frontends
1. **Application Dashboard (`frontend/`)**: Next.js 14, React 18, Tailwind CSS. Features Live Execution Map, Swimlane Timeline, and EvidenceLab Verification Waterfall.
2. **Platform Landing Page (`agentwatch-landing/`)**: Next.js 16, React 19. Features marketing architecture guides and dynamic contributor wall integration.

---

## Section 13: Academic, Industry & Web Intelligence Sources

*Intelligence gathered via `agent-reach` routing across web, dev, and standards backends:*

1. **OpenTelemetry GenAI Semantic Conventions**:
   - *Specification*: OpenTelemetry Semantic Conventions for Generative AI Systems (v1.28.0+).
   - *Source*: [https://opentelemetry.io/docs/specs/semconv/gen-ai/](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
   - *Influence*: Governs AgentWatch's sensor normalization in `panoptis/sensors/otel/`.
2. **RFC 8785: JSON Canonicalization Scheme (JCS)**:
   - *Standard*: IETF RFC 8785 (Rundgren, Jordan, Erdtman).
   - *Source*: [https://datatracker.ietf.org/doc/html/rfc8785](https://datatracker.ietf.org/doc/html/rfc8785)
   - *Influence*: Governs canonical event serialization in `panoptis/evidence/canonical.py`.
3. **Lamport's Logical Clocks & Partial Ordering**:
   - *Paper*: "Time, Clocks, and the Ordering of Events in a Distributed System", Leslie Lamport, Communications of the ACM, 1978.
   - *Influence*: Foundational basis for Issue #729 happens-before partial order engine.
4. **Metric First-Order Temporal Logic (MFOTL)**:
   - *Research*: "Monitoring Metric First-Order Temporal Logic", David Basin, Felix Klaedtke, Samuel Müller, Journal of the ACM.
   - *Influence*: Governs temporal operator formulation in `panoptis/contracts/evaluator.py`.
5. **OWASP Top 10 for Large Language Models**:
   - *Standard*: OWASP Foundation LLM Security Guidelines (LLM01: Prompt Injection, LLM02: Insecure Output Handling).
   - *Source*: [https://genai.owasp.org/](https://genai.owasp.org/)
   - *Influence*: Informs the Information Lineage taint certifier in `panoptis/provenance/`.
6. **Cooperative Game Theory & Shapley Value Attribution**:
   - *Foundation*: "A Value for n-Person Games", Lloyd Shapley, 1953; "A Unified Approach to Interpreting Model Predictions", Scott Lundberg & Su-In Lee, NeurIPS 2017.
   - *Influence*: Powers multi-agent credit attribution in `panoptis/orchestration/shapley.py`.

---

## Section 14: Comprehensive Backlinks & Cross-Reference Table

| Dossier | Core Subject Matter | Primary Code Directory |
| :--- | :--- | :--- |
| **[[README]]** | Master Vault Index & Anatomy | [`~/AgentWatch/`](file:///data/data/com.termux/files/home/AgentWatch/) |
| **[[01_ARCHITECTURE_AND_PLANES]]** | The 6 Planes & Monolith Invariants | [`panoptis/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/), [`docs/v3/`](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/) |
| **[[02_EVIDENCELAB_AND_TEMPORAL_CONTRACTS]]** | 4-Valued Verdicts & PR #728 | [`panoptis/contracts/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/) |
| **[[03_EVENT_ALGEBRA_AND_NORMALIZERS]]** | Event Schemas & Ingest Sensors | [`panoptis/events/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/events/), [`panoptis/sensors/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/sensors/) |
| **[[04_STORAGE_CAUSALITY_AND_GRAPH]]** | 3-View Hypergraph & Provenance | [`panoptis/graph/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/graph/), [`panoptis/causality/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/causality/) |
| **[[05_PLATFORM_API_AND_FRONTEND]]** | FastAPI, CLI & Next.js Apps | [`panoptis/api/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/api/), [`frontend/`](file:///data/data/com.termux/files/home/AgentWatch/frontend/) |
| **[[06_ROADMAP_AND_ISSUES_729_738]]** | Contributor Milestone Issues | [GitHub Issues #729–#738](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues) |
| **[[07_SECURITY_BASELINE_AND_DEVSECOPS]]** | Zero-Baseline Bandit Standards | [`SECURITY-BASELINE.md`](file:///data/data/com.termux/files/home/AgentWatch/SECURITY-BASELINE.md) |
| **[[08_MOBILE_TERMUX_WORKFLOW]]** | Mobile Development Runbook | [`~/.termux/`](file:///data/data/com.termux/files/home/.termux/) |
| **[[09_COMPREHENSIVE_FEATURE_MATRIX]]** | Master Feature Dossier | Full Engine Specification |
