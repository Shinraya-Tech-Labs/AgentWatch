# ADR-0006: Relational-first storage (SQLite embedded / PostgreSQL server) + content-addressed blobs; no graph DB, no columnar store until measured

- Status: Proposed
- Date: 2026-09-25

## Context

The brief suggests Postgres, a columnar event store, a graph store, blob storage and optional vectors. Each extra datastore adds operational burden, consistency problems and deployment friction. The brief also says to choose technology by benchmark, not by fashion.

Workload shape:

- Ingest is append-heavy.
- Analysis is mostly *per run*. Runs are bounded at roughly 10²–10⁵ events.
- Cross-run analysis works on aggregates (motif statistics, genomes).

## Decision

- **One relational store** behind narrow protocols (`ObservationStore`, `InterpretationStore`, `RelationStore`, `DerivedStore`):
  - SQLite (WAL mode) for embedded, local, test and AWBench use;
  - PostgreSQL 16 for server mode, using the existing pgvector image.
- Schema changes go through **Alembic** migrations, replacing `create_all` (D13).
- A **BlobStore** protocol with filesystem and S3-compatible implementations holds payloads above an inline threshold (default 64 KiB) and artifacts. It is content-addressed and write-once.
- **Graph analysis runs in memory, per run.** Relations are loaded into a graph library, chosen in Phase 2 by benchmark between `rustworkx` and `networkx` on 10⁵-edge runs.
- **Columnar store trigger:** add Parquet export plus DuckDB for analytical queries when a measured COMPARE or GENOME query over ≥ 10⁶ events exceeds 5 s p95 on the reference server spec.
- **Graph DB trigger:** reconsider only if a required query class needs multi-hop traversals across more than 10⁶ nodes spanning runs, *and* it cannot be served from aggregates.
- pgvector stays optional. It is used for artifact similarity and NL query retrieval only.

## Consequences

- `pip install panoptis && panoptis ingest …` works with no services.
- There is one backup and restore story, and one migration tool.
- The store protocols must avoid Postgres-only features in core paths, or provide SQLite equivalents (for example a side table instead of a GIN index for `declared_ids`).
