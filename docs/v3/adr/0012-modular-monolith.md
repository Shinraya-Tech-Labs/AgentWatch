# ADR-0012: Modular monolith: one engine, optional workers, no microservices

- Status: Proposed
- Date: 2026-09-25

## Context

Six planes could tempt a service-per-plane design. The team is small, and the workload fits on one machine for foreseeable deployments. v0.2's real distributed-systems bug is process-local state behind a multi-replica deployment (D8), which extra services would make worse, not better.

## Decision

- Planes are **Python packages** with enforced import direction. They are not services.
- Deployables:
  - `panoptis server` (API + ingest + inline pipeline);
  - `panoptis worker` (the same code, running pipeline stages and analyzers off a durable cursor; the existing Celery + Redis stack);
    *as built:* there is no `panoptis worker` command. The worker is the Celery container (`Dockerfile.worker`, compose profile `workers`), and v3 processing runs in the API/CLI process: `Engine.process` runs incrementally when the store is read, or via `panoptis reprocess`.
  - the frontend.
- **All state lives in the stores.** No v3 read path uses process memory as its source of truth. LIVE streaming fans out from the store cursor or Postgres LISTEN/NOTIFY, not from an in-memory bus.
- Splitting a component into a service requires a new ADR with measured justification.

## Consequences

- Horizontal scaling means running more API and worker replicas against one database. This is correct once D8 is fixed.
- Pipeline stages must be idempotent and cursor-driven.
