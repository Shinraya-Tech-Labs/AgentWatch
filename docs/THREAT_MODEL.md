# Threat model and security boundaries

This page covers what AgentWatch's runtime protection defends against, where its boundary is, what it assumes, and what it does not do. It complements:

- [ADAPTER_SUPPORT.md](ADAPTER_SUPPORT.md): what each integration enforces;
- [CLAIMS.md](CLAIMS.md): the evidence for each claim.

## What is protected

An AI agent proposes **tool calls** (shell commands, file and HTTP operations, SQL, MCP tools, framework tools), and the results flow back into its context. AgentWatch sits **between the agent and its tools**:

1. **Before** a call runs, it decides: allow, block, escalate to a human, or rewrite the arguments. The decision draws on:
   - identity;
   - the emergency stop and suspensions;
   - threat analysis of the arguments;
   - RBAC;
   - loop detection;
   - intent grounding.
2. **After** the call returns, it inspects the result before the agent sees it: prompt-injection quarantine, secret redaction, size limits.
3. It **records** every decision, approval, execution and inspection in an append-only, Merkle-sealed evidence store that can be verified independently.

## Adversaries considered

| Adversary | Example | Defence |
|---|---|---|
| a hijacked or confused agent (prompt injection, goal drift) | the agent is told by a web page to delete files or send data | threat analysis, intent grounding against the user's objective, RBAC, human approval |
| malicious tool output (indirect injection) | a document says "ignore previous instructions…" | output inspection quarantines the instruction lines before the agent sees them |
| a careless agent | `rm -rf /`, unbounded `DELETE`, force-push, loops | threat analysis, loop detection, circuit breaker |
| secrets leaking | keys in arguments sent to a network tool, or in results | secret detection: block exfiltration, redact in writes, redact in results and in the evidence |
| another tenant (multi-tenant deployment) | tenant B reads tenant A's decisions or approves its calls | tenant from the authenticated key, never from the request; tenant-scoped records, approvals, suspensions and RBAC bindings |
| a tampering insider (with database access) | edits or deletes an evidence record | Merkle-sealed segments, envelope hashes, a head anchor kept outside the store, and a standalone verifier |
| an operator mistake or incident | an agent goes rogue during an incident | the kill switch and suspensions, checked before every call and again just before execution |

## Boundaries: where protection applies

- **Enforcement is only as complete as the integration.** Only calls that go through an enforcing integration are decided. A tool the agent can reach another way bypasses AgentWatch. Examples of other ways:
  - a CodeAgent's Python interpreter;
  - a framework adapter that only observes;
  - an MCP client configured to start the server directly instead of through the gateway;
  - a Claude Code session without the hook.

  `panoptis protect discover` reports such cases as `UNMONITORED` and never as protected.
- **The Claude Code hook depends on Claude Code.** On Claude Code before 2.1.295, a hook command that fails to start or times out lets the tool run (`onFailure: block` is unavailable there). The real-CLI end-to-end test needs a signed-in CLI and was **not run** in this release (TO-012 is BLOCKED; see IMPLEMENTATION_EVIDENCE.md).
- **Running tools are not interrupted.** The emergency stop prevents new calls and withdraws pending approvals, but a tool already executing finishes.
- **The agent's own process is trusted.** AgentWatch runs in the agent's process (SDK, hook) or next to it (gateway). It is not a sandbox. Code that can modify the agent process can bypass in-process enforcement. Use OS or container isolation for that threat.

## What happens when evaluation fails

- **Fail closed (the default).** If any stage raises, the call is blocked (`ENFORCEMENT_ERROR`) and recorded. If enforcement fails after the tool ran, the result is withheld (`POST_EXECUTION_ERROR`, recorded with `executed: true`).
- **No approval channel.** An escalation with no approval channel is blocked (`NO_APPROVAL_CHANNEL`; configurable), never silently allowed.
- **Approval timeout.** An approval that times out, is rejected, is cancelled, or is invalidated by an emergency stop leaves the tool unrun.
- **The kill-file location cannot be checked.** The call is blocked.
- **Observe mode is opt-in** (`ProtectionConfig(mode="observe")`). It records `would_block` but lets calls run; the kill switch still applies. Records say `mode: observe`.
- **The gateway fails safe.** Malformed, oversized, or timed-out MCP requests are answered with JSON-RPC errors, and a refused call never reaches the server.

## Deployment assumptions

- **API keys are secret and scoped.** A tenant key per tenant (`AGENTWATCH_TENANT_KEYS`) and an operator key (`AGENTWATCH_API_KEY`, or `AGENTWATCH_ADMIN_KEY` in cloud mode). Without any key configured the API is open (development only). `docker-compose.yml` refuses to start without `AGENTWATCH_API_KEY`, and in production (`AGENTWATCH_ENV=production`) the API refuses requests when no key is configured. `scripts/check_deploy_security.py` checks the Compose exposure: no public data services, no default or short database or Redis passwords, no privileged containers.
- **The evidence store is shared by the agents, the API and the dashboard** (`AGENTWATCH_STORE`) for cross-process approvals and controls. The kill file (`AGENTWATCH_KILL_FILE`) is shared by the processes of one host.
- **The anchor lives outside the store.** The evidence head anchor directory (`AGENTWATCH_EVIDENCE_ANCHORS`) should not be writable by whoever can write the store, or truncation becomes undetectable. Export bundles, or the signed audit tokens, are the stronger anchor.
- **The dashboard holds no credential.** Users sign in with their own key.

## Non-goals

- **Not a sandbox or process isolation.** AgentWatch is not an OS-level policy engine.
- **Not a proof of intent.** Intent grounding is a heuristic signal. Lexical similarity never authorizes a risky call, but a cleverly phrased objective can still mislead it.
- **Not real-world attack coverage.** The pattern rules and the synthetic evaluation show behaviour on known forms. Novel encodings, paraphrased injections and new command forms can evade them.
- **No interruption of running actions, and no undo.** The evidence records what happened; it does not reverse it.
- **No PII detection guarantees.** Secret redaction targets credentials. PII redaction is optional (`presidio`) and not covered by these claims.

## Known limitations

These are tracked in the relevant docs:

- **Pattern-based detection.** Arguments and outputs are analysed with parsers plus patterns; obfuscation beyond the decoded forms (base64, URL encoding, zero-width characters, variable indirection) is not resolved ([THREAT_ANALYSIS.md](THREAT_ANALYSIS.md)).
- **Loop detection judges progress by output hashes.** Timestamped outputs look like progress ([LOOP_DETECTION.md](LOOP_DETECTION.md)).
- **Intent heuristics are English-only** ([INTENT_GROUNDING.md](INTENT_GROUNDING.md)).
- **Rate-limit and loop counters are per process** ([RBAC.md](RBAC.md)).
- **Cross-host stop latency.** The emergency stop reaches other hosts within `refresh_s` ([EMERGENCY_CONTROLS.md](EMERGENCY_CONTROLS.md)).
- **Unsealed records are not yet integrity-protected.** Sealing happens every 200 records or 30 s ([EVIDENCE_INTEGRITY.md](EVIDENCE_INTEGRITY.md)).
- **Discovery approximates which Claude Code settings apply,** and only knows listed runtimes ([DISCOVERY.md](DISCOVERY.md)).
- **The MCP gateway does not inspect `resources/read`.** It also does not support the legacy HTTP+SSE transport ([MCP_GATEWAY.md](MCP_GATEWAY.md)).
