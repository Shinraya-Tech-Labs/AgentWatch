# 06 — Roadmap Issues (#729 – #738) & Blueprint for #729

> **Repository Anchor:** [Shinraya-Tech-Labs/AgentWatch Issues](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues)  
> **Maintainer Context:** Curated by Project Admin for Open-Source Contributors (SSOC / NSOC / ELUSoC 2026 tracks)  
> **Admin Role:** Project governance, contributor PR triage, CI verification, and core engine stewardship.  

---

## 🗺️ The 10 Roadmap Issues

The project maintains 10 prioritized roadmap issues split between practical distributed systems challenges (`mid`) and formal verification / cryptographic attestation (`hard`):

### Mid-Tier Roadmap (Distributed Systems & Stream Architecture)

| Issue | Title | Scope & Objectives |
| :--- | :--- | :--- |
| **[#729](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues/729)** | **Interval Temporal Logic & Happens-Before Partial Order Engine** | Resolve race conditions under clock drift using Lamport happens-before DAGs and interval uncertainty bounds. |
| **[#730](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues/730)** | **Sequence-Gap Detection & Dynamic Capture Profile Introspection** | Detect dropped frames in event streams and dynamically query agent nodes for missing telemetry. |
| **[#731](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues/731)** | **Asynchronous Contract Evaluation & Incremental Stream Processor** | Sliding-window streaming evaluator for unbounded trace streams with real-time state updates. |
| **[#732](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues/732)** | **OTel GenAI Semantic Conventions v2 Normalizer & MCP Protocol Tap** | Native support for latest OpenTelemetry GenAI standards and real-time Model Context Protocol taps. |
| **[#733](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues/733)** | **Proof & Gaps Witness Timeline & Coverage Waterfall Visualizer** | Interactive frontend visualization of contract proofs, missingness reasons, and sensor gaps. |

### Hard-Tier Roadmap (Formal Verification & Verifiable Attestation)

| Issue | Title | Scope & Objectives |
| :--- | :--- | :--- |
| **[#734](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues/734)** | **First-Order Metric Temporal Logic (MFOTL) Engine with Open-World Quantifiers** | Support rich multi-agent first-order formulas with open-world existential/universal quantifiers. |
| **[#735](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues/735)** | **Counterfactual Replay L3 Perturbation Engine & Invariant Falsification** | Synthetic fault injection harness to verify whether an invariant holds under adversarial perturbations. |
| **[#736](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues/736)** | **Distributed Hyperedge Relation Store with O(1) Bi-Directional Lineage Traversal** | High-performance graph storage engine capable of querying multi-million node lineage graphs in sub-millisecond time. |
| **[#737](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues/737)** | **AWBench-Sufficiency: Adversarial Missingness Oracle & Blind Split Verification** | Benchmark suite to evaluate contract verification accuracy under extreme sensor failure scenarios. |
| **[#738](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues/738)** | **Zero-Knowledge / Merkle-Proof Verifiable Witness Attestation with Crypto-Shredding** | Cryptographic verification of contract compliance without exposing confidential model prompts or tool outputs. |

---

## 🎯 Implementation Blueprint: Issue #729

### Problem Statement
In multi-agent architectures (e.g., orchestrator spawning worker subagents across separate machines or cloud processes), relying on wall-clock `datetime` creates false race conditions:
1. Clock skew between machines makes event $B$ appear before event $A$ in wall-clock time even when $B$ was caused by $A$.
2. Network jitter delays telemetry packets, causing out-of-order event delivery at the ingest gateway.
3. Standard temporal contract verification evaluates these out-of-order traces as contract violations (`REFUTED`), creating false alarms.

### Mathematical Formulation
1. **Happens-Before Relation ($\prec$)**:
   - For events $a, b \in \mathcal{E}$:
     - If $a$ and $b$ are emitted by the same agent process and $\text{seq}(a) < \text{seq}(b)$, then $a \prec b$.
     - If $b$ has an explicit link `PARENT`, `FOLLOWS`, or `RESPONDS_TO` pointing to $a$, then $a \prec b$.
     - Transitivity: If $a \prec b$ and $b \prec c$, then $a \prec c$.
   - **Concurrency ($a \parallel b$)**:
     - If $\neg(a \prec b) \land \neg(b \prec a)$, the events are **concurrent**.
2. **Interval Timestamp Logic**:
   - Each event carries interval $I(e) = [t_{start}(e) - \delta_e, t_{start}(e) + \delta_e]$.
   - If $I(a)$ and $I(b)$ overlap and $a \parallel b$, wall-clock ordering cannot establish precedence. The obligation verdict evaluates to `EvidenceVerdict.CONFLICTING_EVIDENCE` or `INCONCLUSIVE` (with `MissingReason.CLOCK_AMBIGUITY`).

### Planned Code Structure
- **Module**: `panoptis/contracts/order.py` (Lamport graph builder and interval overlap arithmetic).
- **Integration**: Enhance [`panoptis/contracts/evaluator.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/evaluator.py) to check causal graph precedence before falling back to wall-clock timestamps.
- **Tests**: `tests/contracts/test_happens_before_order.py` with 100+ simulated out-of-order event streams.
