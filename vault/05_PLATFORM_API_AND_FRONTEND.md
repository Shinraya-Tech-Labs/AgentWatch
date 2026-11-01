# 05 — Platform API, CLI & Frontend Interfaces

> **Implementation Directories:**  
> - [`panoptis/api/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/api/) (FastAPI REST, WebSocket & Stream APIs)  
> - [`panoptis/cli/`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/cli/) (Typer CLI commands)  
> - [`frontend/`](file:///data/data/com.termux/files/home/AgentWatch/frontend/) (Next.js 14 / React 18 Application)  
> - [`agentwatch-landing/`](file:///data/data/com.termux/files/home/AgentWatch/agentwatch-landing/) (Next.js 16 / React 19 Marketing Site)  
> - [`grafana/`](file:///data/data/com.termux/files/home/AgentWatch/grafana/) (Prometheus Metrics & Dashboards)  

---

## ⚡ The API Plane (`panoptis/api/`)

AgentWatch exposes a high-throughput, async REST and WebSocket API built on **FastAPI** and **SQLAlchemy async**:

### Core Endpoints
- **Ingestion (`/api/v3/ingest`)**:
  - Accepts raw OTLP spans, Claude Code JSONL, or native observation envelopes.
  - Returns `202 Accepted` with batch IDs for asynchronous normalizer processing.
- **Contracts (`/api/v3/contracts`)**:
  - `POST /verify`: Evaluates an execution trace against an `EvidenceContract` payload.
  - Returns 4-valued verdict, minimal witness events, and remediation recommendations.
- **Lineage & Graphs (`/api/v3/graph`)**:
  - `GET /runs/{run_id}/execution`: Returns the hierarchical call tree.
  - `GET /runs/{run_id}/provenance`: Returns the artifact information lineage DAG.
  - `GET /runs/{run_id}/causal`: Returns the happens-before partial order.
- **Real-Time Streaming (`/ws/v3/live`)**:
  - WebSocket channel broadcasting live `ComputationalEvent`s, active invariants, and circuit breaker trip states.

---

## 💻 Developer CLI (`panoptis/cli/`)

The CLI provides terminal access to all core verification and debugging facilities:

```bash
# Ingest and verify an agent trace file against a contract
panoptis contract verify ./traces/run_102.json --contract ./contracts/approval.json

# Launch the offline EvidenceLab local comparison harness
panoptis lab compare ./traces/run_a.json ./traces/run_b.json

# Deterministically replay an agent session up to step N
panoptis replay --run-id 8a4c-1290 --level L2 --until-step 14

# Scan environment and check active sensors
panoptis doctor
```

---

## 🖥️ Next.js Web Applications

AgentWatch maintains two modern frontend codebases:

### 1. Main Dashboard (`frontend/`)
- **Stack**: Next.js 14, React 18, Tailwind CSS, Jest.
- **Key Views**:
  - **Live Map**: Real-time graph visualization of communicating agents, tool calls, and subagent forks.
  - **Timeline**: Chronological and causal swimlanes showing event intervals with uncertainty error bars.
  - **EvidenceLab**: Contract evaluation dashboard displaying 4-valued verdicts (`SUPPORTED`, `REFUTED`, `INCONCLUSIVE`) and telemetry gap waterfalls.
  - **Genome & Drift**: PCA/t-SNE latent state clusters comparing production runs to baseline behavioral distributions.
  - **Compare**: Side-by-side diff of two runs, highlighting prompt divergence, branch points, and token cost variations.
- **Accessibility**: Audited under WCAG 2.2 AA guidelines ([`accessibility_audit.md`](file:///data/data/com.termux/files/home/AgentWatch/frontend/accessibility_audit.md)).

### 2. Public Platform Site (`agentwatch-landing/`)
- **Stack**: Next.js 16, React 19, Tailwind CSS.
- **Purpose**: Showcase the platform architecture, live documentation, and dynamic contributor wall (`fetch_stats.mjs`).
