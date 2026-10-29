# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The PyPI publish workflow reads the section matching the release tag from this
file and uses it as the GitHub Release body, so keep each version's notes here.

## [Unreleased]

### Added
- Deterministic shadow replay (issue #751): `agentwatch.replay.shadow` replays a recorded session against a model while every tool call is answered from the recorded evidence (nothing is executed), then classifies the result as `IDENTICAL_REPLAY`, `DETERMINISTIC_CORROBORATION` (the recorded failure reproduces from the recorded tool states), `STOCHASTIC_MODEL_DIVERGENCE` or `INSUFFICIENT_EVIDENCE` (a call had no recorded answer). Includes `ToolEvidenceStore`, `ShadowReplayRunner`, `PromptCompletionModel` and a `StepFn` adapter. See `docs/v3/SHADOW_REPLAY.md`.
- Witness attestation (issue #738): `agentwatch.evidence.merkle` (domain-separated Merkle tree with `0x00` leaf and `0x01` node prefixes, inclusion proofs, standalone `verify_inclusion`), `agentwatch.evidence.attestation` (self-contained signed audit token with HMAC-SHA256, optional Ed25519, and field-level hash commitments so a rule can be checked without revealing the prompt or parameters) and `agentwatch.evidence.shredding` (per-subject AES-256-GCM encryption with leaf hashes over the ciphertext, so proofs still verify after `erase(subject)` destroys the key). Merkle, tokens and commitments are standard library only; AES-GCM and Ed25519 use the optional `crypto` extra. See `docs/v3/WITNESS_ATTESTATION.md`.
- `agentwatch session export` now also writes `--format jsonl` (one event per line) and `--format csv` (fixed columns `event_type, timestamp, tool_name, confidence, status, session_id` plus a `metadata_json` column), backed by the stdlib-only `agentwatch.telemetry.exporter` (`export_to_jsonl`, `export_to_csv`). Text that starts with `=`, `+`, `-` or `@` is prefixed with `'` in CSV so spreadsheets do not run it as a formula (issue #769).
- Enterprise Telemetry (OBS-008): OTLP export of agent reasoning traces mapping to OpenTelemetry span hierarchy, ensuring compatibility with Grafana, Jaeger, and Datadog via `BatchSpanProcessor`.
- Scheduled red-team automation (issue #399): externalized the attack corpus to `agentwatch/security/payloads.json` (loadable via `load_corpus()`), plus a Celery task `agentwatch.tasks.run_redteam` that runs the red-team harness on a schedule and returns a JSON vulnerability report (bypassed = guardrails that failed).
- PII/PHI redaction for telemetry (issue #398, CMP-003/004): `agentwatch.security.redaction` masks SSNs, emails, medical diagnoses, and similar sensitive data as `[REDACTED]` (Presidio when installed via the `redaction` extra, else the GDPR/HIPAA regex detectors). Opt-in on the watcher (`GenericAdapter(redact=True)`) scrubs tool-call payloads before they are published/persisted, plus a `GET /api/v1/governance/eu-ai-act-report` Article 15 conformity export.
- Cost-aware model router (`agentwatch.cost.complexity_router`): scores task complexity and routes to the cheapest model capable of handling it — simple subtasks to cheap models (e.g. Gemini Flash), advanced models reserved for complex work — with a budget ceiling and manual override, reusing the `DEFAULT_PRICING` table.
- Automated red-team safety harness (`agentwatch.security.redteam` + `agentwatch redteam` CLI): runs a curated corpus of simulated prompt-injection, path-traversal, credential-scan, and tool-misuse attacks through the detection layer (payloads scored, never executed) and emits a structured resilience report scoring how many attacks are defended vs. bypassed.
- CLI `--api-key` option (with `AGENTWATCH_API_KEY` env fallback) for the `status`, `sessions`, `export`, `compare`, and `session prune` commands, sending the `X-Api-Key` header to protected API endpoints, plus consistent 401 Unauthorized messaging.
- Temporal decay curve manager for episodic memory: importance-weighted exponential forgetting wired into `MemoryEngine` retrieval scoring, plus `prune_decayed()` background cleanup of stale low-importance entries (critical memories persist).
- Locust load-test suite (`tests/load/`) simulating weighted agent traffic.
- Auto-updating contributors wall (scheduled workflow + `contributors.json`).
- PyPI auto-publish workflow on `v*` tags and a PR test/coverage workflow.

### Changed
- Refactor (`#500`): Inlined `SilenceBaseline` attributes into `SilentFailureDetector`. Reduces file complexity and removes the redundant dataclass abstraction (also addresses `#478` and `#498`).

## [0.2.0-preview] - 2026-05-27

### Added
- Universal `watch()` one-line agent wrapper
- LangGraph, AutoGen, and Smolagents adapters
- OpenAI Agents adapter support
- Independent reasoning auditor (12 features)
- Safety engine with OWASP Agentic Top 10 checks
- Multi-agent causal DAG for failure tracing
- Causal memory graph (cross-session reasoning trails)
- 9 new frontend dashboard pages
- 205 test cases passing

### Fixed
- CORS wildcard + credentials bypass vulnerability (CORS fix)
- Hardcoded database password fallback to prevent unauthenticated database access (DB password hardening)
- `publish_sync` logging for silent exception drops
- Duplicate module tree import failures on startup

## [0.1.0] - 2026-05-22

### Added
- Initial release of AgentWatch core features
- Core architecture components: `watch()`, `SafetyEngine`, `EventBus`, FastAPI REST server, and Next.js dashboard UI
- `SafetyEngine` equipped with 40+ destructive command/query pattern detectors
- Independent reasoning auditor to verify reasoning steps pre-execution
- Live real-time WebSocket dashboard stream
- Git-backed session checkpointing and one-click rollback
- Claude Code, LangChain, and AutoGPT adapters
- 47 test cases passing
