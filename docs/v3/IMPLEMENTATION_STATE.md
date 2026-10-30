# AgentWatch v3 — Implementation State

Branch: `main` (v3 merged 2026-09-27, `17d667d`) · Updated 2026-09-27 · Release readiness: [RELEASE_READINESS.md](RELEASE_READINESS.md)

## Completed

| Area | Where | Status |
|---|---|---|
| Immutable evidence: frozen observations, DB-enforced immutability, idempotent append, edge redaction + manifests, blobs (persisted for every store backend), Merkle segments, verify, inclusion proofs, authorized purge (also deletes payload blobs and derived copies, then rebuilds) | `panoptis/evidence`, `panoptis/storage` | tested on SQLite and PostgreSQL (CI); `test_retention.py`, `test_blob_location.py` |
| Crypto-shredding: per-subject keys, erasure destroys the key, purges derived copies and their blobs | `panoptis/evidence/crypto.py`, `storage` | tested; graph invariant: erased data is not reconstructable |
| Sensors: native SDK (incl. runtime object identity and explicit `source=`), OTel (OTLP JSON/protobuf, SpanProcessor, `panoptis.*` extension attributes), LangChain, Claude Code, OpenAI/Anthropic wrappers, MCP tap, LegacyTranslator | `panoptis/sensors`, `panoptis/instrument.py` | tested |
| Normalizers + canonical event algebra with explicit missing facts | `panoptis/events` | tested |
| Engine: versioned, deterministic rebuild; incremental per correlation group (with cross-run memory context); every observation → event or diagnostic | `panoptis/runtime` | tested; incremental == full over random programs |
| Runs from declared ids only; exact-key entities | `panoptis/runs`, `panoptis/entities` | tested |
| Execution graph | `panoptis/graph/build.py` | **VALIDATED**: AWBench H2 F1 1.0 on development and on the first run of all three held-out architectures |
| Information graph over information instances, with an evidence hierarchy and preserved ambiguity (ADR-0017) | `panoptis/graph/information.py` | high-fidelity mode **VALIDATED** (held-out code_review_pipeline: information P/R 1.0/0.92, lineage F1 0.91); best-effort mode EXPERIMENTAL |
| Provenance with ambiguity records and per-run evidence summaries | `panoptis/provenance` | tested; API/CLI parity |
| Run comparison (retry-aware earliest divergence) | `panoptis/compare` | tested + AWBench H4 |
| Motifs (7), profiles, genome, drift with power check | `panoptis/behaviour` | tested + AWBench H5/H7 |
| Causality: hypotheses with computed evidence class; causes/effects | `panoptis/causality` | tested + AWBench |
| Lab: observe, replay L0–L3, branches, counterfactuals, experiments API | `panoptis/lab` | end-to-end tests + AWBench |
| Latent states, forecasting | `panoptis/state`, `panoptis/forecasting` | EXPERIMENTAL, insufficient-data paths tested |
| Query engine | `panoptis/query/engine.py` | tested + AWBench faithfulness |
| API `/api/v3` + `/v1/traces` + legacy tee | `panoptis/api/v3.py` | tested; parity with read model and CLI |
| CLI (v3 commands + `evidence` group; `doctor` checks the v3 store) | `panoptis/cli/v3.py` | full-surface test on a store written by `observe` |
| Frontend: LIVE, MAP, TIMELINE, LAB, GENOME, COMPARE, QUERY (values, evidence strength, ambiguity, erased state) | `frontend/` | Jest, type-check, build; production build verified against live data |
| AWBench: 3 development + 4 held-out architectures, computed evidence status, pre-registration, opt-in real-model mode | `benchmarks/awbench` | results committed |
| Performance benchmark (with comparison to the previous run) | `benchmarks/perf` | results committed |
| Docker images (API, frontend) and compose stack (v3 store on PostgreSQL, blob volume, key required) | `Dockerfile.api`, `frontend/Dockerfile`, `docker-compose.yml` | built in CI on every push; compose smoke test in CI |

## Known limitations

- **Best-effort information lineage is conservative, and its recall is low.**
  - With value-level telemetry, an intermediate that only copies text can't be proven to have been used rather than its sources. It is kept as an ambiguous candidate. AWBench's stub models are extractive, so this shows up everywhere there.
  - Held-out `async_event_pipeline`, first run: information precision 0.95, recall of certain links 0.51; lineage precision 1.0, recall 0.20.
  - With candidates, recall rises to 0.58–0.95 at lower precision.
  - Declared inputs (`source=`, passing the produced object, OTel `panoptis.information.source`) make lineage exact.
- **H1 cross-source equivalence (OTel only) is 0.80 < 0.90.**
  - OpenTelemetry has no artifact-write convention.
  - With the AgentWatch extension it is 1.0. That figure is unthresholded: the extension is AgentWatch's own mapping.
- **M006 (information bottleneck)** fires only when a bottleneck is certain, so recall is low with extractive intermediates.
- **Content matching has a resolution limit.** Candidates that differ by fewer shingles than one concatenation seam (4) cannot be separated.
- **AWBench uses stub models.** Real-model runs are implemented (`--real-model`) but have not been run: no usable credential was available. The OpenAI key in this environment is rejected by the provider (401, `account_deactivated`).
- **Held-out status.** All four held-out architectures are FORMER_HELD_OUT. Three informed fixes. The fourth (`code_review_pipeline`, high-fidelity) met every threshold on its first run, before the post-merge changes to interpretation code (ordering keys, run ordering, entity merging).
  - The development regression run at `cfac761` shows no metric moved.
  - Re-validating the current code, or any generalization claim about it, needs a fresh fifth held-out architecture.
  - Maturity labels were not changed.
- **Retention concurrency.** Purge and erasure delete payload blobs that nothing references. A byte-identical large payload ingested at that moment could lose its shared blob (documented in USER_GUIDE §6).
- **Privacy.**
  - Crypto-shredding is opt-in (`AGENTWATCH_ENCRYPT_PAYLOADS=1`), and declared ids are not encrypted (ADR-0011).
  - PII redaction is opt-in.
- **Replay** mocks only instrumented calls. Other program logic runs live, as the reports say.
- **Entity resolution** is exact-key, plus aliases the user declares (`AGENTWATCH_ENTITY_ALIASES`); nothing is merged by similarity.
- **Packaging split (ADR-0008)** is not done; the base install still includes the server stack.
- **Python versions.** The pin is `>=3.12,<3.13`. The full suite passes on 3.12 locally (Windows) and in CI (Linux); development also ran on 3.14.

## Next exact tasks

1. Owner decision: the packaging split (ADR-0008) and a release tag. v3 is merged into `main`, and the post-merge pre-release fixes are done (RELEASE_READINESS §4a); no other engineering work is open.
2. Run AWBench `--real-model` with a working credential, and the `external` test marker.
3. A fifth held-out architecture before claiming anything about later changes; a real-model held-out run would test best-effort lineage on abstractive outputs.
