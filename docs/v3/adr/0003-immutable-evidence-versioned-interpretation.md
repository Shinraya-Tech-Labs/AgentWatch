# ADR-0003: Immutable evidence; every interpretation is versioned and re-derivable

- Status: Proposed
- Date: 2026-09-25

## Context

v0.2 stores AgentWatch's own verdicts (risk scores, confidence, anomaly flags) in the same row as the observation. It redacts in place. It can lose events silently (D2). When a detector improves, history cannot be re-interpreted, and nobody can audit what was actually observed.

## Decision

- **Evidence plane** (`observations`, blobs) is append-only:
  - The store protocol has no update or delete.
  - In Postgres, the application role lacks UPDATE and DELETE on `observations`, and a trigger rejects updates.
  - Idempotent append uses `idempotency_key`.
- Observations are sealed into **hash-chained segments** with Merkle roots. This generalizes `governance/audit_log.py` from per-row to per-segment chaining, so writers are not serialized. `panoptis evidence verify` validates the chain end to end.
- Every derived record carries `derived_by = (component, version, config_hash)` and references to its evidence. A new normalizer, resolver or analyzer version writes new rows under a new interpretation id. Old interpretations are retained, marked `superseded`, and garbage-collected only by an explicit retention policy.
- Event ids are deterministic: `uuid5(normalizer name, sorted obs ids, local index)`. References therefore survive re-interpretation where the derivation did not change.

## Consequences

- Storage grows with interpretation versions. Retention policy and GC are required (Phase 2).
- Any derived plane can be dropped and rebuilt, which is also the disaster-recovery story for derived data.
- Scientific auditability: every UI claim can be traced to specific raw observations, and those observations can be proven unaltered.
- Erasure must be reconciled with immutability. See ADR-0011.
