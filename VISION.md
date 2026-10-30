# What is AgentWatch?

AgentWatch is the control plane for safe, reliable AI agent execution.

It began as an AI agent observability project, but the repository now shows a broader identity: a hybrid SDK, runtime guardrail, telemetry system, governance layer, and developer platform for agents that use tools, memory, multiple models, multiple frameworks, and long-running workflows.

The strongest product evidence is:

- `panoptis.watch()` instruments existing agents and framework objects without requiring users to rewrite their agent code.
- The core event schema normalizes agent lifecycle, tool calls, memory, safety, confidence, rollback, and multi-agent events.
- The `SafetyEngine` evaluates tool calls before execution, attaches reasoning confidence, estimates blast radius, enforces policies, and can block or require approval.
- The FastAPI server exposes sessions, events, replay, rollback, safety policy, safety checks, dashboard summaries, compliance reports, entitlement usage, tenants, metrics, and WebSocket streaming.
- The CLI exposes watch, replay, session inspection, export, scoring, rollback, pruning, safety checks, policy inspection, cost reports, evaluation, server controls, comparison, red-team checks, upgrade/licensing, MCP, and diagnostics.
- The dashboard presents live events, sessions, safety blocks, confidence trends, replay, rollback, policy editing surfaces, memory, multi-agent, security, benchmark, compliance, cost, sandbox, and safety lab views.
- The repository contains importable modules for observability, reasoning audit, safety, memory, multi-agent orchestration, cost governance, compliance, security, rollback, replay, protocol, plugins, cloud tenancy, and deployment.

AgentWatch is therefore not only an observability layer. It is an execution governance platform for autonomous and semi-autonomous AI systems.

# Why AgentWatch Exists

AI agents fail differently from normal software.

Traditional applications usually fail through errors, crashes, bad latency, or bad status codes. Agents can fail while looking successful: they can hallucinate tool arguments, drift away from the original goal, call the wrong system, leak sensitive data, loop until budget is exhausted, create inconsistent multi-agent state, or perform irreversible actions before anyone notices.

Existing observability tools are strongest after execution. AgentWatch is designed around the earlier moment: before an action reaches the outside world.

AgentWatch exists to make agent behavior inspectable, governable, reversible where possible, and preventable where necessary.

# The Problem

The single problem AgentWatch solves:

> Teams cannot safely operate AI agents in production unless they can observe, evaluate, constrain, and intervene in agent actions before those actions cause damage.

Who experiences this:

- AI application developers adding tool use to LLM workflows.
- Platform teams standardizing agent infrastructure across frameworks.
- Security teams worried about command execution, exfiltration, prompt injection, and unsafe tools.
- Compliance and governance teams that need audit trails, retention controls, redaction, and explainable decision records.
- Engineering managers responsible for reliability, cost, and incident response.
- Operators supervising long-running or multi-agent workflows.

Why existing tools fail:

- Post-hoc tracing explains what happened after the action has already run.
- Generic APM does not model reasoning steps, tool intent, goal drift, hallucinated arguments, or agent memory.
- Framework-specific monitoring fragments across LangChain, CrewAI, AutoGen, LangGraph, Claude Code, OpenAI Agents, AutoGPT, Smolagents, OpenClaw, and custom agents.
- Simple guardrails usually sit at prompt or response boundaries, not around every tool action.
- Cost dashboards rarely understand agent loops, task complexity, model failover, or per-step attribution.
- Compliance systems usually require humans to reconstruct agent decisions from scattered logs.

What makes AgentWatch different:

- It treats each agent action as a governed event, not just a log line.
- It combines telemetry, safety, reasoning audit, policy, cost, memory, replay, and governance around one normalized event model.
- It provides both developer-local workflows and platform/API/dashboard workflows.
- It has an explicit pre-execution enforcement path, including block, approval, and rollback-oriented workflows.

# Our Mission

Make AI agents safe enough to operate in real production environments by giving teams a unified control plane for observing, auditing, constraining, replaying, and governing agent execution.

# Our Vision

AgentWatch should become the default safety and reliability layer between AI agents and the systems they affect.

In five years, a production agent stack should not connect directly to tools, APIs, databases, file systems, or other agents without a control plane that can answer:

- What is the agent trying to do?
- Why is it trying to do it?
- Is the action safe?
- Is the reasoning trustworthy enough?
- What is the blast radius?
- Does policy allow it?
- Should a human approve it?
- Can the action be replayed or rolled back?
- What did this cost?
- What evidence is available for audit and compliance?

AgentWatch should be that control plane.

# Core Product Pillars

Every future feature should strengthen at least one of these pillars.

## 1. Pre-Execution Safety

AgentWatch must prevent high-risk agent actions before they execute. This includes destructive command detection, risky database operations, remote code execution patterns, exfiltration attempts, prompt injection, sandbox simulation, blast-radius estimation, human approval gates, and policy enforcement.

## 2. Unified Agent Telemetry

AgentWatch must capture agent behavior as normalized, typed events across frameworks. Sessions, traces, spans, tool calls, reasoning artifacts, memory events, cost metadata, confidence scores, and task graph events should share one coherent model.

## 3. Reasoning Trust

AgentWatch must evaluate whether an agent's reasoning and intended action remain coherent, grounded, goal-aligned, and resistant to drift or hallucination. The independent auditor, confidence scoring, semantic drift, hallucination checks, fingerprinting, dual evaluation, and benchmark harness all belong here.

## 4. Operational Control

AgentWatch must help operators intervene in running or completed sessions. Replay, counterfactual simulation, rollback, checkpoints, alerts, live dashboards, CLI inspection, session sharing, and server controls belong here.

## 5. Multi-Agent and Memory Governance

AgentWatch must model state that crosses a single call: causal memory, temporal decay, memory health, identity, natural-language memory query, inter-agent DAGs, crew context, propagation, deadlock, consensus, spawning, and attribution.

## 6. Cost and Model Governance

AgentWatch must make agent spend predictable and controllable. Token budgets, per-session tracking, cost reporting, model comparison, degradation routing, complexity-based routing, anomaly detection, ROI, and semantic caching belong here.

## 7. Compliance and Trust Evidence

AgentWatch must produce evidence for regulated and enterprise adoption. Audit logs, RBAC, tenant isolation, redaction, GDPR, HIPAA, EU AI Act, ISO 42001, data residency, security reports, entitlement checks, and signed or immutable records belong here.

## 8. Open Integration Surface

AgentWatch must be easy to adopt and extend. Framework adapters, REST APIs, WebSockets, CLI, MCP server, Open Reasoning Trace schema, badge checks, plugins, deployment templates, and OpenTelemetry export belong here.

# Product Identity

AgentWatch is a hybrid product.

It is a library because the package exposes `watch()`, adapters, event schemas, safety engines, memory engines, reasoning modules, and utility APIs directly to Python users.

It is an SDK because developers integrate it into LangChain, CrewAI, AutoGPT, Claude Code, LangGraph, AutoGen, Smolagents, OpenAI Agents, OpenClaw, or custom agents.

It is a runtime guardrail because it intercepts tool-like calls, evaluates safety before execution, and can raise `AgentWatchBlockedError`.

It is a control plane because the server, dashboard, CLI, WebSocket stream, tenants, policies, compliance reports, and operational commands create a central operating surface for many agent sessions.

It is an observability layer because it captures traces, spans, sessions, events, dashboards, metrics, OTEL-compatible telemetry, replay, and live streams.

It is a security and governance layer because it implements risk scoring, policy enforcement, redaction, OWASP checks, sandboxing, audit logs, RBAC, entitlement controls, and compliance reports.

It is not yet a fully integrated platform in every area. Several advanced modules are implemented and tested as Python components but are not consistently exposed through mounted API routes or complete dashboard workflows.

The objective identity is:

> AgentWatch is a hybrid AI agent execution control plane: part SDK, part runtime guardrail, part observability platform, part governance and security layer.

# System Architecture

The system is organized around a normalized event pipeline.

## Instrumentation Layer

The `watch()` API and adapters instrument framework-specific agents and translate actions into `AgentEvent` records. Evidence includes adapters for Claude Code, LangChain, CrewAI, AutoGPT, OpenAI Agents, OpenClaw, LangGraph, AutoGen, and Smolagents, plus the generic wrapper.

## Event Model and Bus

The core schema defines event types for lifecycle, planning, tool execution, memory, safety, multi-agent messages, rollback, confidence, and custom events. The event bus routes these events to collectors, live streams, forwarders, and other subscribers.

## Runtime Enforcement

The safety engine evaluates tool calls with pattern-based risk scoring, intent normalizers, reasoning audit, blast-radius estimation, policy DSL evaluation, static policy fallback, and optional approval callbacks. The sync path blocks approval-required actions fail-safe.

## Persistence and API

The backend uses FastAPI, SQLAlchemy async, PostgreSQL, Redis, and Celery. The API stores sessions/events, streams WebSockets, exposes Prometheus metrics, supports pruning, handles rollback, and reports governance/compliance outputs.

## Operator Surfaces

The CLI and dashboard provide developer and operator workflows: watch, replay, list, export, score, safety check, rollback, cost report, eval, red-team, server status, live event feed, session detail, replay studio, safety lab, sandbox, and domain pages.

## Advanced Capability Modules

Separate packages cover reasoning, memory, orchestration, cost, governance, security, platform, protocol, plugins, replay, rollback, monitoring, telemetry, tracing, validation, and infrastructure routing.

## Deployment

The repo includes Docker Compose, Dockerfiles, Render/Railway/Fly configuration, a Helm chart, Grafana dashboards, CI workflows, load tests, and benchmark scripts.

# Guiding Principles

## Product Principles

- Prevent harm before explaining it.
- Treat actions, not responses, as the primary unit of risk.
- Make every agent action attributable to a session, actor, policy, cost, and confidence state.
- Work across frameworks instead of forcing one orchestration stack.
- Keep local developer adoption simple while supporting enterprise controls.
- Prefer one coherent control plane over scattered point tools.

## Design Philosophy

- Operator views must answer what happened, what is happening, why it matters, and what can be done next.
- Safety UI should expose decisions, evidence, matched policies, and blast-radius reasoning.
- Advanced capability pages must not exist as empty product promises; if a page ships, the backend route and data contract should ship with it.
- The dashboard should be an operational cockpit, not a marketing site.

## Engineering Philosophy

- The normalized event schema is the foundation; new features should enrich or consume it, not bypass it.
- Guardrails should fail closed when security or approval state is uncertain.
- Runtime instrumentation should avoid crashing host agents except when intentionally blocking a dangerous action.
- Modules should become end-to-end workflows before new domains are added.
- Tests should validate behavior across the full chain: adapter to event, safety decision, persistence, API, dashboard/CLI.
- Security-sensitive shell execution must stay routed through the central CLI utility.

## User Philosophy

- Developers should add AgentWatch without rewriting their agent.
- Operators should understand and act without reading raw logs.
- Security and compliance teams should get evidence, not anecdotes.
- Teams should be able to self-host and keep sensitive traces inside their environment.

# Scope

## What Belongs Inside AgentWatch

- Agent instrumentation and framework adapters.
- Normalized event schemas and trace collection.
- Pre-execution risk scoring, blocking, approval, and policy enforcement.
- Reasoning confidence, hallucination, drift, quality, benchmark, and trust evaluation.
- Replay, counterfactual simulation, checkpointing, rollback, and incident review.
- Causal memory and memory governance when tied to agent reliability, auditability, or safety.
- Multi-agent tracing, propagation, consensus, deadlock, task graph, and attribution when tied to execution reliability.
- Cost tracking, budget enforcement, model routing, semantic caching, anomaly detection, and ROI reporting.
- Compliance, audit, redaction, RBAC, tenancy, data residency, entitlement, and security reporting.
- Dashboard, CLI, REST API, WebSocket, MCP, plugin, OTEL, Grafana, and deployment integrations.
- Benchmarks and tests that measure safety, reasoning, overhead, and resilience.

## What Should Never Belong Inside AgentWatch

- A general-purpose agent framework that competes with LangChain, CrewAI, AutoGen, or LangGraph.
- A general chat application or end-user assistant unrelated to agent execution control.
- A generic APM replacement for non-agent services.
- A standalone vector database, model provider, or LLM gateway unless directly required for safety, routing, or cost governance.
- Prompt libraries or business workflow templates that are not tied to governance, evaluation, or observability.
- Marketing-only dashboard pages without backend routes and working data contracts.
- Compliance claims that cannot be generated from real telemetry, configuration, and audit evidence.
- Autonomous remediation that mutates production systems without policy, auditability, approval controls, and rollback planning.

# Non Goals

- AgentWatch should not become the agent orchestration engine.
- AgentWatch should not hide unsafe behavior behind prettier observability.
- AgentWatch should not require one vendor's model, cloud, or framework.
- AgentWatch should not collect private chain-of-thought unless explicitly provided and permitted by the host system.
- AgentWatch should not claim production readiness for modules that are test-only, simulated, or not wired to an operational surface.
- AgentWatch should not prioritize breadth of feature count over end-to-end reliability.

# Repository Assessment

## Feature Inventory

| Capability | Repository Evidence | Purpose | Beneficiary | Layer | Completeness | Overlap / Belongs |
|---|---|---|---|---|---|---|
| Universal `watch()` SDK | `panoptis/core/watcher.py`, root exports | Attach observability and safety to existing agents | Developers | SDK/runtime | Strong for generic and several framework paths | Belongs; core adoption surface |
| Framework adapters | `panoptis/adapters/*` | Normalize framework-specific events | Developers/platform teams | SDK/integration | Broad coverage; depth varies by framework | Belongs; should be tested against real framework versions |
| Event schema | `panoptis/core/schema.py` | Common representation for sessions/actions/safety/memory/cost | All users | Core | Strong | Belongs; should remain canonical |
| Event bus | `panoptis/core/event_bus.py` | Route events to collectors/streams/forwarders | Platform/operators | Core/runtime | Strong | Belongs |
| HTTP forwarding | `panoptis/core/http_forwarder.py` | Send local events to API | Developers/platform | Integration | Present | Belongs |
| Trace collection/spans | `panoptis/tracing`, `panoptis/telemetry` | Capture spans, trajectories, sampling, OTEL export, live streams | Operators/SRE | Observability | Implemented modules; API integration partial | Belongs |
| API server | `panoptis/api/server.py` | Central REST/WebSocket/metrics surface | Dashboard, CLI, integrations | Platform | Strong for sessions/safety/replay/tenant basics | Belongs; route modularization needed |
| Dashboard | `frontend/pages/*` | Operator UI for sessions, live feed, safety, replay, domain pages | Operators | Product UI | Core dashboard works; many domain pages hit missing routes | Belongs; incomplete chains need priority |
| CLI | `panoptis/cli/main.py` | Local developer/operator control | Developers/operators | DX/control | Broad and useful | Belongs; should keep secure command utility rule |
| Safety engine | `panoptis/core/safety.py`, risk/injection/blast policy modules | Block/approve risky actions before execution | Security/operators | Runtime enforcement | Strong core | Belongs; central pillar |
| Policy DSL | `panoptis/core/policy_dsl.py`, `policy_loader.py`, CLI/page surfaces | Human-readable runtime policy | Security/platform | Governance | Implemented; frontend endpoints drift from backend | Belongs; needs single API contract |
| Human approval | Safety policy and approval callback | Pause risky actions for review | Operators/security | Runtime control | CLI callback exists; full workflow partial | Belongs |
| Sandbox/safety lab | `panoptis/security/sandbox.py`, frontend sandbox/safety-lab | Simulate command risk and threat path | Developers/security | Security/DX | Safety lab wired to `/safety/check`; sandbox page calls missing `/security/sandbox/simulate` | Belongs; consolidate |
| OWASP checks | `panoptis/security/owasp.py`, tests, security page | Detect agentic security vectors | Security | Security/compliance | Module/test present; page calls missing route | Belongs; route needed |
| Red-team harness | `panoptis/security/redteam.py`, payload corpus, CLI, Celery task | Test guardrail resilience without executing attacks | Security | Security/eval | Implemented harness | Belongs |
| Redaction | `panoptis/security/redaction.py`, GDPR/HIPAA patterns | Scrub PII/PHI from telemetry | Compliance/security | Governance | Implemented, opt-in | Belongs |
| Reasoning auditor | `panoptis/reasoning/auditor.py` and related modules | Score trust, hallucination, drift, quality, calibration | Developers/operators | Reasoning trust | Broad module coverage; benchmark semantics depend on judge | Belongs |
| Confidence scoring | `panoptis/scoring/confidence.py`, API confidence endpoint | Summarize session trust/anomalies | Operators | Observability/reasoning | Present | Belongs |
| Benchmarks/eval | `benchmarks`, `panoptis/eval`, CLI eval | Test reasoning and safety behavior | Developers/platform | Evaluation | Harness present; saved mock result should not be overclaimed | Belongs |
| Replay | `panoptis/replay`, API, dashboard | Time-travel session review | Operators | Operational control | Strong | Belongs |
| Counterfactual simulation | `panoptis/replay/counterfactual.py`, API simulate | Ask what changes if a step differs | Developers/operators | Operational control | Implemented with user-supplied step behavior; not full autonomous replay | Belongs |
| Rollback | `panoptis/rollback`, CLI/API/session UI | Restore filesystem/git checkpoints | Operators | Operational control | Present; high-risk area | Belongs with strict safety/audit |
| Memory engine | `panoptis/memory/*` | Store/query/govern causal and temporal memory | Developers/operators | Memory governance | Implemented modules; dashboard routes missing | Belongs if tied to reliability/governance |
| Multi-agent orchestration analysis | `panoptis/orchestration/*` | Model DAGs, deadlocks, trust, propagation, consensus | Multi-agent builders | Multi-agent governance | Implemented modules; dashboard routes missing | Belongs |
| Cost intelligence | `panoptis/cost/*`, CLI cost | Track budgets, compare models, route, predict, cache, detect anomalies | Engineering/finance | Cost governance | Modules and CLI present; dashboard route missing | Belongs |
| Compliance/governance | `panoptis/governance/*`, API reports | Produce audit/compliance evidence | Compliance/security | Governance | Modules plus some mounted reports | Belongs; claims must be evidence-backed |
| RBAC/auth/tenant | `panoptis/api/auth.py`, `tenant_auth.py`, models | Auth, permissions, tenant isolation | Enterprise/platform | Platform/security | Present; dashboard auth forwarding mismatch | Belongs |
| Entitlements/licensing | `security/license.py`, `checkout.py`, entitlement API, CLI upgrade | Premium feature gating and abuse detection | Maintainers/cloud users | Commercial platform | Present | Belongs if product includes cloud/commercial path |
| Plugins | `panoptis/plugins/*`, manifest schema | Extend safely with permissions | Ecosystem | Platform | Registry/sandbox present | Belongs but should remain constrained |
| Protocol/MCP/badge | `panoptis/protocol/*`, CLI MCP | Open trace schema and interoperability | Ecosystem/frameworks | Protocol | Present | Belongs; strategic differentiator |
| Monitoring/metrics | `panoptis/monitoring`, `/metrics`, Grafana JSON | Operate AgentWatch itself | SRE/platform | Ops | Present | Belongs |
| Deployment | Docker, Helm, Render, Railway, Fly | Self-host and deploy | Platform/devops | Infra | Present but inconsistent completeness | Belongs; harden |
| Landing site | `agentwatch-landing` | Public positioning, docs/blog/contributors | Community/users | Marketing/docs | Separate app; contains unverified claims | Belongs as website, not source of product truth |

## Architecture Drift and Technical Debt

- Package/runtime versioning is now aligned at `0.2.0`, but generated examples, Helm metadata, docs, and landing-page release claims still need a single release authority.
- Python support is now documented as 3.12+ in package and primary setup docs; secondary docs and examples should continue to be checked during release work.
- `MASTERLIST_STATUS.md` explicitly says some new modules do not have wired API routes and frontend pages render empty states when endpoints are missing.
- Frontend hooks call routes not mounted in `panoptis/api/server.py`, including `/memory/graph`, `/memory/query`, `/orchestration/dag`, `/orchestration/deadlock`, `/security/owasp`, `/security/sandbox/simulate`, `/cost/summary`, `/governance/compliance/status`, `/reasoning/benchmark/latest`, `/reasoning/benchmark/run`, `/policies/current`, and `/policies/preview`.
- Backend exposes `/api/v1/safety/policy`, while frontend policy hooks use `/policies/current`.
- Backend `/api/v1/safety/blocked` returns an object containing `blocked_events`, while the dashboard hook types it as an array.
- Backend `/api/v1/sessions` returns `{ sessions, total }`, while the dashboard hook types it as `AgentSession[]`.
- Backend `/api/v1/sessions/{id}/checkpoints` response shape should be verified against the frontend's direct array expectation.
- Frontend API proxy forwards `Authorization` but not `X-Api-Key`, while protected backend endpoints rely on `X-Api-Key`.
- Docker Compose sets `AGENTWATCH_ENV=production` for the API but does not set `AGENTWATCH_API_KEY`, which conflicts with the backend fail-closed production model.
- Helm chart covers the API deployment/service but not a complete platform release including frontend, PostgreSQL templates, Redis templates, workers, ingress, secrets, and auth values.
- Benchmark README warns the default heuristic auditor is not semantic; saved results use a mock judge. Performance and accuracy claims should distinguish harness capability from real auditor accuracy.
- Landing blog/source claims include broad market and performance assertions that are not clearly tied to repository evidence.
- Stale setup references were corrected in the main developer setup and CLI getting-started docs; remaining docs should be periodically checked against package metadata.
- The API server is large and centralizes many concerns in one module, increasing coupling across auth, storage, telemetry, safety, rollback, governance, tenants, metrics, and WebSockets.
- There are duplicate or overlapping concepts: governance RBAC vs API auth, memory decay vs temporal decay, telemetry collector vs tracing collector, sandbox page vs safety lab, policy DSL routes vs safety policy routes, platform tenant store vs API tenant store.
- `__main__.py` at the repository root is empty.
- The landing app has its own Next.js 16 rules and separate dependency set; it must remain isolated from the dashboard.

# Current Strengths

- Clear differentiated core: pre-execution safety plus reasoning-aware agent telemetry.
- Strong normalized event model that can support observability, governance, memory, cost, and multi-agent use cases.
- Broad framework adapter surface and a one-line adoption story.
- Useful developer CLI with real operational workflows.
- FastAPI backend already exposes the core session, event, replay, rollback, safety, dashboard, compliance, tenant, metrics, and WebSocket surfaces.
- Safety engine includes practical bypass-resistant command intent normalizers.
- Replay and rollback give AgentWatch an operational control story beyond passive logging.
- Rich module inventory creates a credible roadmap for a wider control plane.
- Self-hosting and deployment assets exist across Docker, Render, Railway, Fly, Helm, Grafana, and CI.
- Test suite is broad and includes security, API, CLI, safety, reasoning, memory, cost, governance, protocol, and adapter coverage.

# Current Weaknesses

- Product scope has expanded faster than end-to-end integration.
- Several dashboard pages are ahead of mounted backend routes.
- Versioning and public claims are inconsistent.
- Some advanced features are implemented as isolated modules rather than coherent API/dashboard workflows.
- The API server has become a large coupling point.
- Production auth defaults and compose configuration conflict.
- Compliance and benchmark claims need stricter evidence discipline.
- Documentation quality is uneven and includes stale setup instructions.
- The product has both open-source/self-hosted and commercial/entitlement directions, but the boundary is not yet clearly documented.

# Future Direction

## Immediate Priorities

1. Reconcile product identity and versioning across `pyproject.toml`, package `__version__`, README, Helm, docs, and landing site.
2. Fix the dashboard/API contract mismatches for sessions, blocked events, policies, memory, multi-agent, security, cost, compliance, and benchmark surfaces.
3. Decide which advanced pages should ship now, which should be hidden, and which need backend routes first.
4. Fix production auth/deployment defaults, including Docker Compose `AGENTWATCH_API_KEY` handling and dashboard proxy key forwarding.
5. Split or modularize the API server by capability while preserving the public API.
6. Add end-to-end tests for the most important workflows: watch to event to safety to persistence to dashboard/CLI.
7. Mark benchmark modes clearly: heuristic, mock judge, external LLM judge, and production calibration.
8. Update stale docs and remove conflicting installation/version claims.

## Medium-Term Priorities

1. Turn the strongest module families into complete product workflows:
   - Safety and policy center.
   - Replay and rollback studio.
   - Reasoning trust dashboard.
   - Memory governance dashboard.
   - Multi-agent causal DAG.
   - Cost governance and routing.
   - Compliance evidence center.
2. Define stable API contracts for each product pillar.
3. Harden tenant isolation, RBAC, API key propagation, audit logging, and data residency.
4. Expand real framework integration tests against supported versions.
5. Publish the Open Reasoning Trace schema as a formal spec with validation, examples, and compatibility tests.
6. Make plugin permissions and sandbox enforcement production-grade before inviting third-party plugins.
7. Build a measured overhead benchmark and publish latency/cost/accuracy results with reproducible methodology.

## Long-Term Vision

Over the next 3-5 years, AgentWatch should become:

- The standard control plane for tool-using AI agents.
- The open trace schema for agent reasoning, tool execution, safety decisions, and governance evidence.
- The self-hosted enterprise platform for agent observability, safety, compliance, and cost governance.
- The interoperability layer across agent frameworks.
- The operational evidence system for regulated agent deployments.

## Future Ecosystem Opportunities

- AgentWatch-compatible badge for frameworks and tools.
- Open Reasoning Trace adoption across agent frameworks.
- Managed AgentWatch Cloud for teams that do not want to operate Postgres/Redis/API/dashboard.
- Policy packs for industries such as finance, healthcare, legal, and infrastructure.
- Plugin marketplace for auditor models, security rules, exporters, compliance templates, and custom dashboards.
- Anonymous failure benchmark, opt-in only, built from scrubbed and governed traces.
- MCP-based agent self-inspection tools.

# Success Metrics

AgentWatch should measure success by operational outcomes, not feature count.

- Percentage of dangerous actions blocked before execution.
- False positive and false negative rates for safety decisions.
- Median and p99 overhead added by instrumentation and safety checks.
- Time from integration to first visible session.
- Percentage of supported dashboard pages backed by real API routes and tests.
- Number of frameworks with verified adapter tests.
- Mean time to diagnose a failed agent session using replay.
- Cost reduction from budget enforcement, model routing, and caching.
- Compliance evidence completeness for audit requests.
- Tenant isolation and auth test coverage.
- Production deployments with configured API keys, retention, redaction, and alerting.
- User retention for CLI, dashboard, and API workflows.

# Final Statement

AgentWatch should not be guided by the old assumption that it is merely AI agent observability.

The repository shows a more ambitious and more valuable product: an AI agent execution control plane that observes, audits, constrains, replays, governs, and helps reverse agent behavior across frameworks.

The path forward is not to add more disconnected features. The path forward is to make the existing pillars coherent, end-to-end, evidence-backed, and hard to misuse.

AgentWatch wins if teams trust it enough to put it between their agents and production.
