# ADR-0001: Build v3 inside the repository as a strangler around a new core

- Status: Proposed
- Date: 2026-09-25

## Context

v0.2 has working infrastructure: auth, tenancy, deployment, CI, frontend shell, parsers, and about 940 tests. Its core data model cannot carry v3 semantics: no dependency capture, mutable evidence, control-path coupling (CURRENT_ARCHITECTURE D1–D5).

The options were:

- (a) evolve v0.2 in place,
- (b) start a new repository,
- (c) build a new core inside the repository and strangle the old one.

## Decision

Option (c):

- New v3 packages are added alongside the existing code on a long-lived `architecture/v3` branch.
- v0.2 modules classified DEPRECATE move to `panoptis/legacy/`, with warning shims at their old paths.
- One FastAPI app serves `/api/v1` (legacy) and `/v3` during migration.
- The legacy ingest path tees into a translation sensor, so existing integrations feed v3 from day one.
- v3 packages may not import `panoptis.legacy`. This is enforced by an import-linter contract in CI.

## Consequences

- No integration breaks before a major-version retirement (MIGRATION_MAP M5).
- The repository temporarily holds two architectures. The import contract and the `legacy/` boundary keep them from contaminating each other.
- Evolving in place (a) was rejected: legacy abstractions (`AgentEvent`, risk levels, sessions) would leak into every new component.
- A new repository (b) was rejected: it discards CI, deployment, auth and community history for no architectural gain.
