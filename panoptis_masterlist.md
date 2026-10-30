# AgentWatch — Complete Feature Masterlist
> v0.1 → Final · 87 features · 9 domains · 14 nobody has built · Apache 2.0
> Researched against: Langfuse, Arize, Galileo, Maxim, Phoenix, Braintrust, Datadog, EU AI Act, SOC 2, HIPAA, OWASP Agentic Top 10

---

## 14 Features Nobody Has Shipped — Your Actual Moat

- Pre-execution causal blocking (not post-hoc)
- Independent model reasoning auditor
- Cross-session causal memory graph
- Inter-agent failure propagation tracing
- Semantic drift detection across sessions
- Counterfactual replay ("what if step 3 was different")
- Adversarial auditor resistance scoring
- Goal coherence scoring over time (not just per-session)
- Agent policy DSL (human-readable YAML rules)
- Model degradation auto-routing
- Causal attribution for compliance ("why did AI do X on date Y")
- Open reasoning trace schema (become the standard)
- Reasoning fingerprinting (detect model swaps mid-session)
- OWASP Agentic Top 10 automated test harness

---

## Priority Legend
- `SHIP NOW` — build before anything else
- `NEXT` — after foundation is stable
- `LATER` — once users validate direction
- `ADVANCED` — moat-deepening, technically hard
- `FINAL` — endgame platform features

---

## 01 · Core Observability
> Everything you need to see what an agent actually did — not just that it returned a 200 OK.

| ID | Feature | Priority | What it does |
|----|---------|----------|-------------|
| OBS-001 | **Full-Span Trace Collection** | `SHIP NOW` | Every agent action captured as a typed span: reasoning, tool-call, memory-read, model-call. Each span records input, output, latency, token count, retry count, error state. The minimum viable schema that makes every failure mode debuggable. |
| OBS-002 | **Live Session Dashboard** | `SHIP NOW` | Real-time WebSocket stream of every action the running agent takes. Colour-coded by span type. Confidence meter updating per step. No polling, no refresh — pure streaming. |
| OBS-003 | **Session Replay (Time-travel Debugging)** | `SHIP NOW` | Scrub through any past session step by step. See exact agent state, tool inputs/outputs, and model response at every point in time. One-click jump to any step. |
| OBS-004 | **Trajectory Mapping** | `NEXT` | Visual graph of the agent's actual execution path vs. intended path. Automatically detects recursive loops, repeated tool calls, dead-end branches. Highlights where the agent deviated from its original goal. |
| OBS-005 | **Silent Failure Detector** | `NEXT` | 1 in 20 agent requests fail silently — the output looks correct but isn't. Statistical anomaly detection across sessions flags when an agent returns plausible-but-wrong outputs. Trained on your agent's baseline, not generic patterns. |
| OBS-006 | **Tool Call Audit Log** | `NEXT` | Every external tool call logged with: tool name, exact arguments, raw response, duration, whether it was retried and why. Surfaces hallucinated arguments and silent retry storms. |
| OBS-007 | **Embedding Drift Heatmap** | `LATER` | Track how the semantic representation of agent outputs shifts over time and across model versions. Cluster analysis surfaces failure modes by semantic similarity. |
| OBS-008 | **Grafana / OTEL Export** | `LATER` | Push all AgentWatch spans and metrics to any OpenTelemetry-compatible backend. Native Grafana dashboard template. Lets enterprise teams absorb AgentWatch data into their existing stack. |
| OBS-009 | **Counterfactual Replay** ⭐ | `ADVANCED` | Nobody has this. Rewind to any step and ask: "what would have happened if the tool returned X instead?" Run the agent forward from that alternate state. The debugging equivalent of a time machine. |
| OBS-010 | **Production Traffic Sampling** | `ADVANCED` | Intelligent sampling at scale: always capture failures in full, sample successes at configurable rates. Reservoir sampling ensures rare events are never dropped. Tail-based sampling keeps full traces for slow outliers. |

---

## 02 · Reasoning Auditor
> The core IP. An independent model whose only job is to find failure before the next action fires.

| ID | Feature | Priority | What it does |
|----|---------|----------|-------------|
| RSN-001 | **Independent Confidence Scorer** ⭐ | `SHIP NOW` | Architecturally separate model scores every step 0.0–1.0. When confidence drops below threshold, next action is held. The insight: an agent scoring its own work is structurally biased toward overconfidence. This fixes that. |
| RSN-002 | **Hallucination Risk Classifier** ⭐ | `SHIP NOW` | Per-step hallucination risk: low / medium / high. Checks whether the agent's stated facts can be grounded in the context it was given. A "high" flag on a tool argument means the agent invented something — before it executes. |
| RSN-003 | **Goal Drift Detector (In-Session)** ⭐ | `SHIP NOW` | Checks at every step whether the agent's current action is still aligned with the original goal. Catches "I was asked to write a report but now I'm trying to access the file system." |
| RSN-004 | **Reasoning Quality Score** | `NEXT` | Rate the quality of the reasoning chain itself across 5 dimensions: coherence, completeness, factual grounding, goal alignment, safety. Not just confidence — actual reasoning quality. |
| RSN-005 | **Adversarial Auditor Resistance** ⭐ | `NEXT` | Nobody has this. Tests whether a sophisticated agent can learn to game the auditor's scoring patterns. Runs adversarial probes against the auditor model itself. If the auditor is gameable, you know before production finds out. |
| RSN-006 | **Semantic Drift Detection (Cross-Session)** ⭐ | `LATER` | Nobody has this. The agent's interpretation of "optimize the pipeline" shifts between Monday and Thursday because context was reconstructed differently. Detects semantic consistency of goals over time, not just within a session. |
| RSN-007 | **Auditor Model Calibration Dashboard** | `LATER` | Track the auditor's false positive / false negative rate over time on your specific agent. Surfaces calibration decay before it starts producing bad scores. Recalibrate with one click. |
| RSN-008 | **Reasoning Fingerprinting** ⭐ | `ADVANCED` | Nobody has this. Generates a semantic fingerprint of the model's reasoning style per session. Detects if the model appears to have changed mid-session (e.g., provider silently rolled out a new version). |
| RSN-009 | **Dual-Level Goal Evaluation** | `ADVANCED` | Step-level: did this reasoning step make sense? Session-level: did the agent actually achieve the user's original goal end-to-end? Most tools do one. Dual-level catches agents that ace every step but still fail the actual job. |
| RSN-010 | **Reasoning Benchmark Suite** | `FINAL` | 200+ adversarial prompts, edge cases, and failure scenarios run against your specific agent configuration before deploying to production. Scores across all 9 reasoning dimensions. Generates a signed benchmark report. |

---

## 03 · Persistent Memory
> The hardest open problem in AI agents. Cross-session identity, causal memory, temporal reasoning — none of which any competitor has shipped in production.

| ID | Feature | Priority | What it does |
|----|---------|----------|-------------|
| MEM-001 | **Episodic Memory Store** | `SHIP NOW` | Structured storage of past sessions: what happened, what the agent decided, what the outcome was. Indexed by project, user, session. Recalled at the start of new sessions to restore context. |
| MEM-002 | **Causal Memory Graph** ⭐ | `LATER` | Nobody has this in production. Replaces flat episodic store with a temporal knowledge graph where every decision has edges to: the context that caused it, constraints that shaped it, and the outcome it produced. Query: "why did we choose X?" returns the full reasoning chain. |
| MEM-003 | **Cross-Session Identity** ⭐ | `LATER` | The hardest open problem. LoCoMo benchmarks show this is where all current systems fail. Maintain stable identity for a user/project across sessions: preferences, past decisions, active constraints, resolved conflicts — without memory staleness. |
| MEM-004 | **Semantic Memory Layer** | `LATER` | Vector store of semantic knowledge accumulated over sessions — not raw conversation history, but distilled facts, preferences, domain knowledge. Fused retrieval: BM25 + semantic similarity + entity matching (Mem0 research shows +29.6pt temporal gain). |
| MEM-005 | **Procedural Memory** | `LATER` | Stores how-to knowledge: procedures the agent has learned work well for specific task types. "For database migration, always verify foreign keys before dropping tables" — captured from past sessions where this prevented failure. |
| MEM-006 | **Temporal Reasoning Engine** | `ADVANCED` | Answer "what was the state of X on date Y?" queries correctly. Current systems fail at temporal multi-hop reasoning. Stores time-indexed snapshots of state and reasons about what was true when — critical for audit, debugging, compliance. |
| MEM-007 | **Memory Staleness Manager** | `ADVANCED` | Memories decay in relevance. A constraint from 6 months ago may no longer apply. Staleness scoring per memory entry, automatic decay curves, conflict detection when new information contradicts old memory. Runs as a nightly background job. |
| MEM-008 | **Natural Language Memory Query** | `ADVANCED` | Ask the memory graph in plain English: "Why did this agent reject approach X last sprint?" Returns a grounded answer with citations to the specific memory nodes that support it. Graph traversal, not RAG. |
| MEM-009 | **Org-Level Knowledge Graph** | `FINAL` | Aggregate memory across all agents in an org into a shared knowledge graph. Discoveries made by Agent A are available to Agent B. Conflicts between agents' knowledge are surfaced and resolved. The org's accumulated AI knowledge becomes a structured, queryable asset. |

---

## 04 · Safety Engine
> Pre-execution blocking is your clearest competitive advantage. Every competitor is post-hoc. Expand this ruthlessly.

| ID | Feature | Priority | What it does |
|----|---------|----------|-------------|
| SAF-001 | **Destructive Command Blocker** ⭐ | `SHIP NOW` | 40+ patterns blocked pre-execution: rm -rf /, curl \| bash, disk formatting, credential exfiltration, DROP TABLE, mass deletion, privilege escalation. Pattern matching + semantic intent detection. Before the action, not after the damage. |
| SAF-002 | **OWASP Agentic Top 10 Coverage** ⭐ | `NEXT` | Nobody has a complete harness. Blocks all 10: prompt injection, tool abuse, excessive permissions, unsafe code execution, data exfiltration, goal hijacking, context poisoning, trust boundary violations, insecure memory access, supply chain attacks. |
| SAF-003 | **PII / PHI Detection and Redaction** | `NEXT` | Scan all tool call arguments and model outputs for PII (names, emails, SSNs, credit cards) and PHI (medical records, diagnoses). Redact before logging. Configurable: redact-and-continue vs. block-and-alert. Required for HIPAA and GDPR. |
| SAF-004 | **Prompt Injection Defence** | `NEXT` | Detect when tool responses or retrieved documents attempt to inject instructions into the agent's context. Jailbreak shield, indirect injection detection (malicious content in retrieved web pages or files). Based on OWASP LLM01. |
| SAF-005 | **Configurable Policy DSL** ⭐ | `LATER` | Nobody has this for agents. Human-readable YAML rules: "block any action that touches production database outside 9am–5pm UTC" or "require human approval for any action costing more than $100". Policies evaluate at runtime, not deploy-time. |
| SAF-006 | **Least-Privilege Tool Scope Enforcement** | `LATER` | Define exactly which tools an agent is allowed to call, with what argument ranges, in which contexts. Attempts to call out-of-scope tools are blocked and logged. Prevents scope creep mid-task. |
| SAF-007 | **Human-in-the-Loop Gate** | `LATER` | When confidence drops below X, or when a specific tool is about to be called, pause and route to a human for approval via Slack / PagerDuty. Agent waits. Human approves or rejects with full context shown. |
| SAF-008 | **Red-Teaming Automation** | `ADVANCED` | Scheduled adversarial tests against your production agent. Sends crafted prompts designed to trigger goal hijacking, tool misuse, and safety violations. Reports which attacks succeeded, with severity scoring. |
| SAF-009 | **Dual-Stage Guardrail Validation** | `ADVANCED` | Input guardrails (on prompts entering the agent) AND output guardrails (on responses leaving it). Required by EU AI Act Article 15. Covers: content safety, PII, prompt injection, domain policy, ungrounded claims. Adds <10ms latency. |
| SAF-010 | **Blast Radius Estimator** ⭐ | `FINAL` | Nobody has this. Before executing any action, estimate: if this fails or produces an unintended side effect, what is the maximum damage? Covers data affected, downstream services, cost exposure, reversibility. High blast radius triggers mandatory human approval. |

---

## 05 · Multi-Agent Coordination
> Where AgentWatch v0.1 ends and the real opportunity begins. You're logging 5-agent crews as isolated sessions. Fix this.

| ID | Feature | Priority | What it does |
|----|---------|----------|-------------|
| MAG-001 | **Shared Crew Context** | `NEXT` | When a CrewAI crew (or any multi-agent system) runs, all agents share one session context — not 5 separate isolated traces. Agent-to-agent calls create edges in the shared graph. The crew is observable as a unified system. |
| MAG-002 | **Inter-Agent Causal DAG** ⭐ | `NEXT` | Nobody has this. Directed Acyclic Graph of which agent called which, what context was passed between them, where confidence / hallucination risk propagated. When Agent 5 fails, trace back to Agent 2's hallucination 6 steps earlier as root cause. |
| MAG-003 | **Confidence Decay Propagation** | `NEXT` | When Agent A produces low-confidence output and passes it to Agent B, Agent B's confidence is automatically discounted. Confidence decay propagates upstream through the causal DAG — crew-level confidence reflects the weakest link, not the average. |
| MAG-004 | **Race Condition Detector** | `LATER` | Async agent pipelines suffer race conditions: Agent A and Agent B both read-then-write the same resource, producing inconsistent state. AgentWatch detects these patterns in the trace timeline before they corrupt production data silently. |
| MAG-005 | **Semantic Conflict Detector** | `LATER` | When 5 agents work in parallel, they may reach contradictory conclusions. Detects semantic conflicts between agent outputs before they're merged — surfaces "Agent 2 says delete, Agent 4 says archive" as a blocking conflict requiring resolution. |
| MAG-006 | **Cascade Failure Isolation** | `LATER` | When one agent in a crew fails, the failure should not cascade. Circuit breaker pattern: detect failure propagation, isolate the failing agent, route around it if possible, alert if not. Prevents 1-agent failure = 5-agent crash. |
| MAG-007 | **Causal Attribution (Shapley)** ⭐ | `ADVANCED` | Nobody has this. Given a crew-level failure, produce a ranked list of which agent decision contributed most — with percentage attribution. "Agent 2's hallucinated file path was 73% responsible for the final failure." Uses Shapley value attribution on the causal DAG. |
| MAG-008 | **Adaptive Agent Routing** | `ADVANCED` | Based on real confidence data from production, automatically suggest re-routing certain task types to different agents. "Agent 3 consistently underperforms on SQL tasks — route those to Agent 1." Data-driven crew optimization. |

---

## 06 · Cost Intelligence
> Agentic workloads can hit $0.15/execution × 500k requests/day = $75k/day. Cost tracking is a survival feature.

| ID | Feature | Priority | What it does |
|----|---------|----------|-------------|
| CST-001 | **Per-Session Token Budget** | `SHIP NOW` | Set a token budget per session. Real-time spend tracking. Alert at 80%, block at 100%. Prevents runaway agents from bankrupting you overnight. |
| CST-002 | **Cost-per-Reasoning-Step Attribution** | `NEXT` | Break down cost not just by session but by each reasoning step. Which tool calls are most expensive? Which reasoning chains consume the most tokens? Shows the 20% of steps costing 80% of your budget. |
| CST-003 | **Model Degradation Auto-Router** ⭐ | `NEXT` | Nobody has this. Monitor upstream model health in real time. When Claude or GPT degrades (as happened April 2026), automatically route tasks to a healthy alternative without context loss. Configurable priority order. |
| CST-004 | **Intelligent Model Downgrade** | `LATER` | For simple sub-tasks, automatically route to cheaper models (Haiku, Flash, Gemini Flash). AgentWatch classifies task complexity at runtime and selects the cheapest model that can handle it. Teams report 40–60% cost reduction. |
| CST-005 | **Semantic Caching** | `LATER` | Cache model responses for semantically similar inputs. When the agent asks a question it has effectively asked before, return the cached response. Industry reports 40–95% cache hit rates on repetitive agent workloads. |
| CST-006 | **Cost Anomaly Detection** | `ADVANCED` | Baseline your agent's normal cost profile. Alert when spending spikes anomalously — a cost spike is often the first indicator that an agent has entered an infinite retry loop. Catches runaway agents before the bill arrives. |
| CST-007 | **ROI Tracking Dashboard** | `ADVANCED` | Track ratio of agent cost to business value produced. Shows the "cost per successful outcome" trend over time — the number that justifies the AI budget to finance. |

---

## 07 · Compliance & Governance
> EU AI Act high-risk requirements active August 2026. SOC 2 is now the leading procurement blocker for enterprise AI. This is where AgentWatch becomes worth $50k/year.

| ID | Feature | Priority | What it does |
|----|---------|----------|-------------|
| CMP-001 | **Immutable Audit Log** | `SHIP NOW` | Append-only log of every agent decision, every action taken, every safety check result. Cryptographically signed. Cannot be deleted or modified post-facto. Regulators ask: "Show me why the AI made this decision on this date." This answers it. |
| CMP-002 | **GDPR Export & Right-to-Erasure** | `NEXT` | Export all data related to a specific user on request (Article 15). Delete all traces of a user's interactions on erasure request (Article 17). Configurable retention policies per data type. |
| CMP-003 | **HIPAA Compliance Mode** | `NEXT` | PHI auto-redaction from all logs. BAA-ready infrastructure. Access controls ensuring only authorized personnel view sessions containing health data. Encryption at rest and in transit. Unlocks the healthcare market. |
| CMP-004 | **EU AI Act Article 15 Package** | `LATER` | High-risk AI system requirements active August 2026. Provides: exhaustive technical documentation, decision logs for auditing, accuracy and robustness evidence from guardrail telemetry, conformity assessment support. Fines up to €35M if non-compliant. |
| CMP-005 | **Enterprise RBAC + SAML SSO** | `LATER` | Role-based access: viewer / operator / admin / owner. Per-team agent policies. SAML SSO. Not just username/password. Enterprise RBAC means Active Directory integration and audit logs that satisfy a SOC 2 auditor. |
| CMP-006 | **Compliance Report Generator** | `LATER` | One-click export of compliance evidence packages: SOC 2 audit trail, GDPR processing records, HIPAA access logs, EU AI Act technical documentation. Formatted for each framework's specific requirements. |
| CMP-007 | **Data Residency Controls** | `ADVANCED` | Configure where data is stored: EU-only, US-only, specific cloud regions. Routes traces and memory to the correct region at runtime. Required for enterprise customers with data sovereignty obligations. |
| CMP-008 | **Causal Compliance Attribution** ⭐ | `ADVANCED` | Nobody has this. Given an adverse outcome, produce a machine-readable compliance report showing: which policy was active, which agent action violated it, what the causal chain was, what the remediation was. Satisfies regulators. |
| CMP-009 | **ISO 42001 AI Management System** | `FINAL` | ISO 42001 is the emerging AI-specific management system standard. AgentWatch provides the technical infrastructure for certification: AI risk assessments, documented governance, incident tracking, continuous monitoring evidence. Closes deals 40% faster per BCG research. |

---

## 08 · Platform & Integrations
> Distribution is everything. Every framework adapter is a growth channel. Every integration reduces churn.

| ID | Feature | Priority | What it does |
|----|---------|----------|-------------|
| PLT-001 | **Framework Adapters (Tier 1)** | `SHIP NOW` | Claude Code, LangChain, AutoGPT, CrewAI (done). Next: LangGraph, AutoGen, Smolagents (Hugging Face), BabyAGI. Zero-rewrite integration. Each adapter is a distribution channel into that framework's entire user base. |
| PLT-002 | **REST API + WebSocket API** | `SHIP NOW` | Full REST API for all operations (done). WebSocket API for real-time streaming (done). SDK wrappers in Python and TypeScript. API-first design lets any tool use AgentWatch as a backend observability service. |
| PLT-003 | **Alerting Hub** | `NEXT` | Unified alerting: Slack, PagerDuty, email, webhook. Configurable conditions: confidence below X, safety block fired, cost spike, loop detected, model degradation. Alert contains full context — not just "something failed." |
| PLT-004 | **One-Click Cloud Deploy** | `NEXT` | Render + Railway (done). Add: Fly.io, AWS CDK, GCP Cloud Run, Azure Container Apps. Enterprise on-premise: Helm chart for Kubernetes. "3 minutes from clone to production." |
| PLT-005 | **Prompt Version Management** | `LATER` | Version-control system prompts inside AgentWatch. A/B test different prompt versions against real production traffic. Roll back instantly when a new prompt version causes confidence to drop. |
| PLT-006 | **Evaluation Dataset Builder** | `LATER` | Convert production traces into evaluation datasets with one click. When a session is flagged as a failure or a golden example, add it to your eval set. Closes the loop from production monitoring to pre-deployment testing. |
| PLT-007 | **AgentWatch Cloud (Managed SaaS)** | `ADVANCED` | Fully managed SaaS alongside self-hosted. $50/mo developer, $500/mo team, custom enterprise. Turns OSS contributors into paying customers without forcing them to manage Postgres themselves. |
| PLT-008 | **Plugin Marketplace** | `ADVANCED` | Third-party auditor models, custom safety rule packs, compliance templates, industry-specific policy libraries. Contributors publish plugins, AgentWatch takes revenue share. The Shopify App Store model for AI observability. |
| PLT-009 | **AgentWatch Intelligence (AI-on-AI)** | `FINAL` | An AI assistant trained on your AgentWatch telemetry. Surfaces patterns you'd never notice: "your agent always fails on Tuesday mornings — that's when your database has scheduled maintenance." Proactively suggests fixes and prompt optimizations. |

---

## 09 · Protocol Play
> The scope upgrade that turns a tool into a company. OpenTelemetry didn't win by being the best APM. It won by becoming the standard.

| ID | Feature | Priority | What it does |
|----|---------|----------|-------------|
| PRT-001 | **Open Reasoning Trace Schema** ⭐ | `LATER` | Publish an open specification for how agent reasoning steps should be logged: `ReasoningTrace v1.0`. Define the standard before anyone else. If LangGraph, AutoGen, and CrewAI adopt it, every tool in the ecosystem feeds into AgentWatch. The OTEL play. |
| PRT-002 | **AgentWatch-Compatible Badge Program** | `ADVANCED` | "AgentWatch-compatible" becomes a badge that agent frameworks adopt. Frameworks that implement the ReasoningTrace schema get listed in the AgentWatch registry. Network effects: more frameworks → more users → more data → better intelligence. |
| PRT-003 | **Anonymized Failure Benchmark** | `ADVANCED` | Opt-in: anonymized failure patterns from all AgentWatch deployments, aggregated into a public benchmark of "how often agents fail at X." More users = better benchmark = more users. Data flywheel that makes AgentWatch defensible at scale. |
| PRT-004 | **MCP Server Integration** | `FINAL` | Expose AgentWatch as an MCP server. Any Claude-based agent can query its own observability data, check its confidence history, retrieve its causal memory — all via standard MCP tool calls. AgentWatch becomes the memory and observability layer that Claude agents natively use. |

---

## Competitive Matrix

| Feature | AgentWatch | Langfuse | Arize Phoenix | Galileo | Datadog LLM |
|---------|-----------|---------|--------------|---------|------------|
| Span-level trace collection | ✅ done | ✅ | ✅ | ✅ | ✅ |
| Pre-execution safety blocking | ✅ **unique** | ❌ | ❌ | ❌ | ❌ |
| Independent reasoning auditor | ✅ **unique** | ❌ | ❌ | ⚠️ partial | ❌ |
| Git-backed rollback | ✅ **unique** | ❌ | ❌ | ❌ | ❌ |
| Session replay / time-travel | ✅ | ❌ | ✅ | ❌ | ⚠️ partial |
| Causal memory graph | 🔨 building | ❌ | ❌ | ❌ | ❌ |
| Inter-agent causal DAG | 🔨 building | ❌ | ⚠️ basic | ❌ | ❌ |
| Embedding drift detection | 📋 planned | ❌ | ✅ | ❌ | ❌ |
| OWASP Agentic Top 10 | ⚠️ partial | ❌ | ❌ | ❌ | ❌ |
| Counterfactual replay | 📋 planned | ❌ | ❌ | ❌ | ❌ |
| Model degradation auto-routing | 📋 planned | ❌ | ❌ | ❌ | ❌ |
| Open reasoning trace schema | 📋 planned | ❌ | ❌ | ❌ | ❌ |
| HIPAA / GDPR compliance | ⚠️ partial | ✅ | ✅ | ✅ | ✅ |
| Production scale (battle-tested) | ❌ gap | ✅ | ✅ | ✅ | ✅ |
| MCP server integration | 📋 planned | ❌ | ❌ | ❌ | ❌ |

---

## Summary

| Domain | Features | Unique to AgentWatch |
|--------|---------|---------------------|
| Core Observability | 10 | Counterfactual replay |
| Reasoning Auditor | 10 | Auditor itself, fingerprinting, adversarial resistance, semantic drift |
| Persistent Memory | 9 | Causal graph, cross-session identity, NL memory query |
| Safety Engine | 10 | Blast radius estimator, policy DSL, OWASP full coverage |
| Multi-Agent | 8 | Inter-agent DAG, Shapley attribution |
| Cost Intelligence | 7 | Model degradation auto-router |
| Compliance | 9 | Causal compliance attribution |
| Platform | 9 | AI-on-AI intelligence |
| Protocol | 4 | Open schema, MCP integration |
| **Total** | **87** | **14** |

---

*⭐ = unique to AgentWatch · 🔨 = in progress · 📋 = planned · Built by sreerevanth · Issues → open one*
