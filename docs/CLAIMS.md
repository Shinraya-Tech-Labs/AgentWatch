# Product claims and their evidence

Every security claim AgentWatch makes, with where it is implemented and how it is verified. A claim that cannot be verified by a test, a benchmark or a reproducible procedure does not belong in the README or docs.

Prevention, detection and observation are different things:

- **Prevented:** the action is stopped before it runs. It applies only through an enforcing integration ([ADAPTER_SUPPORT.md](ADAPTER_SUPPORT.md)).
- **Detected:** a finding is raised and recorded. Through an enforcing integration, a detection also decides the call.
- **Observed or labelled after execution:** recorded for audit, nothing is stopped. Examples are the observation-only adapters and the Claude Code stream adapter.
- **Alerted:** a notification is sent.

| Claim | Kind | Implementation | Verification |
|---|---|---|---|
| A blocked tool call never executes, and the tool runs at most once | prevention | `protect/enforce.py`, `protect/pipeline.py` | `tests/protect/test_enforce.py`; `protect_tool` row of the evaluation (`executed`); `tests/protect/test_single_boundary.py` |
| Every enforcing integration uses the same decision flow | architecture | `protect/enforce.py` | `tests/protect/test_single_boundary.py` (every enforcing support-matrix row); `tests/protect/test_framework_integrations.py` |
| Evaluation failure blocks (fail closed) | prevention | `pipeline.failure_decision` | `tests/protect/test_enforce.py`; `tests/test_watcher_fail_closed.py` |
| Dangerous shell, SQL, path, SSRF, Python and secret-exfiltration arguments are stopped | prevention | `protect/arguments.py`, `protect/threats.py` | `tests/protect/test_threat_analysis.py` (77); [evaluation](https://github.com/Shinraya-Tech-Labs/AgentWatch/blob/main/benchmarks/results/protect_eval/REPORT.md): dangerous-arguments and secrets categories, with false-positive rates |
| Prompt-injection lines in tool results are quarantined and secrets redacted before the agent sees them | prevention | `protect/output.py` | `tests/protect/test_output_inspection.py`; evaluation: prompt-injection category, with sanitization correctness and benign preservation |
| Unauthorized tools and arguments are rejected before execution, per tenant | prevention | `protect/rbac.py` | `tests/protect/test_rbac.py` (51); evaluation: policy category |
| Escalated calls run only after a valid approval for exactly that action | prevention | `protect/approvals.py`, `api/protect_approvals.py` | `tests/protect/test_approvals_lifecycle.py`; browser E2E `tests/e2e_dashboard` (approve in the dashboard, agent runs once) |
| Loops are detected without flagging productive polling | detection, then escalation | `protect/loops.py` | `tests/protect/test_loop_detection.py` (36); evaluation: loops category |
| Destructive actions under read-only objectives are blocked or escalated | prevention | `protect/intent.py` | `tests/protect/test_intent_grounding.py` (49); evaluation: intent category |
| The emergency stop blocks new tool calls across paths and processes | prevention | `protect/control.py` | `tests/protect/test_emergency_controls.py` (real subprocesses); CLI and browser E2E |
| The MCP gateway never forwards a refused `tools/call` and sanitizes results | prevention | `protect/mcp_gateway.py` | `tests/protect/test_mcp_gateway.py`: SDK client → gateway → SDK server over stdio and HTTP; evaluation `mcp_gateway` row |
| The Claude Code hook denies, asks or rewrites before the CLI runs a tool | prevention (hook contract) | `protect/claude_hook.py` | `tests/protect/test_claude_hook.py`; evaluation `claude_code_hook` row. **Not yet verified against the real CLI** (TO-012 BLOCKED: the CLI is not signed in) |
| Discovery never presents an unmonitored runtime as protected | observation | `protect/discovery.py` | `tests/protect/test_discovery.py`; dashboard component test |
| Evidence tampering (records, order) is detected by an independent verifier, and truncation when the verifier holds a head from outside the store. Tamper-evident relative to that anchor, not tamper-proof: whoever controls both store and anchor can rewrite history | detection | `storage/store.py`, `evidence/verifier.py` | `tests/protect/test_evidence_integrity.py` (12 tamperings × 2 verifiers) |
| Tenants never see or act on each other's data | isolation | `api/tenancy.py`, the protect routers | `tests/protect/test_protect_api.py`, `test_rbac.py`, `test_emergency_controls.py`, `test_memory_erasure.py`, `tests/v3/test_tenant_isolation_api.py`, browser E2E |
| Right-to-erasure removes matching sessions, SQL memory rows, in-process memory, cost budgets and checkpoints in the caller's tenant, for the targets named in the receipt. Receipts list what was not reached | data handling | `api/server.py` erasure targets, `memory/governance.py` | `tests/test_memory_erasure.py`, `tests/v3/test_tenant_isolation_api.py` |
| Runtime overhead of an in-process decision with a no-op tool is sub-millisecond without evidence recording, on the measured machine | performance | — | [evaluation report](https://github.com/Shinraya-Tech-Labs/AgentWatch/blob/main/benchmarks/results/protect_eval/REPORT.md), overhead table (machine stated) |
| The legacy `watch()` gate has 15 built-in patterns (5 critical, blocked by default) | prevention (legacy) | `core/safety.py` | `tests/test_safety.py`; counted from `core/safety.py` |
| Packages install cleanly and have no known-vulnerable dependencies at release | supply chain | `pyproject.toml` floors, `constraints/py312.txt` | `scripts/clean_install.sh`, CI `package` job, [PACKAGING.md](PACKAGING.md) |
| Releases publish only artifacts built from a commit that passed the full CI | supply chain | `.github/workflows/release.yml` | `tests/test_release_check.py`, a local dry run ([RELEASING.md](RELEASING.md)). **No GitHub release-candidate run yet** (TO-033) |

## Removed or corrected claims

- **`panopti demo` as a product proof.** It is a scripted illustration: no database, no tools and no pipeline. It no longer says a production database was saved. `examples/walkthrough/live_demo.py` is the real-pipeline demo.
- **"Every tool call".** Decisions apply to calls routed through an enforcing integration.
- **"Tamper-proof".** Evidence is tamper-evident relative to a trusted external anchor.
- **"RFC 8785" on a rounding canonicalizer.** It rounded floats to ten places, sorted keys by code point and could change values in tool arguments. `panoptis/evidence/jcs.py` now conforms, and neutralizing a payload never changes a number. Older design documents under `docs/archive/history/` and `COMPLETE_PROJECT_DOCUMENTATION.md` still use the term for `json.dumps` sketches and are historical.
- **Erasure in cloud mode.** Cloud rows were stored with no tenant, so a tenant's erasure answered 200 and left them in the database. Rows now carry their tenant, and receipts list older rows as not reached.
- **The competitive blueprint and the "defeat ledger".** The first is retired, and the second no longer claims proof or percentile ranks.
- **"40+ critical patterns"** was not true of any component. It is now stated as 15 legacy patterns, and about 70 finding types for the protection pipeline, with the evaluation linked. The same claim remains on the landing site (`agentwatch-landing/app/components/Features.tsx`), a separate repository outside this branch; correct it there.
- **"One command. Every failure caught."** Not demonstrable; removed.
- **"v3 does not block or control agents" next to "blocks pre-execution"** contradicted itself. It is now explicit that the v3 evidence engine is passive and `panoptis.protect` enforces.
- **The independent auditor "never logs after the fact".** Qualified: it applies on the legacy async `watch()` path and needs `ANTHROPIC_API_KEY`.
- **The Claude Code stream adapter called detected calls "blocked"** although they had already run. They are now labelled as post-execution detections (TO-012).
