# AgentWatch v3 — User Guide

AgentWatch v3 passively records what an AI-native system does. It reconstructs each run as an execution graph and an information graph, and lets you inspect, compare, replay and question that reconstruction. Every answer points back to immutable raw evidence.

Everything below describes features that exist on the `architecture/v3` branch. Maturity labels, as shown by `panoptis status`, tell you what has been validated. Most analysis features are still **EXPERIMENTAL**.

## 1. Install

```bash
pip install -e ".[dev]"            # from a checkout (Python 3.12)
pip install "panoptis[otel]"  # optional: OTLP protobuf decoding and the OTel SDK processor
```

By default the store is `~/.agentwatch/agentwatch.db` (SQLite). You can point it elsewhere with `--store` or `AGENTWATCH_STORE`:

```bash
export AGENTWATCH_STORE=sqlite:///./aw.db
export AGENTWATCH_STORE=postgresql+psycopg://user:pass@host:5432/panoptis   # server mode
```

## 2. Record a program

### Native SDK

```python
from panoptis import instrument as aw

@aw.retriever("kb")
def search(q): ...

@aw.model("anthropic/claude-sonnet-5")
def llm(prompt): ...

@aw.tool("calculator")
def calc(expr): ...

with aw.run("research"):
    with aw.span("OPERATION", "plan", actor="agent:planner") as plan:
        docs = search("solar")
        answer = llm(prompt_from(docs))
        aw.memory_write("notes", "k", answer)
    with aw.span("OPERATION", "write", actor="agent:writer", links=[plan]):  # multi-parent
        aw.artifact("report.md", answer)
```

The SDK provides:

- `aw.message`
- `aw.delegate`
- `aw.memory_read`
- `aw.external_input`
- `aw.event` (point events)
- `aw.tag(**attrs)`

### Run it under observation

```bash
panoptis observe python examples/research_system.py
panoptis observe python examples/research_system.py --variant flaky_tool
```

`observe` runs the program in its own process. It records its command so the run can be replayed later, ingests what it recorded, and prints the run id.

### Other sources

| Source | How |
|---|---|
| OpenTelemetry (incl. GenAI conventions) | Send OTLP/HTTP to `POST /v1/traces`, add `AgentWatchSpanProcessor(sink)` to your SDK, or run `panoptis ingest spans.json`. Optional `panoptis.*` attributes (ADR-0018) declare artifact writes and information identity. |
| LangChain / LangGraph | `callbacks=[AgentWatchLangChainSensor(sink)]` (keeps `run_id` / `parent_run_id`) |
| Claude Code | `panoptis ingest transcript.jsonl` (stream-json or transcript files) |
| OpenAI / Anthropic SDK | `instrument_openai(client, sink)`, `instrument_anthropic(client, sink)` |
| MCP | `MCPTap(sink, server="github").record(direction, message)` |
| v0.2 `AgentEvent` | `POST /api/v1/events` (automatically teed into v3) or `panoptis ingest events.jsonl` |

Each legacy event goes through `LegacyTranslator`, which reports ACCEPTED, ACCEPTED_WITH_LOSS or REJECTED, together with the fields that were lost.

## 3. Investigate

```bash
panoptis runs                                # reconstructed runs
panoptis inspect latest                      # execution tree, failures, motifs, missing facts
panoptis events latest --kind TOOL_INVOCATION
panoptis show <event>                        # normalized event + raw evidence + relations
panoptis graph latest --view information --format dot | dot -Tsvg > g.svg
panoptis provenance report.md --run latest   # where did this come from?
panoptis dependents report.md                # what depends on it?
panoptis compare latest~1 latest             # earliest divergence, structure, resources
panoptis motifs latest                       # behavioural motifs (EXPERIMENTAL)
panoptis causes <event>                      # dependency / correlation / hypotheses / interventions
panoptis effects <event>
panoptis query "why did latest take longer than latest~1?"
```

Run references can be full ids, prefixes, run names, `latest` or `latest~N`.

**Information lineage: values, evidence, ambiguity (ADR-0017)**

The information graph is made of *values* (information instances), not content:

- Two values with the same bytes are still two values. `report.md` written twice is two instances.
- `inst:<event>/o<k>` is a value an event produced; `inst:<event>/in<k>` is a value an event consumed that no observed event produced as such.

Every link says what supports it:

| strength | evidence | mode |
|---|---|---|
| STRONG | the sensor declared it: an input/output, `source=`, a declared instance id, or the very object an earlier call returned | high-fidelity |
| MEDIUM | a correlated parent span; a message on the same channel; a memory read returning the written value | best-effort |
| WEAK | identical or contained text | best-effort |
| NONE | similarity into retrieved/external content (`MATCHES_CONTENT`) — never a flow | — |

**When several producers fit the evidence equally well, none is chosen.** For example, the report's text equals both a summary and the documents the summary copied. In that case:

- the sources that are certain under every explanation get links;
- the others are listed as *ambiguous candidates* (`CANDIDATE_SOURCE`): `provenance` prints them as "POSSIBLY from", MAP draws them dotted, and `GET /api/v3/runs/{ref}/provenance-evidence` lists them.

To make lineage exact, declare what a step consumed:

```python
with aw.span("STATE_MUTATION", "write_report", facets=["artifact_creation"]) as s:
    s.input(answer)                     # the object the model call returned: a declared reference
    s.input(text, source=answer_span)   # or name the producing span explicitly
    s.output(report, role="artifact", label="report.md")
```

`panoptis inspect` reports each run's share of declared (high-fidelity) links, and how many consumed values are resolved, ambiguous or of unknown origin.

**Entity aliases.** Different sensors can name the same thing differently (`model:gpt-4o` vs `model:openai/gpt-4o`). AgentWatch never merges entities by similarity. To merge them, declare it:

```bash
echo '{"model:gpt-4o": "model:openai/gpt-4o"}' > aliases.json
export AGENTWATCH_ENTITY_ALIASES=aliases.json
```

The merged entity has resolution basis `DECLARED_ALIAS` and lists the keys it absorbed. Events keep the names as observed. Aliases are part of the interpretation identity, so changing them triggers a rebuild.

**What the outputs mean**

- **Missing facts stay missing.** If the source didn't declare an actor, parent, run or timestamp, the event lists it under `missing`. AgentWatch never invents these.
- **Relation basis.**
  - `DECLARED`: the source said so.
  - `CONTENT_MATCH`: identical content, or text containment (with the measured containment).
  - `KEY_MATCH`: same memory key, content unverified.
  - `HEURISTIC`: for example retries; labelled as such.
  - Information relations also carry `evidence_type`, `strength` and `mode` (see above).
- **Dependency is not causation.** `compare` reports a *dependency cone*. `causes` keeps four sections separate:
  - dependency (OBSERVATIONAL);
  - cross-run correlation (CORRELATIONAL);
  - hypotheses, whose evidence class is computed from recorded evidence;
  - interventions (INTERVENTIONAL / VERIFIED).

## 4. Replay, branch, counterfactual (LAB)

```bash
panoptis replay latest --level L0   # timeline reconstruction, nothing executed
panoptis replay latest --level L1   # re-derive structure from raw evidence and check consistency
panoptis replay latest --level L2   # re-execute; every instrumented call served from captures
panoptis replay latest --level L3 --live calculator  # selected calls run live
panoptis branch latest --at <retrieval-event> --substitute '[{"id":"X","text":"..."}]'
panoptis counterfactual latest --at <event> --alternative '<json>'
panoptis branches latest
```

Replay is never claimed to be deterministic. Each replay reports:

- `reproduction_confidence` (uncalibrated),
- the components that were mocked or run live,
- missing dependencies,
- *stale captures*: a recorded result whose input no longer matches is never served.

Counterfactual results are labelled with one of:

| Label | Meaning |
|---|---|
| OBSERVED | recorded in the source run |
| SIMULATED | produced by an actual branch run |
| MODEL_ESTIMATED | from ≥3 historical runs where the alternative happened |
| UNKNOWN | neither of the above is available |

## 5. Behaviour over many runs (EXPERIMENTAL)

```bash
panoptis genome name:research_system
panoptis drift variant:normal variant:model_v2
panoptis states
panoptis forecast latest
panoptis forecast --evaluate
```

- **Drift** runs permutation tests with FDR control. It reports `insufficient_data` or `underpowered` rather than "no drift" when the sample cannot support a conclusion.
- **States and forecasts** are baselines. They return `insufficient_data` until enough runs exist.

## 6. Evidence integrity

```bash
panoptis evidence verify                                   # seal pending observations, verify the Merkle chain
panoptis evidence show <obs_id>                            # raw observation + inclusion proof
panoptis evidence purge --older-than-days 90 --reason "retention" --yes
panoptis reprocess                                         # rebuild the interpretation from evidence
```

**Per-subject erasure (crypto-shredding).** Set `AGENTWATCH_ENCRYPT_PAYLOADS=1` and declare the data subject when recording (`with aw.run("intake", subject_id="customer-42"):`). Then:

```bash
panoptis evidence erase-subject customer-42 --reason "GDPR art. 17" --yes
```

This destroys the subject's key, purges every derived copy and rebuilds. The raw evidence chain still verifies.

Raw observations cannot be updated or deleted. Database triggers reject both. Retention removes whole sealed segments, and the purge authorization is recorded. A purge (and an erasure) also deletes the blob files of large payloads that nothing references any more, and every derived copy (events, relations, artifacts, analyses, experiments about affected runs); the derived data is then rebuilt from the evidence that remains. Run retention while the same large payloads are not being ingested: a byte-identical payload ingested at that moment could lose its shared blob file. Secrets are redacted before storage, and a redaction manifest is kept.

## 7. Server and UI

```bash
AGENTWATCH_STORE=postgresql+psycopg://... uvicorn panoptis.api.server:app   # /api/v3 + /v1/traces
cd frontend && AGENTWATCH_API_URL=http://localhost:8000 npm run dev            # http://localhost:3000
```

The UI has seven views, all reading the same `/api/v3` model: **LIVE, MAP, TIMELINE, LAB, GENOME, COMPARE, QUERY**. Re-execution from the API (L2/L3, branches) is disabled unless the server sets `AGENTWATCH_ALLOW_REEXECUTION=1`.

## 8. Benchmarks

```bash
python -m benchmarks.awbench.runner      # AWBench; writes benchmarks/awbench/results/
python -m benchmarks.perf.v3_perf        # throughput and latency measurements
```
