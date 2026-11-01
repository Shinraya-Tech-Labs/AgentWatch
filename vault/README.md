# AgentWatch Knowledge Vault

> **Repository:** [Shinraya-Tech-Labs/AgentWatch](https://github.com/Shinraya-Tech-Labs/AgentWatch)  
> **Status:** v3.1 EvidenceLab Architecture (PR #728 Merged, Commit `f87693a`)  
> **Environment:** Mobile Termux (Linux aarch64) with Antigravity & Active Skills  
> **Date:** October 2026  

---

## 🧭 Vault Navigation Index

This Knowledge Vault contains a complete, verified architectural scan, systems analysis, and roadmap dossier for the AgentWatch codebase.

| File | Topic & Scope | Key Codebase Anchors |
| :--- | :--- | :--- |
| **[01_ARCHITECTURE_AND_PLANES.md](./01_ARCHITECTURE_AND_PLANES.md)** | The 6 Architectural Planes (A–F), unidirectional data flow, immutability invariants | `panoptis/evidence/`, `panoptis/events/`, `docs/v3/` |
| **[02_EVIDENCELAB_AND_TEMPORAL_CONTRACTS.md](./02_EVIDENCELAB_AND_TEMPORAL_CONTRACTS.md)** | 4-valued epistemic logic, open-world assumptions, minimal witnesses, PR #728 state | `panoptis/contracts/` (`schema.py`, `evaluator.py`, `remedies.py`) |
| **[03_EVENT_ALGEBRA_AND_NORMALIZERS.md](./03_EVENT_ALGEBRA_AND_NORMALIZERS.md)** | `ComputationalEvent` model, temporal intervals $\pm \delta$, multi-framework normalizers | `panoptis/events/`, `panoptis/adapters/`, `panoptis/evidence/` |
| **[04_STORAGE_CAUSALITY_AND_GRAPH.md](./04_STORAGE_CAUSALITY_AND_GRAPH.md)** | Hyperedge relation store, 3 views (Execution, Information, Causal), provenance DAG | `panoptis/causality/`, `panoptis/graph/`, `panoptis/storage/` |
| **[05_PLATFORM_API_AND_FRONTEND.md](./05_PLATFORM_API_AND_FRONTEND.md)** | FastAPI backend, Typer CLI, Next.js 14 frontend, Next.js 16 landing page | `panoptis/api/`, `panoptis/cli/`, `frontend/`, `agentwatch-landing/` |
| **[06_ROADMAP_AND_ISSUES_729_738.md](./06_ROADMAP_AND_ISSUES_729_738.md)** | Deep breakdown of 10 roadmap issues (#729–#738) & implementation blueprint for #729 | `panoptis/contracts/`, GitHub Issues #729–#738 |
| **[07_SECURITY_BASELINE_AND_DEVSECOPS.md](./07_SECURITY_BASELINE_AND_DEVSECOPS.md)** | Zero-baseline security philosophy, Bandit scoped inline suppressions, CI matrix | `SECURITY-BASELINE.md`, `.github/workflows/` |
| **[08_MOBILE_TERMUX_WORKFLOW.md](./08_MOBILE_TERMUX_WORKFLOW.md)** | Mobile Termux environment, Antigravity workflow, tmux, memory hygiene, active skills | `~/.agents/skills/`, `~/.agents/rules/` |
| **[09_COMPREHENSIVE_FEATURE_MATRIX.md](./09_COMPREHENSIVE_FEATURE_MATRIX.md)** | Exhaustive 400+ line technical architecture dossier, mathematical models, sources & backlinks | Entire Engine & Platform (`panoptis/`) |

---

## 📚 Deep-Dive Feature Dossiers (>= 400 Lines Each)

Every core capability of AgentWatch is fully documented in an independent, exhaustive technical dossier containing mathematical formulations, code listings, architecture diagrams, `[[Obsidian backlinks]]`, and external standards citations via `agent-reach`:

| # | Feature Dossier | Module Anchors | Scope & Architectural Focus | Lines |
| :-: | :--- | :--- | :--- | :-: |
| **01** | **[01_TEMPORAL_CONTRACTS_AND_EVIDENCELAB.md](./features/01_TEMPORAL_CONTRACTS_AND_EVIDENCELAB.md)** | `panoptis/contracts/` | 4-valued epistemic logic, OWA, minimal witnesses, remedies | 432 |
| **02** | **[02_COMPUTATIONAL_EVENT_ALGEBRA.md](./features/02_COMPUTATIONAL_EVENT_ALGEBRA.md)** | `panoptis/events/` | RFC 8785 canonical hashes, $\pm \delta$ temporal intervals, lifecycle states | 411 |
| **03** | **[03_INGEST_SENSORS_AND_ADAPTERS.md](./features/03_INGEST_SENSORS_AND_ADAPTERS.md)** | `panoptis/sensors/`, `adapters/` | OTel GenAI, LangChain, CrewAI, Claude Code hook adapters | 410 |
| **04** | **[04_HYPEREDGE_GRAPH_AND_PROVENANCE.md](./features/04_HYPEREDGE_GRAPH_AND_PROVENANCE.md)** | `panoptis/graph/`, `provenance/` | 3-view hyperedge store, backward lineage DAGs, cycle breakers | 473 |
| **05** | **[05_CAUSAL_INFERENCE_AND_BLAST_RADIUS.md](./features/05_CAUSAL_INFERENCE_AND_BLAST_RADIUS.md)** | `panoptis/causality/`, `core/` | Pearl's Ladder, causal cones, SAF-004 predictive blast radius | 446 |
| **06** | **[06_LATENT_STATE_AND_MOTIFS.md](./features/06_LATENT_STATE_AND_MOTIFS.md)** | `panoptis/state/`, `behaviour/` | 8-D windowing, emergent polar vocabularies, motifs M001–M007 | 551 |
| **07** | **[07_BEHAVIORAL_GENOME_AND_DRIFT.md](./features/07_BEHAVIORAL_GENOME_AND_DRIFT.md)** | `panoptis/behaviour/` | 25-trait genome, bootstrap 95% CIs, permutation tests, BH FDR | 442 |
| **08** | **[08_REPLAY_AND_COUNTERFACTUALS.md](./features/08_REPLAY_AND_COUNTERFACTUALS.md)** | `panoptis/replay/`, `rollback/` | Time travel, counterfactual $do(x)$, anti-Zip-Slip state rollback | 440 |
| **09** | **[09_CIRCUIT_BREAKERS_AND_HITL.md](./features/09_CIRCUIT_BREAKERS_AND_HITL.md)** | `panoptis/circuit_breaker/`, `hitl/` | 4-state active tripwires, EU AI Act Art. 12/14 ambient HITL | 491 |
| **10** | **[10_COST_GOVERNANCE_AND_CACHE.md](./features/10_COST_GOVERNANCE_AND_CACHE.md)** | `panoptis/cost/` | CST-009 complexity router, semantic caching, budget quotas | 409 |
| **11** | **[11_DEVELOPER_APIS_CLI_AND_UI.md](./features/11_DEVELOPER_APIS_CLI_AND_UI.md)** | `panoptis/api/`, `cli/`, `frontend/`| FastAPI, Typer CLI, stdio MCP server, Next.js 14/16 apps | 488 |
| **12** | **[12_ENTERPRISE_SECURITY_AND_PRIVACY.md](./features/12_ENTERPRISE_SECURITY_AND_PRIVACY.md)** | `panoptis/security/` | Zero-baseline Bandit, OWASP Agentic Top 10, PII/PHI redaction | 443 |

---

## 📊 Codebase Anatomy & Metrics

A complete physical scan of the repository reveals **~131,000+ lines of code** across 600+ tracked files:

| Directory | Files | Total Lines | Primary Languages & Breakdown |
| :--- | :--- | :--- | :--- |
| **`panoptis/`** | 268 | 47,471 | Python (47,399 lines across 44 specialized modules) |
| **`tests/`** | 115 | 19,178 | Python (18,691 lines of unit, integration, and contract tests) |
| **`benchmarks/`** | 37 | 29,697 | JSON test cases (26,233 lines), AWBench harness (3,006 lines Python) |
| **`frontend/`** | 39 | 13,914 | Next.js 14, React 18, TypeScript/TSX (3,096 lines), JSON config |
| **`agentwatch-landing/`** | 52 | 8,623 | Next.js 16, React 19, TSX (4,687 lines), JSON (2,160 lines) |
| **`docs/`** | 48 | 7,120 | Markdown specifications (4,637 lines), diagrams & screenshots |
| **Root & Config** | 31 | 3,616 | `SECURITY-BASELINE.md`, `pyproject.toml`, Docker, CI workflows |
| **Grafana & Helm** | 5 | 1,566 | Grafana dashboard JSON (1,432 lines), Helm charts (134 lines) |
| **Scripts** | 3 | 522 | Python utilities (393 lines), shell scripts (129 lines) |

---

## 🔬 Core Mission & Premise

AgentWatch is not another prompt firewall or LLM-as-a-judge wrapper. Its foundational research question:

> *"Can the behaviour of a complex AI-native software system be reconstructed from observable events into a causal, temporal and partially executable system model that can explain past behaviour and estimate future or counterfactual trajectories?"*

Unlike naive telemetry tools that record raw logs and report false alarms on missing data, AgentWatch's **v3.1 EvidenceLab** introduces **open-world epistemic logic**:
1. **Differentiates Absence of Proof from Proof of Absence**: If an uninstrumented tool ran, the verdict is `INCONCLUSIVE`—never a false `REFUTED`.
2. **Generates Minimal Witnesses**: Pinpoints the exact subset of events proving or refuting a temporal invariant.
3. **Prescribes Corrective Remediation**: Recommends the missing telemetry sensor or configuration needed to turn an inconclusive contract into a definitive proof.
