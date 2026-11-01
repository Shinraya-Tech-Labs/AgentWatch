# AgentWatch v3 — Release Readiness

Branch `architecture/v3` · assessed 2026-09-26 · proposed release: **0.3.0 (pre-1.0)**, per MIGRATION_MAP stage M4.

**Status (2026-09-27):** merged into `main` with `--no-ff` (merge commit `17d667d`), keeping every v3 commit. Development continues directly on `main`. Still open: the packaging split (ADR-0008) — the default `pip install panoptis` is deliberately unchanged until the owner approves it — and a release tag (tags publish to PyPI; none has been created).

**Final pre-release state (2026-09-27, `main`).** After the merge, pre-release gaps were fixed one commit at a time on `main`, each with a regression test and green CI (section 4a). No engineering work outside the owner's decisions (section 9) and external credentials remains open. No maturity label was changed.

## 1. Quality bar

| Requirement | Status | Evidence |
|---|---|---|
| Canonical observation pipeline | ✓ | `tests/v3/test_evidence.py`, `test_normalizers.py`; every observation becomes an event or a diagnostic |
| Immutable storage | ✓ | DB triggers reject UPDATE/DELETE (SQLite + PostgreSQL in CI); Merkle segments, verify, inclusion proofs; graph invariant test |
| Crypto-shredding | ✓ (opt-in) | `test_erasure.py`; invariant: erased data cannot be reconstructed from the graph |
| Incremental processing | ✓ | `test_incremental.py`; incremental == full rebuild over seeded random programs |
| Execution graph | ✓ **VALIDATED** | H2 execution F1 1.0 on development and on the first run of all three held-out architectures |
| Information graph behaves conservatively | ✓ | ADR-0017; held-out precision 0.95 (best-effort) and 1.0 (high-fidelity); ambiguity preserved; adversarial and invariant tests |
| Causal evidence classes | ✓ | CAUSAL relations need an evidence class; hypotheses become SUPPORTED only through interventions; an LLM cannot self-verify |
| Provenance | ✓ (EXPERIMENTAL) | lineage with evidence type, strength and ambiguous candidates |
| Motifs | ✓ (EXPERIMENTAL) | 7 detectors; held-out precision 1.0, recall 0.64 |
| Comparison | ✓ (EXPERIMENTAL) | retry-aware earliest divergence; H4 0.96 dev, 1.0 on all architectures after fixes |
| Replay at documented levels | ✓ (EXPERIMENTAL) | L0–L3; reproduction confidence; stale captures never served; L1/L2 1.0 on AWBench |
| API | ✓ | `test_api.py`, `test_api_parity.py` (API = read model = CLI) |
| CLI | ✓ | `test_lab_cli.py::test_cli_full_surface_on_persisted_data`; CLI surface golden |
| Frontend consumes canonical data | ✓ | Jest; type-check; build; the production build was checked against a live API and store (MAP information view, evidence summary, ambiguous values, value panel, COMPARE, LAB, TIMELINE) |
| AWBench reproducible | ✓ | seeded; registry with pre-registered thresholds and changelog; computed evidence status; `results/` |
| Held-out validation exists | ✓ | four held-out architectures, each first run recorded unchanged; the fourth met every threshold (section 3) |
| Limitations documented | ✓ | section 6, IMPLEMENTATION_STATE, RESEARCH_HYPOTHESES §6 |
| Tests and CI green | ✓ | section 2 |

Some systems are EXPERIMENTAL by design and are labelled so in the capability registry (`panoptis status`), without claims of completeness:

- behavioural genome and drift;
- latent states;
- forecasting;
- counterfactual estimation;
- query routing.

## 2. Test and CI status

| Check | Result |
|---|---|
| Backend `pytest tests/` (local, Python 3.14, 2026-09-27) | **1234 passed, 7 skipped**, 0 failed, 263 s |
| — of which v3 | 124 tests (incl. 15 adversarial lineage, 10 graph invariants, 5 API parity, full CLI surface) |
| — skipped | 3 PostgreSQL store tests and 2 connectivity tests (need DATABASE_URL/REDIS_URL/PG URL; they run in CI), 2 real-model tests `SKIPPED_EXTERNAL_CREDENTIAL` |
| `ruff check` / `ruff format --check` (panoptis, tests/v3, benchmarks) | clean |
| mypy (`make typecheck`, v3 modules) | clean; enforced in CI |
| Frontend: ESLint, Prettier check, `tsc --noEmit`, Jest | clean; 16 passed |
| Frontend `next build` | succeeds |
| Docker | CI builds `Dockerfile.api` and `frontend/Dockerfile` on every push (success); `docker compose config` validates all services |
| Compose smoke test (CI) | starts `api` + `frontend` with `--wait`; checks health, 401 without a key, the v3 store on PostgreSQL, an observation round trip, the frontend and its key-attaching proxy, and that a >64 KB payload survives an API restart |
| CI (GitHub Actions, Python 3.12, PostgreSQL + Redis) | green: lint + mypy, unit, integration, frontend, landing, Docker build, compose smoke, security scan |

One Landing Page Build failure in CI was a transient Google Fonts fetch; it passed on re-run.

## 3. AWBench

All AWBench results come from deterministic stub systems. Real-model mode exists (`--real-model`, `REAL_MODELS.md`) but has not been run: no usable credential was available.

**Held-out evidence.** First scored runs, recorded before any change they informed:

| architecture | information precision | information recall | lineage F1 | H4 top-1 | motif P/R |
|---|---|---|---|---|---|
| map_reduce (content-addressed model) | 0.37 | — | — | — | 0.28 / — |
| hybrid_rag_cache (content-addressed model) | 0.53 | 0.92 | 0.96 | 1.0 | 1.0 / 0.55 |
| **async_event_pipeline (information instances, best-effort telemetry)** | **0.95** | 0.51 | 0.33 | 0.89 | 1.0 / 0.64 |
| **code_review_pipeline (information instances, high-fidelity telemetry)** | **1.0** | **0.92** | **0.91** | **1.0** | **1.0 / 0.89** (24/24 after a logged ground-truth correction) |

**Current results (development evidence).** The most recent full run is at `cfac761` (clean tree, 3 seeds), `results/awbench-20260927T080257Z.json`. Its development metrics are identical to the previous committed run (`e802336`). The only difference is `code_review_pipeline` motif recall 0.89 → 1.0, which is the logged, benchmark-only ground-truth correction `e93bde8`. So the post-merge changes (ordering keys, run ordering, entity merging and aliases, storage and retention fixes) moved no AWBench metric.

| thresholded metric | met? |
|---|---|
| H2 execution F1 (1.0), information precision (0.94) | ✓ |
| H3 lineage F1 (0.42) | ✗ |
| H4 top-1 / root at or upstream (0.96 / 0.96) | ✓ |
| H5 motif precision (0.94) / recall (0.36) | ✓ / ✗ |
| H7 drift detection, clean control | ✓ |
| Replay L1/L2, counterfactual, hypotheses, faithfulness | ✓ |
| H2 information recall (0.49) | ✗ |
| H1 cross-source kind F1, OTel only (0.80) | ✗ (with the AgentWatch extension: 1.0, unthresholded) |

The failed recall metrics are the deliberate trade-off of ADR-0017. Content that fits several producers is reported as ambiguous instead of being attributed. With extractive stub models, most intermediates are ambiguous. Following candidates too gives recall 0.80 at precision 0.88 on the development set. No threshold was changed after results were seen.

## 4. Implemented features (v3)

- **Evidence layer:** immutable observations; edge redaction; content-addressed blobs; Merkle-sealed segments; crypto-shredding.
- **Sensors:** native SDK (runtime object identity, explicit `source=`), OpenTelemetry (plus the `panoptis.*` extension), LangChain, Claude Code, OpenAI/Anthropic, MCP, legacy v0.2 translator.
- **Events:** canonical computational events with explicit missing facts; deterministic, versioned interpretations; incremental processing.
- **Graphs:**
  - EXECUTION;
  - INFORMATION over information instances, with evidence type, strength and mode, and ambiguity preserved;
  - CAUSAL, only from hypotheses and interventions with evidence classes.
- **Analysis:** provenance and dependents; run comparison; motifs; profiles, genome and drift; causes and effects; hypotheses.
- **Lab:** observe; replay L0–L3; branches; counterfactuals; experiments.
- **Surfaces:** query engine; API `/api/v3`; CLI; frontend (LIVE, MAP, TIMELINE, LAB, GENOME, COMPARE, QUERY); `panoptis doctor`.
- **AWBench:** development and held-out architectures, pre-registration, computed evidence status, opt-in real-model mode. The performance benchmark compares each run with the previous one.

## 4a. Pre-release fixes after the merge

Each is its own commit on `main` with a regression test; CI was green after each.

- **Storage and privacy.**
  - Large payloads (>64 KB) of a PostgreSQL store were kept in memory and lost on restart; they now go to `AGENTWATCH_BLOB_DIR` or `$AGENTWATCH_HOME/blobs`.
  - Retention purge left payload blob files and derived copies behind; erasure left plaintext artifact blobs. Both now delete them and rebuild (`test_retention.py`).
  - API keys are compared in constant time.
  - HIPAA mode detects SSNs and emails, without MRN false positives.
  - Schema-v1 stores upgrade in place; newer stores are refused.
- **Correctness.** Ordering keys sort numerically under coarse clocks; runs that share a start time are ordered by their first observation; incremental processing merges entity aggregates.
- **Deployment.**
  - Compose used to crash-loop without a key and kept the v3 store inside the container. It now requires the key and uses PostgreSQL plus a blob volume.
  - The frontend container was never healthy (BusyBox resolved `localhost` to `::1`).
  - A compose smoke test runs in CI.
- **CLI, API and docs.**
  - `observe` reports refused arguments instead of crashing.
  - The capability registry works in installed packages.
  - `/api/v3/status` reports the store backend (never the URL).
  - Re-execution warns that ingesting clients choose the command (ADR-0014).
  - Docs commands are checked against the CLI by a test.
  - `.env.example`, `AGENTS.md`, examples and the erasure endpoints were corrected or documented.
- **Features added in this phase:** declared entity aliases (no fuzzy merging), and entity resolution shown in the API and MAP.

## 5. Breaking changes and packaging

- **Graph node model.** Information nodes are `inst:<event>/o<k>` (and `/i<j>`, `/in<k>`), no longer `artifact:<content id>`.
  - `artifact:` references and labels are still accepted by provenance, dependents, API and CLI; they resolve to the most recent instance of that content.
  - Consumers of raw graph JSON must read `inst:` nodes and the new relation attributes (`evidence_type`, `strength`, `mode`, `resolution`).
  - New relation type: `CANDIDATE_SOURCE` (not a flow).
- **Interpretations are rebuilt.** Changed component versions (graph.information@6, native normalizer 2, OTel normalizer 2, motifs 3) produce a new interpretation id, rebuilt automatically on the next `process` or `panoptis reprocess`. Stored observations are untouched. The storage schema is unchanged (v2).
- **CLI.** The v0.2 `compare` command lives under `panoptis legacy compare`; the v3 `compare` compares runs.
- **API.** `/api/v1` is still served, and events posted to it are teed into v3. `/simulate` and `/replay` (v0.2) are relabelled as not deterministic.
- **Packaging (ADR-0008) is not done.** `pip install panoptis` still installs the server stack. The light base install with `[server]`/`[analysis]` extras is scheduled with M4 and needs the owner's sign-off, because it changes what the default install provides.
- **Python** is pinned to `>=3.12,<3.13` (CI 3.12).

## 6. Known limitations

- **Best-effort lineage is conservative.**
  - Low recall of certain links; intermediates that only copy text stay ambiguous.
  - Declared inputs, runtime identity or the OTel extension make lineage exact.
  - Short or templated texts that differ by fewer shingles than one seam cannot be separated.
- **Stub models only.** No real-model AWBench run yet. The OpenAI key present in this environment is rejected by the provider (401 `account_deactivated`); no Anthropic key is set.
- **Held-out evidence is spent.** All four held-out architectures are now FORMER_HELD_OUT (computed). `code_review_pipeline` met every threshold on its first run, before the post-merge changes. Those changes touched interpretation code (ordering keys, run ordering, entity merging), so re-validating the current code, or claiming generalization for it, needs a fresh fifth held-out architecture. Re-running the former held-out ones is development evidence only. The labels (high-fidelity VALIDATED, best-effort EXPERIMENTAL) rest on the first runs and were not changed.
- **Retention and erasure** delete unreferenced payload blobs and every derived copy, then rebuild. A byte-identical large payload ingested at the same moment could lose its shared blob file, so run retention while such payloads are not being ingested.
- **Privacy.** Crypto-shredding is opt-in (`AGENTWATCH_ENCRYPT_PAYLOADS=1`); declared ids are not encrypted; PII redaction is opt-in.
- **Replay** mocks only instrumented calls. Real models are not deterministic, so replay differences can come from the model alone.
- **Entity resolution** is exact-key plus user-declared aliases; no fuzzy merging.
- **Unmeasured:** sensor overhead and ingest throughput vary with machine load; no multi-tenant load test exists.

## 7. Migration steps

1. **Upgrade and check.** Install the release (`pip install -e .` from source, or the 0.3.0 wheel), then run `panoptis doctor` to check the v3 store, schema, interpretation status, evidence chain and crypto availability.
2. **Configure.**
   - `AGENTWATCH_STORE` (SQLite path or PostgreSQL URL);
   - optionally `AGENTWATCH_ENCRYPT_PAYLOADS=1`;
   - `AGENTWATCH_API_KEY` in production;
   - for the frontend, `AGENTWATCH_API_URL`.
3. **Rebuild interpretations.** Run `panoptis reprocess`; `doctor` reports STALE until then.
4. **Existing v0.2 integrations** keep working. `/api/v1/events` is teed into v3, and `panoptis ingest events.jsonl` imports history with translation-loss accounting.
5. **Improve lineage in instrumented code.** Pass produced objects directly, or use `source=`. For OTel, set the `panoptis.information.*` attributes.
6. **Deploy.** Set `AGENTWATCH_API_KEY` in `.env` (compose refuses to start without it), then `docker compose up -d` (images built in CI). The v3 store is on the PostgreSQL service and large payloads are on the `panoptis_blobs` volume. Optional profiles: `workers`, `tracing`.

## 8. Rollback plan

- **Git.** The release merges to `main` as one merge commit, tagged `v0.3.0`. Rollback means reverting that merge with a new commit on `main` (history is never rewritten) or redeploying `v0.2.0`.
- **Data.**
  - v3 data lives in separate tables (`aw3_*`) and does not modify v0.2 tables, so rolling back the code leaves v0.2 data intact.
  - v3 observations are immutable and can stay in place for a later re-upgrade. Interpretations are derived and can be rebuilt.
- **Encrypted payloads.** If payload encryption was enabled, keep the key table (`aw3_data_keys`): observations encrypted under it are unreadable without it.
- **Clients.** v0.2 clients never depended on v3 endpoints. v3-only clients (frontend v3 views, `/api/v3` users) must be pointed back to the v0.2 dashboard.

## 9. Decisions

- **Merge to `main`**: done (option a — merged now, packaging split later), 2026-09-27, `17d667d`.
- **Packaging split** (ADR-0008): pending the owner's explicit approval; it will be its own sequence of commits.
- **Release tag / version bump**: not created. Pushing a `v*` tag publishes to PyPI.
