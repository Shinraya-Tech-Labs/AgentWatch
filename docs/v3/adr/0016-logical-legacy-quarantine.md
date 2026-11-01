# ADR-0016: Legacy quarantine is enforced logically until retirement

- Status: Accepted
- Date: 2026-09-26
- Amends: ADR-0001, MIGRATION_MAP stage M2

## Context

M2 planned to move roughly 90 DEPRECATE modules under `panoptis/legacy/` and leave re-exporting shims at the old paths. In practice:

- Many v0.2 tests monkeypatch attributes on the old module paths. A shim that re-exports with `import *` does not forward monkeypatching, and it drops private names. The move would break or silently weaken hundreds of tests that currently protect v0.2 behaviour.
- The risk M2 guards against is v3 core depending on v0.2 abstractions. That is already prevented by `tests/v3/test_architecture_contracts.py`, which runs in CI and:
  - fails if any v3 package imports a deprecated module;
  - fails if any sensor imports the server stack;
  - fails if any module reads benchmark ground truth.
- The packaging split (ADR-0008) changes what `pip install panoptis` provides. That is a user-facing breaking change that belongs with the merge to `main` (M4), where it can be announced.

## Decision

- Quarantine stays **logical**: the deprecated set is the `DEPRECATED` list in the contract test, mirrored in MIGRATION_MAP §1. The files stay where they are until **M5 (retire)**, when they are deleted outright rather than moved.
- The packaging split (light base install, `[server]` extra) is scheduled with **M4** and needs owner sign-off.

## Consequences

- There is no import-path churn for v0.2 users or tests during migration.
- The v0.2 code remains visible in the tree. The contract test, not the directory layout, is the boundary.
