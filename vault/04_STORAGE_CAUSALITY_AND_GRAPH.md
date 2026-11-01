# 04 — Storage, Causality & The Graph Plane

> **Specification Anchor:** [`docs/v3/adr/0005-three-views-one-relation-store.md`](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0005-three-views-one-relation-store.md)  
> **Implementation Modules:**  
> - [`panoptis/graph/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/graph/) (Graph storage, relations, and traversals)  
> - [`panoptis/provenance/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/provenance/) (Lineage, DAG acyclicity, and taint propagation)  
> - [`panoptis/causality/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/causality/) (Causal graph construction and blast radius)  
> - [`panoptis/state/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/state/) (Latent state clustering and clustering invariants)  

---

## 🗄️ One Relation Store, Three Views ([ADR-0005](file:///data/data/com.termux/files/home/AgentWatch/docs/v3/adr/0005-three-views-one-relation-store.md))

Traditional observability splits graph data across incompatible stores (e.g., Neo4j for provenance, Jaeger for execution spans, vector databases for memory). AgentWatch unifies graph data into a **single relational model of hyperedges**, projected into three distinct views:

```text
                        ┌───────────────────────────────┐
                        │   Hyperedge Relation Store    │
                        │ (PostgreSQL async / In-Memory)│
                        └───────────────┬───────────────┘
                                        │
             ┌──────────────────────────┼──────────────────────────┐
             ▼                          ▼                          ▼
   ┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
   │  EXECUTION VIEW  │       │ INFORMATION VIEW │       │   CAUSAL VIEW    │
   │                  │       │                  │       │                  │
   │ • Call Tree      │       │ • Read / Write   │       │ • Happens-Before │
   │ • Delegation     │       │ • Data Lineage   │       │ • Counterfactual │
   │ • Span Sequence  │       │ • Taint Flow     │       │ • Blast Radius   │
   └──────────────────┘       └──────────────────┘       └──────────────────┘
```

1. **`EXECUTION` View**:
   - Represents the physical invocation structure: which agent summoned which subagent, which step called which tool, and the parent-child span hierarchy.
2. **`INFORMATION` View**:
   - Tracks data artifacts and representations: which documents were retrieved into context, how prompt templates were synthesized, and how tool outputs mutated downstream memory.
   - Powers **taint tracking**: tracing an adversarial prompt injection backwards to the source document or forward to an unauthorized database write.
3. **`CAUSAL` View**:
   - Establishes genuine causal dependencies ($A \prec B$).
   - Distinguishes incidental temporal coincidence from true counterfactual dependence: would $B$ have occurred if $A$ had returned a failure?

---

## 🧬 Information Lineage & Taint Certifier (`panoptis/provenance/`)

The provenance engine evaluates DAG acyclicity and data dependency chains:
- **Derivation Depth**: Measures how many transformation steps separate an output from original source documents.
- **Taint Certifier**: Flags when untrusted user inputs cross trust boundaries into privileged model prompts without intermediate sanitization.
- **Bi-Directional Traversal**: Answers both *"Where did this variable come from?"* (upstream lineage) and *"What systems did this tool call impact?"* (downstream blast radius).

---

## 🧠 Latent State Clustering & Motifs (`panoptis/state/`)

AgentWatch clusters high-dimensional agent execution traces into recognizable **cognitive states**:
- **State Discovery**: Groups sequences of `ComputationalEvent`s into discrete macro-states:
  - `PLANNING`: Heavy reasoning, chain-of-thought, sub-goal generation.
  - `EXPLORATION`: Multiple read-only tool calls, web searches, documentation lookups.
  - `ACTUATION`: Destructive or state-mutating tool execution (writes, database updates, transfers).
  - `ERROR_RECOVERY`: Catching exceptions, reformulating prompts, backoff loops.
- **Reasoning Motifs**: Recurring multi-step patterns across sessions.
- **Behavioral Drift Detection**: Compares current trace clusters against baseline distributions using Monte Carlo permutation tests to flag subtle reasoning degradation before errors trigger outages.
