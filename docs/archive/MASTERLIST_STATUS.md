# AgentWatch v0.2.0-preview — Feature Status

> Generated at end of v0.2.0-preview build. 205 tests passing.

## Per-feature status table

| Feature ID | Name | Status | Tests |
|---|---|---|---|
| **Phase 1 — Foundation** | | | |
| FOUND-001 | Universal watch() SDK | ✅ done | `test_watcher.py::test_watch_*` |
| FOUND-002 | Remove demo dependency | ✅ done | adapters operate via EventBus, demo.py untouched |
| FOUND-003 | Fix existing adapters | ✅ done | claude_code/langchain/autogpt/openclaw verified intact |
| FOUND-004 | New Tier 1 adapters | ✅ done | langgraph/autogen/smolagents + tests |
| **Phase 2 — Core Observability** | | | |
| OBS-001 | Full-span trace collection | ✅ done | `test_observability::test_span_*` |
| OBS-002 | Live session dashboard | ✅ done | `LiveStreamHub` + WebSocket auto-session-create |
| OBS-003 | Session replay engine | ✅ existing engine.py reused | `frontend/pages/replay.tsx` |
| OBS-004 | Trajectory mapping | ✅ done | `test_observability::test_trajectory_*` |
| OBS-005 | Silent failure detector | ✅ done | `test_observability::test_silence_*` |
| OBS-006 | Tool call audit log | ✅ done | `test_observability::test_audit_*` |
| OBS-007 | Embedding drift heatmap | ✅ done | `test_observability::test_drift_*` |
| OBS-008 | Grafana / OTEL export | ✅ done | `test_observability::test_otel_*` |
| OBS-009 | Counterfactual replay | ✅ done | `test_observability::test_counterfactual_*` |
| OBS-010 | Production traffic sampling | ✅ done | `test_observability::test_*_sampler` |
| **Phase 3 — Reasoning Auditor** | | | |
| RSN-001 | Independent confidence scorer | ✅ existing `reasoning/auditor.py` preserved | `test_pipeline` |
| RSN-002 | Hallucination risk classifier | ✅ done | `test_reasoning::test_hallucination_*` |
| RSN-003 | Goal drift detector | ✅ done | `test_reasoning::test_goal_drift_*` |
| RSN-004 | Reasoning quality score | ✅ done | `test_reasoning::test_quality_*` |
| RSN-005 | Adversarial auditor resistance | ✅ done | `test_reasoning::test_adversarial_*` |
| RSN-006 | Semantic drift (cross-session) | ✅ done | `test_reasoning::test_cross_session_drift_*` |
| RSN-007 | Auditor calibration | ✅ done | `test_reasoning::test_calibration_*` |
| RSN-008 | Reasoning fingerprinting | ✅ done | `test_reasoning::test_fingerprint_*` |
| RSN-009 | Dual-level goal evaluation | ✅ done | `test_reasoning::test_dual_eval_*` |
| RSN-010 | Reasoning benchmark suite | ✅ done | `test_reasoning::test_benchmark_*` |
| RSN-011 | Why-this-was-blocked explainer | ✅ done | `test_reasoning::test_explainer_*` |
| RSN-012 | Trust score per session | ✅ done | `test_reasoning::test_trust_score_*` |
| **Phase 4 — Persistent Memory** | | | |
| MEM-001 | Episodic memory store | ✅ existing `memory/engine.py` preserved | pre-existing tests |
| MEM-002 | Causal memory graph | ✅ done | `test_memory::test_causal_graph_*` |
| MEM-003 | Cross-session identity | ✅ done | `test_memory::test_identity_*` |
| MEM-004 | Memory conflict resolver | ✅ done | `test_memory::test_resolver_*` |
| MEM-005 | Forgetting curve engine + temporal decay manager (engine-integrated retrieval decay & cleanup) | ✅ done | `test_memory::test_*_decay`, `test_critical_memories_*`, `test_decay_manager_*`, `test_engine_prune_decayed_*`, `test_engine_retrieval_deprioritizes_*` |
| MEM-006 | Memory health monitor | ✅ done | `test_memory::test_health_monitor_*` |
| MEM-007 | Natural-language memory query | ✅ done | `test_memory::test_parse_*`, `test_query_*` |
| MEM-008 | Memory governance | ✅ done | `test_memory::test_retention_*`, `test_erasure_*` |
| MEM-009 | Memory visualization | ✅ done | `test_memory::test_visualization_*` + `frontend/pages/memory.tsx` |
| **Phase 5 — Safety Engine** | | | |
| SAF-001 | Pre-execution causal blocking | ✅ existing `core/safety.py` preserved | pre-existing tests |
| SAF-002 | Risk scoring engine | ✅ done | `test_safety::test_risk_scoring_*` |
| SAF-003 | OWASP Agentic Top 10 | ✅ done | `test_safety::test_owasp_*` + `frontend/pages/security.tsx` |
| SAF-004 | Blast radius estimator | ✅ done | `test_safety::test_blast_radius_*` |
| SAF-005 | Agent policy DSL | ✅ done | `test_safety::test_policy_dsl_*` + `frontend/pages/policies.tsx` |
| SAF-006 | Prompt injection detector | ✅ done | `test_safety::test_injection_*` |
| SAF-007 | Recursive loop detector | ✅ done | `test_safety::test_loop_*` |
| SAF-008 | Exfiltration attempt detector | ✅ done | `test_safety::test_exfil_*` |
| SAF-009 | Security audit report | ✅ done | `test_safety::test_security_report_*` |
| SAF-010 | Live safety sandbox | ✅ done | `test_safety::test_sandbox_*` + `frontend/pages/sandbox.tsx` |
| SAF-011 | Blast radius UI | ✅ surfaces in sandbox + replay pages | n/a |
| SAF-012 | Automated red-team safety harness + scheduled automation (issues #371/#399; SAF-008 category) — externalized `payloads.json` corpus and a Celery `run_redteam` task | ✅ done | `test_redteam::*`, `test_redteam_automation::*` |
| **Phase 6 — Multi-Agent** | | | |
| MAG-001 | Inter-agent causal DAG | ✅ done | `test_multiagent::test_dag_*` + `frontend/pages/multiagent.tsx` |
| MAG-002 | Deadlock detector | ✅ done | `test_multiagent::test_deadlock_*` |
| MAG-003 | Inter-agent trust scoring | ✅ done | `test_multiagent::test_trust_*` |
| MAG-004 | Failure propagation tracer | ✅ done | `test_multiagent::test_propagation_*` |
| MAG-005 | Shared crew context | ✅ done | `test_multiagent::test_crew_context_*` |
| MAG-006 | Shapley attribution | ✅ done | `test_multiagent::test_shapley_*` |
| MAG-007 | Consensus failure detector | ✅ done | `test_multiagent::test_consensus_*` |
| MAG-008 | Agent spawning tracker | ✅ done | `test_multiagent::test_spawning_*` |
| **Phase 7 — Cost Intelligence** | | | |
| CST-001 | Per-session token budget | ✅ existing `cost/tracker.py` covers it | `test_cost::test_tracker_*` |
| CST-002 | Model cost comparator | ✅ done | `test_cost::test_comparator_*` |
| CST-003 | Model degradation auto-router | ✅ done | `test_cost::test_router_*` |
| CST-004 | Task cost predictor | ✅ done | `test_cost::test_predictor_*` |
| CST-005 | Cost anomaly detector | ✅ done | `test_cost::test_anomaly_*` |
| CST-006 | ROI calculator | ✅ done | `test_cost::test_roi_*` |
| CST-007 | Budget governance policies | ✅ done | `test_cost::test_budget_governance_*` + `frontend/pages/costs.tsx` |
| CST-009 | Cost-aware model router — complexity → cheapest capable model (issue #375, CST-004 scope) | ✅ done | `test_cost_router::*` |
| **Phase 8 — Compliance** | | | |
| CMP-001 | GDPR data handling | ✅ done | `test_compliance::test_pii_*`, `test_gdpr_erasure_*` |
| CMP-002 | SOC 2 audit trail | ✅ existing `governance/engine.py` + reports.py | `test_compliance::test_compliance_export_*` |
| CMP-003 | HIPAA compliance mode + telemetry PII/PHI redaction (issue #398) | ✅ done | `test_compliance::test_phi_*`, `test_hipaa_*`, `test_redaction::*` |
| CMP-004 | EU AI Act Article 15 package + conformity export endpoint (issue #398) | ✅ done | `test_compliance::test_eu_ai_act_*`, `test_redaction::test_eu_ai_act_report_endpoint` |
| CMP-005 | Enterprise RBAC + SAML SSO | ✅ done | `test_compliance::test_rbac_*`, `test_saml_*` |
| CMP-006 | Compliance report generator | ✅ done | `test_compliance::test_compliance_export_*` |
| CMP-007 | Data residency controls | ✅ done | `test_compliance::test_residency_*` |
| CMP-008 | Causal compliance attribution | ✅ done | `test_compliance::test_causal_attribution_*` |
| CMP-009 | ISO 42001 AI Management System | ✅ done | `test_compliance::test_iso42001_*` + `frontend/pages/compliance.tsx` |
| **Phase 9 — Platform** | | | |
| PLT-001 | Session sharing via public link | ✅ done | `test_platform::test_share_link_*`, `test_render_for_viewer_*` |
| PLT-002 | REST + WebSocket API | ✅ existing API preserved (owned by issue #22) | n/a |
| PLT-003 | Alerting hub | ✅ existing `alerting/engine.py` preserved | pre-existing |
| PLT-004 | Cloud deploy configs | ✅ done | `fly.toml`, `helm/Chart.yaml`, `helm/values.yaml`, `helm/templates/deployment.yaml` |
| PLT-005 | Prompt version management | ✅ done | `test_platform::test_prompt_*` |
| PLT-006 | Evaluation dataset builder | ✅ done | `test_platform::test_eval_dataset_*` |
| PLT-007 | Multi-tenant config | ✅ done | `test_platform::test_tenant_*` |
| PLT-008 | Plugin system | ✅ done | `test_platform::test_plugin_registry_*` |
| PLT-009 | AgentWatch Intelligence | ✅ done | `test_platform::test_intelligence_*` |
| **Phase 10 — Protocol Play** | | | |
| PRT-001 | Open reasoning trace schema | ✅ done | `test_protocol::test_schema_*`, `test_validate_trace_*` |
| PRT-002 | AgentWatch-compatible badge | ✅ done | `test_protocol::test_badge_*` |
| PRT-003 | Anonymized failure benchmark | ✅ done | `test_protocol::test_anonymized_benchmark_*` |
| PRT-004 | MCP server integration | ✅ done | `test_protocol::test_mcp_server_*` |

## Frontend pages

| Page | File | Status |
|---|---|---|
| Dashboard | `frontend/pages/index.tsx` | ✅ existing |
| Session detail | `frontend/pages/sessions/[id].tsx` | ✅ existing |
| Memory | `frontend/pages/memory.tsx` | ✅ new |
| Multi-agent | `frontend/pages/multiagent.tsx` | ✅ new |
| Security | `frontend/pages/security.tsx` | ✅ new |
| Benchmark | `frontend/pages/benchmark.tsx` | ✅ new |
| Compliance | `frontend/pages/compliance.tsx` | ✅ new |
| Costs | `frontend/pages/costs.tsx` | ✅ new |
| Policies | `frontend/pages/policies.tsx` | ✅ new |
| Sandbox | `frontend/pages/sandbox.tsx` | ✅ new |
| Replay studio | `frontend/pages/replay.tsx` | ✅ new |

## Test summary

```
python -m pytest tests/ --tb=short -q
=========== 205 passed, 2 skipped, 16 warnings in 85s ============
```

## Caveats (rough-edges acceptance)

- **API surface not modified.** `panoptis/api/main.py` is owned by issue #22. New backend modules ship as importable Python; route mounting is left to whoever owns that file.
- **pyproject.toml version still `0.1.0`.** Owned by issue #21. The in-package `panoptis.__version__` is `0.2.0`. Final version bump deferred until issue #21 lands.
- **Some new modules don't have wired-up API routes yet.** Frontend pages render empty states cleanly when their endpoint is missing — they don't crash.
- **`panoptis/telemetry/publisher.py`** still untouched (issue #23). Only new files `panoptis/telemetry/otel.py` and `__init__.py` were added.
- **`panoptis/core/config.py`** untouched (issue #24).
- **Python 3.14 environment.** `asyncio.iscoroutinefunction` deprecation warnings are harmless.

## Tag

`v0.2.0-preview` applied to the head of branch `v0.2.0-preview`.
