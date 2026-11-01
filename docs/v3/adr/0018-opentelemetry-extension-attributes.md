# ADR-0018: AgentWatch OpenTelemetry extension attributes

- Status: Accepted
- Date: 2026-09-26

## Context

OpenTelemetry's GenAI conventions cover model calls, tools and agents. Its database and messaging conventions cover queries and queues. It has no convention for **artifact operations** (a program writing `report.md`), and none for **information identity** (which produced value a span consumed).

As a result:

- H1 cross-source equivalence is stuck at 0.80: a report write traced through OTel normalizes to a generic OPERATION, while the native SDK records a STATE_MUTATION.
- OTel-instrumented systems can only get best-effort, content-based lineage (ADR-0017).

## Decision

AgentWatch reads a small set of **extension attributes**. They are namespaced `panoptis.*` because they are AgentWatch's own convention. They are **not** part of the OpenTelemetry specification, and nothing here claims they are. Spans without them are normalized exactly as before.

| attribute | on | meaning |
|---|---|---|
| `panoptis.artifact.operation` | any span | `create`, `write` or `update` → STATE_MUTATION with facet `artifact_creation`; `delete` → STATE_MUTATION; `read` → EXTERNAL_IO with a READ effect |
| `panoptis.artifact.id` | with `.operation` | the artifact's name or path (object `file:<id>`, output label) |
| `panoptis.artifact.content` | optional | the content written or read (becomes the span's output value) |
| `panoptis.artifact.parent` | optional | the artifact this one derives from (recorded as an attribute, descriptive) |
| `panoptis.information.instance_id` | span producing values | id, or list of ids, for the span's output values, in order |
| `panoptis.information.source` | span consuming values | instance id(s) the span's input is, or was built from. Without captured input content, it is a reference-only input. |

A declared source becomes `DECLARED_REFERENCE` evidence (STRONG, `HIGH_FIDELITY`). It outranks every content heuristic. Instance ids are resolved within a run.

## Consequences

- **H1 is reported both ways.** OTel-only stays the thresholded `cross_source_kind_f1`. OTel plus extension is reported as `cross_source_kind_f1_enriched`, with **no threshold**, because the extension is AgentWatch's own mapping: a high score shows the mapping works, not that plain OTel is equivalent.
- **Instrumentation cost.** The attributes can be set by hand or by an instrumentation library.
- **Upstream changes.** If OpenTelemetry later standardizes artifact or lineage semantics, the normalizer should map those, and these attributes should be deprecated in favour of them.
