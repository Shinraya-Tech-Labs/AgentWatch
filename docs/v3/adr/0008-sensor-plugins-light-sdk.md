# ADR-0008: Plugin sensors; a dependency-light base install; server stack behind an extra

- Status: Proposed
- Date: 2026-09-25

## Context

Sensors run *inside observed applications*. Today the base install pulls FastAPI, SQLAlchemy, asyncpg, Redis, Celery, LiteLLM and MCP (D9). That adds import time, memory and conflict risk inside the host application, which is part of the observer effect. Framework coupling must also stay out of the core.

## Decision

**Sensor protocol** (`start(sink)`, `stop()`, class-level `sensor_type` and `version`):

- Sensors capture raw payloads plus declared identifiers only.
- Sensors never interpret. Normalizers live server-side in `events/normalizers`.
- Discovery uses Python entry points (`panoptis.sensors`), so third-party sensors need no core change.
- Each sensor imports its framework lazily.

**Packaging:**

- `panoptis` (base) depends on `pydantic` and `httpx` only. It contains `evidence` (envelope and codecs), `sensors`, and exporters.
- `panoptis[server]` adds FastAPI, SQLAlchemy, Alembic, drivers and workers.
- `panoptis[analysis]` adds the graph library and numpy/scipy.
- Per-framework extras (`[langchain]`, `[otel]`, …) remain.

**Sensor contract:**

- Non-blocking emit with a bounded buffer.
- Drops are counted and emitted as `sensor.health` observations.
- A per-instance monotonic `source_seq` enables loss detection.
- Sampling decisions are recorded.

**Release gate:** every sensor release passes the overhead benchmark (p99 added latency and memory budgets defined per sensor).

## Consequences

- Breaking packaging change: `pip install panoptis` no longer installs the server. The CLI detects the missing extra and prints the fix. The change is documented in migration notes (MIGRATION_MAP M2).
- An import-linter contract ensures `sensors/*` import only `evidence` and their own framework.
