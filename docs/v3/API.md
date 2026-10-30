# AgentWatch v3 HTTP API

Source: `panoptis/api/v3.py` (mounted by `panoptis/api/server.py`). FastAPI serves the OpenAPI schema at `/docs`.

- **Authentication:** same as v0.2 (`X-Api-Key`).
- **Tenancy:** comes from the API key in cloud mode, and is `default` otherwise. A payload can never select a tenant.
- **Errors:** 404 (not found), 409 (ambiguous prefix), 400 (invalid request), 403 (re-execution disabled), 422 (replay unavailable).

## Ingestion and evidence

| Method | Path | Body / query | Returns |
|---|---|---|---|
| POST | `/api/v3/observations` | `{"observations": [ObservationDraft…]}` | `{accepted, duplicates, rejected[{index, reason}], redacted_observations, obs_ids}` |
| POST | `/v1/traces` | OTLP/HTTP JSON, or protobuf with `opentelemetry-proto` | OTLP `partialSuccess` |
| POST | `/api/v3/legacy/events` | list of v0.2 `AgentEvent` dicts | per-event `TranslationResult` + append result |
| POST | `/api/v3/process` | — | rebuild report (interp id, counts, timings) |
| POST | `/api/v3/evidence/verify` | — | seals pending observations; `{ok, segments_checked, observations_checked, errors}` |
| GET | `/api/v3/observations/{obs_id}` | — | raw observation, inclusion proof, derived events |
| POST | `/api/v3/evidence/erase` | `{subject, reason}` | crypto-shreds a data subject (irreversible; ADR-0011): destroys its key and rebuilds derived data without its payloads; erasure report |
| GET | `/api/v3/evidence/erasures` | — | the tenant's erasure log |

`ObservationDraft` fields:

- `sensor{sensor_type, sensor_version, instance_id}`
- `source_kind`
- `payload`
- `source_seq?`
- `observed_at?` (ISO, timezone-aware)
- `clock{source, clock_id, precision_ms}`
- `declared_ids{…}`
- `sampling?`

## Read model

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v3/status` | interpretation, observation count, capabilities with maturity, `reexecution_enabled` |
| GET | `/api/v3/capabilities` | capability registry |
| GET | `/api/v3/live?limit=` | recent events, active/recent runs, actors, unresolved diagnostics |
| GET | `/api/v3/runs?limit=` | runs of the active interpretation |
| GET | `/api/v3/runs/{ref}` | summary: events, tree, graph stats, failures, retries, missing facts, motifs, profile |
| GET | `/api/v3/runs/{ref}/events?kind=` | normalized events |
| GET | `/api/v3/runs/{ref}/graph?view=all\|execution\|information\|causal` | nodes (described) + relations + stats |
| GET | `/api/v3/runs/{ref}/motifs` | motif instances |
| GET | `/api/v3/runs/{ref}/branches` | branch history |
| GET | `/api/v3/events/{ref}` | event, raw evidence, incoming/outgoing relations, motifs |
| GET | `/api/v3/entities` | resolved entities |
| GET | `/api/v3/artifacts/{ref}` | content by content id, prefix or output label (content identity, not a value) |
| GET | `/api/v3/instances/{node}` | one information instance (`inst:<event>/o<k>` …): content id, producer/consumer, same-bytes count, erased state, relations with evidence |
| GET | `/api/v3/runs/{ref}/provenance-evidence` | the run's information-evidence summary (by evidence type / strength / mode; resolved, ambiguous, unresolved inputs) and every ambiguous value with its candidates |
| GET | `/api/v3/diagnostics` | normalization diagnostics |

`{ref}` accepts a full id, a unique prefix, a run name, `latest` or `latest~N`.

## Analysis

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v3/provenance/{node}?run=` | lineage tree + text rendering + EXPERIMENTAL metrics; every node carries `evidence_type`, `strength`, and `ambiguous` + `alternatives` for candidate sources (not expanded) |
| GET | `/api/v3/dependents/{node}?run=` | forward lineage |
| GET | `/api/v3/compare?a=&b=` | earliest divergence, structural/resource/information/motif diffs, dependency cone share |
| GET | `/api/v3/motifs` | registry with definitions, support and descriptive outcome associations |
| GET | `/api/v3/genome?scope=` | profile aggregate with bootstrap CIs |
| GET | `/api/v3/drift?baseline=&candidate=` | permutation tests + BH; `tested` / `underpowered` / `insufficient_data` |
| GET | `/api/v3/states?runs=&window=` | latent-state baseline (EXPERIMENTAL) |
| GET | `/api/v3/forecast/{ref}` / `/api/v3/forecast/evaluate` | forecast + calibration (EXPERIMENTAL) |
| GET | `/api/v3/causes/{event}`, `/api/v3/effects/{event}` | separate dependency / correlation / hypothesis / intervention sections |
| GET/POST | `/api/v3/hypotheses` | list / propose (`proposer`: human, analyzer, llm, benchmark) |
| POST | `/api/v3/hypotheses/{id}/evidence` | add evidence; INTERVENTION requires a recorded experiment id |
| POST | `/api/v3/query` | `{"text": "..."}`; structured query or routed question; answer + evidence ids + result |

## Lab

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/v3/replay` | `{run, level, live[]}` | L0/L1 always; L2/L3 need `AGENTWATCH_ALLOW_REEXECUTION=1` — which lets any client that can ingest observations choose a command the server runs (ADR-0014); trusted single-user deployments only |
| POST | `/api/v3/branches` | `{run, at, substitute, level, execute}` | executing needs re-execution enabled |
| POST | `/api/v3/counterfactual` | `{run, at, alternative, execute}` | falls back to MODEL_ESTIMATED / UNKNOWN when re-execution is off |
| GET | `/api/v3/experiments?record_type=&subject=` | — | recorded lab experiments of one type (e.g. `branch`), optionally for one run |
| GET | `/api/v3/experiments/{id}` | — | one recorded experiment |
