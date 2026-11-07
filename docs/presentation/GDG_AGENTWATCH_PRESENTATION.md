# GDG 2026 Keynote Presentation: Sovereign Agentic Security with Panoptis & Google ADK

**Title:** Securing the Autonomous Agent Frontier: Pre-Execution Hypervisors & Google ADK  
**Presenter:** Antigravity & Pair Programming Partner  
**Target Event:** Google Developer Groups (GDG) 2026 / Agent Ecosystem Summit  
**Repository:** `Shinraya-Tech-Labs/AgentWatch` (`panoptis`)

---

## Executive Presentation Deck & Review Structure

```
┌────────────────────────────────────────────────────────────────────────┐
│                        THE 5-ACT PRESENTATION DECK                     │
│                                                                        │
│   [ACT I]    The Agentic Paradox (Chatbots vs. Autonomous Agents)       │
│   [ACT II]   The Perimeter Illusion (Why Model Armor + gVisor Fail)    │
│   [ACT III]  Panoptis Architecture (Pre-Execution Hypervisor & IFC)   │
│   [ACT IV]   Google Ecosystem Symbiosis (Google ADK Guard & Chronicle) │
│   [ACT V]    Live Demonstration, Fallbacks & Honest Engineering Data   │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Slide 1 — Title Slide
- **Visual:** Minimalist dark mode terminal graphic with split view: on the left, an unconstrained autonomous agent executing `rm -rf /`; on the right, the Panoptis active shield intercepting the syscall in 0.3 ms with a Merkle witness seal.
- **Header:** Securing the Autonomous Frontier: Runtime Hypervisors for Agentic AI
- **Subtitle:** Bringing Pre-Execution Deterministic Mediation & Dual-Lattice IFC to Google ADK
- **Speaker Cue:** "Good afternoon everyone. In 2023, an AI mistake meant an awkward hallucination. In 2026, an AI mistake means an agent with production terminal credentials drops your user database."

---

### Slide 2 — Act I: The Agentic Paradox
- **Core Thesis:** The moment an LLM is granted tools (Bash, Python, SQL, REST APIs), the threat model flips from *content moderation* to *operating system security*.
- **Key Visual:** Comparison Matrix:
  - **2023 Chatbot:** In: Prompt → Out: Text string. Risk: Misinformation, tone violation.
  - **2026 Autonomous Agent:** In: High-level goal → Out: Multi-turn tool execution (`curl`, `psql`, `os.unlink`, git push). Risk: Data exfiltration, privilege escalation, autonomous propagation.
- **Key Takeaway:** An agent cannot police itself. You cannot prompt an LLM to "please never run malicious shell code" when prompt injection rewrites its context window.

---

### Slide 3 — Act II: The Security Perimeter Illusion
- **The Failure of Traditional Defenses:**
  - **Prompt Firewalls (e.g. Model Armor):** Inspects input/output text only at the front door. Blind to indirect prompt injection in downloaded files, API payloads, or multi-turn gradual drift.
  - **Container Sandboxes (e.g. gVisor, Docker):** Isolates the host OS from container breakout, but gives the agent complete, unmonitored freedom *inside* the container to drop internal databases or exfiltrate credentials over outbound HTTP.
  - **The Lethal Trifecta:** When an agent simultaneously possesses (1) untrusted input reading, (2) internal state access, and (3) outbound communication, exfiltration is mathematically guaranteed without an execution-boundary hypervisor.

---

### Slide 4 — Act III: Panoptis — The Pre-Execution Hypervisor
- **Architectural Shift:** Rather than inspecting tokens, Panoptis intercepts tool calls *before* execution in the runtime (`before_tool_callback` / `PreToolUse` hook).
- **Core Security Engines:**
  1. **Dual-Lattice Information Flow Control (IFC):** Mathematically tracks confidentiality and integrity tags. Reading untrusted web data taints the session; tainted data is forbidden from flowing to network or shell execution sinks without human approval.
  2. **Metric Temporal Logic (MTL) Trajectory Verification:** Blocks piece-wise attack assembly where individually benign commands combine over multiple turns into a reverse shell or exfil loop.
  3. **Zero-Click Exfiltration Defanging:** Renders markdown images, hidden HTML links, and zero-width steganographic covert channels harmless before tool results reach the agent or user.

---

### Slide 5 — Act IV: Google Ecosystem Symbiosis (Google ADK + Panoptis)
- **The Native ADK Integration:**
  ```python
  from google.adk import Agent
  from panoptis.adapters.google_adk import PanoptisADKGuard

  guard = PanoptisADKGuard(policy_path="panoptis.yaml")
  agent = Agent(
      model="gemini-2.0-flash",
      tools=[database_query, bash_exec, fetch_url],
      before_tool_callback=guard.before_tool_callback,
      after_tool_callback=guard.after_tool_callback,
  )
  ```
- **Enterprise Features:**
  - **Latency Overhead:** p50 of 0.3 ms — invisible to real-time Gemini agent loops.
  - **Google SecOps / Chronicle Streaming:** Security findings stream to Cloud Logging / PubSub with Merkle execution witness seals.
  - **Sovereignty & Compliance:** Ready for EU AI Act, SOC2 Type II, FedRAMP deterministic audit requirements.

---

### Slide 6 — Act V: Live Demonstration & The Three Mediation Paths
- **Live Flow:**
  1. **ALLOW:** Safe file read (`read_file("README.md")`) passes instantly.
  2. **BLOCK:** Injection trigger (`bash("rm -rf /")`) is intercepted; execution is aborted; circuit breaker transitions to tripped.
  3. **ESCALATE (HITL):** Sensitive path traversal (`read_file("../../../../etc/shadow")`) is paused; an approval request (`apr-xxxx`) is generated for operator review via CLI or Slack webhook.
  4. **CRYPTOGRAPHIC LEDGER:** Merkle hash-chain is inspected and verified live on stage (`ledger.verify() -> ok=True`).

---

### Slide 7 — Honest Engineering Disclosures (No Snake Oil)
- **Measured Empirical Benchmark (Fixed 80-Case Corpus):**
  - **Attack Recall:** 75.0% (30/40 attack variations blocked).
  - **False Positive Rate:** 47.5% on tricky benign cleanup commands (e.g. `rm -rf ./node_modules`, `DROP TABLE IF EXISTS`). *Published transparently as an ongoing tuning area.*
  - **Inline Latency:** 0.3 ms p50 / 0.7 ms p95.
- **Architectural Limitations:**
  - *Erasure:* Memory & payload crypto-shredding destroys decryption keys; physical SSD flash blocks are subject to wear-leveling controllers.
  - *Persistence:* Ephemeral in-memory mode requires PostgreSQL for clustered multi-tenant deployments.
  - *Integrations:* Pre-execution enforcement is supported on Google ADK, Claude Code, LangChain, LangGraph, AutoGen, smolagents, and MCP Gateway; generic stream-reading adapters remain post-execution observation.

---

### Slide 8 — Live Demo Contingency & Fallback Protocol

In the event of conference Wi-Fi failure, projector disconnect, or cloud API throttling:

| Condition | Fallback Action | Execution Time | Offline? |
|---|---|---|---|
| **Primary Live Demo** | Interactive ADK Gemini agent execution | ~5–10s | Requires Internet |
| **Fallback A (Local CLI)** | `panopti demo --fast` | **<0.2s** | **100% Offline** |
| **Fallback B (Terminal TUI)** | `python demo.py` | **~2s** | **100% Offline** |
| **Fallback C (Deterministic Verification)** | `python examples/live_mediation_demo.py` | **~0.4s** | **100% Offline** |

---

### Review Sign-off Checklist

- [x] All relevant GitHub CI workflows verified green (ruff, pytest, bandit, docs, benchmark incident check).
- [x] Fresh installation verified via `pip install -e .`.
- [x] Live demo tested in clean isolated temporary environment.
- [x] All 3 mediation paths (ALLOW, BLOCK, ESCALATE_HITL) verified live.
- [x] README and presentation verified free of ungrounded or exaggerated claims.
- [x] 100% offline demo fallback scripts implemented and verified (`examples/live_mediation_demo.py`, `demo.py`, `panopti demo --fast`).
- [x] Joint presentation narrative and engineering disclosures reviewed.
