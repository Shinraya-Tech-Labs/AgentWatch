# Project Status and Remaining Tasks

**Checked:** 2026-11-01  
**Repository:** `main`, HEAD `9a6b9fd` (`origin/main`); working tree was clean before this file was added.  
**Scope:** Python API/package, v3 evidence pipeline, dashboard, landing app, tests, benchmarks, Docker deployment, CI/release workflows, and the attached `COMPLETE_PROJECT_DOCUMENTATION.md`.

## Review coverage

- The tracked source inventory contains **555 code files and about 101,500 lines**, including tests and benchmarks. The full implementation/configuration inventory also contains JSON/YAML/TOML files and dependency locks.
- All **489 tracked Python files** parsed successfully for syntax and top-level structure (functions/classes), using BOM-aware UTF-8 decoding. A whole-tree search covered security-sensitive patterns, TODO markers, tenant/erasure paths, dependency declarations, and roadmap feature names.
- High-risk files were read in full or in the relevant complete path. This is not a literal manual line-by-line review of all 101,500 lines. Broad UI styling, historical docs, generated content, and every test assertion were not individually audited. No tests or builds were run.

## Current state

The repository's current v3 implementation record says the evidence pipeline, sensors, processing engine, analysis, API, CLI, dashboard, Docker images, and Compose smoke test are implemented. The same record says no general engineering work is open beyond owner decisions and external validation. See [`docs/v3/IMPLEMENTATION_STATE.md`](docs/v3/IMPLEMENTATION_STATE.md) and [`docs/v3/RELEASE_READINESS.md`](docs/v3/RELEASE_READINESS.md).

This task list separates confirmed work from decisions and proposed roadmap items. `TO` IDs are stable references for follow-up.

## Confirmed work to do

### TO1 — Scope GDPR session erasure deletes to the authenticated tenant

- **Priority:** P0 — data isolation and destructive operation.
- **Status:** Open; code inspection found a tenant-filter mismatch.
- **Evidence:** [`agentwatch/api/server.py`](agentwatch/api/server.py) `_SessionErasureTarget.erase_matching`. A cloud tenant's session-ID count is tenant-filtered, but the session-ID deletion calls `delete_events_by_session(identifier)` and `delete_session(identifier)` without a tenant constraint.
- **Work:** Apply the caller tenant scope to both deletes; ensure the operation cannot delete a session whose tenant does not match.
- **Done when:** A DB-backed regression test seeds two tenants, erases tenant B's session ID as tenant A, and verifies both tenant B's session and events remain.

### TO2 — Bind v3 tenant identity to trusted authentication

- **Priority:** P0 — tenant isolation.
- **Status:** Open; caller-controlled tenant selection is present in the default mode.
- **Evidence:** [`agentwatch/api/server.py`](agentwatch/api/server.py) `_v3_tenant` returns the `X-Tenant-ID` header when cloud mode is off. The v3 API module states tenancy must come from authentication and callers cannot select another tenant.
- **Work:** Make single-tenant mode always use `default`. In cloud mode, use only a tenant returned from a validated API key and reject invalid or absent credentials. Move the current header-based multi-tenant tests to authenticated cloud-mode fixtures.
- **Done when:** API tests show that changing `X-Tenant-ID` cannot change a caller's data scope and that invalid cloud keys do not resolve to `default` or a supplied tenant; existing tenant-isolation coverage still passes using validated identities.

### TO3 — Persist cloud tenants and API keys in shared storage

- **Priority:** P0 before cloud mode is used across restarts or replicas.
- **Status:** Open; current tenant/key store is process-local.
- **Evidence:** [`agentwatch/api/tenant_auth.py`](agentwatch/api/tenant_auth.py) `TenantStore` stores tenants, API keys, revocations, and usage in Python dictionaries; `get_tenant_store()` creates a module singleton.
- **Work:** Back tenant and API-key records with durable shared storage, including hashed secrets, revocation, expiry, and tenant status. Preserve the raw-key-only-at-creation behavior.
- **Done when:** Keys and revocation state behave consistently after process restart and when requests hit different API replicas.

### TO4 — Fail closed when configured production persistence cannot start

- **Priority:** P1 — write durability.
- **Status:** Open; startup can silently degrade to memory.
- **Evidence:** [`agentwatch/api/server.py`](agentwatch/api/server.py) lifespan catches `init_db()` failures and continues; the health route reports `in_memory` without marking the service degraded when no DB session factory exists.
- **Work:** In production, fail startup if a configured database cannot initialize. If persistence is absent by design, make that explicit and ensure production write routes do not imply durable storage.
- **Done when:** Startup/health tests cover database absent, connection failure, and normal operation; the failure case cannot return a healthy status while accepting non-durable production writes.

### TO5 — Remove shell interpolation of pushed tag values in release workflow

- **Priority:** P1 — CI credential and release security.
- **Status:** Open; release tag is inserted into a shell script.
- **Evidence:** [`.github/workflows/publish-pypi.yml`](.github/workflows/publish-pypi.yml) interpolates `${{ steps.version.outputs.version }}` inside `run:`. GitHub warns that untrusted context values inserted into shell scripts can cause script injection: [GitHub Actions script-injection guidance](https://docs.github.com/en/actions/concepts/security/script-injections).
- **Work:** Pass the tag/version through an environment variable, validate it in the shell, then compare it to `pyproject.toml` without expression interpolation into script source.
- **Done when:** Workflow uses safe data passing and rejects non-version tag names before build or publish steps.

### TO6 — Protect Compose database ports and credentials

- **Priority:** P1 when Compose is run on a reachable host; P2 for localhost-only development.
- **Status:** Open; Postgres has a fixed credential and a published port.
- **Evidence:** [`docker-compose.yml`](docker-compose.yml) sets `POSTGRES_PASSWORD: agentwatch` and maps `5432:5432`. Docker publishes to all host interfaces by default when no host IP is given: [Docker port-publishing docs](https://docs.docker.com/engine/network/port-publishing/).
- **Work:** Bind Postgres to localhost for local development, or remove host publication where unnecessary. For remote deployment, require a supplied strong secret and network controls. Review Redis and Jaeger port publication against their intended use at the same time.
- **Done when:** The default local Compose stack does not expose the database beyond the host and deployment guidance documents any intentional externally reachable ports.

### TO7 — Choose one release workflow and make its checks authoritative

- **Priority:** P1 — release correctness.
- **Status:** Open; overlapping tag workflows exist.
- **Evidence:** [`.github/workflows/publish-pypi.yml`](.github/workflows/publish-pypi.yml) and [`.github/workflows/release.yml`](.github/workflows/release.yml) both match version tags and both create GitHub Releases. The latter's “Build and Test” job only prints a message; it does not build or test.
- **Work:** Keep one release-creation path. Run the actual build and required checks before publishing, and give publish permissions only to the publishing job.
- **Done when:** A version tag triggers one authoritative release flow; a failing build cannot publish; the GitHub Release is created once.

### TO17 — Remove the public default key from Panopti attestations

- **Priority:** P0 if these seals are used as compliance or audit evidence.
- **Status:** Open; the production-facing API has a known default signing key.
- **Evidence:** [`panopti/attest.py`](panopti/attest.py) defaults both sealing and verification to `panopti-seal-default-key`; [`panopti/cli.py`](panopti/cli.py) uses the same key when `attest verify` is called without `--key`. Anyone who knows the published source can mint a seal that verifies as `COMPLIANT`. Tests currently assert that this default verifies.
- **Work:** Require an explicit high-entropy key for seal creation and verification; remove the CLI fallback and make missing-key behavior fail closed. Keep demo/test secrets explicit and clearly non-production.
- **Done when:** Default keyless signing is impossible; tests prove a missing key fails and a seal signed with a caller-owned key still round-trips and rejects tampering.

### TO18 — Implement or remove the API's missing memory-erasure target

- **Priority:** P1 — right-to-erasure currently reports partial failure.
- **Status:** Open; target imports functions not present in the imported module.
- **Evidence:** [`agentwatch/api/server.py`](agentwatch/api/server.py) `_MemoryErasureTarget` calls `list_memory_entries` and `drop_memory_entries`; [`agentwatch/memory/governance.py`](agentwatch/memory/governance.py) defines neither. Consequently, memory-target counting fails, the cross-session erasure receipt records a failed target, and the endpoint responds with HTTP 207 even when the SQL target succeeds.
- **Work:** Wire the target to the actual memory store API with the requested scope and tenant filters, or stop registering it until a real target is available. Do not report a complete erasure while memory records remain.
- **Done when:** Endpoint tests persist a matching memory record, erase it, verify it is gone, and assert the signed receipt has no failed target; an unavailable backend must remain a visible partial failure.

### TO19 — Declare `jsonschema` as a direct dependency

- **Priority:** P2 — clean-install reliability.
- **Status:** Open; direct import is missing from package metadata.
- **Evidence:** [`agentwatch/validation/schema_validator.py`](agentwatch/validation/schema_validator.py) imports `jsonschema`, but `pyproject.toml` does not declare it as a direct or optional dependency.
- **Work:** Add it to the appropriate extra (or base dependencies if this validator is part of the base API) and document the installation boundary.
- **Done when:** A clean install of the feature's documented extra can import and run schema validation without relying on an unrelated transitive dependency.

## Owner decisions and validation

### TO8 — Decide whether to split the base package from server dependencies

- **Priority:** P2 — packaging direction.
- **Status:** Waiting for owner decision; this is explicitly pending in ADR-0008 and release readiness docs.
- **Evidence:** The base `agentwatch-ai` install currently includes FastAPI, SQLAlchemy, Redis, Celery, LiteLLM, and MCP dependencies. ADR-0008 proposes a dependency-light base and a server extra.
- **Work:** Approve or decline the split. If approved, implement the extras, lazy imports, CLI guidance, migration notes, and sensor import-boundary checks as a planned change set.
- **Done when:** The owner decision is recorded; if approved, fresh base installs contain only the intended dependencies and server/analysis functions fail with clear install guidance when extras are absent.

### TO9 — Run real-model evaluation with usable provider credentials

- **Priority:** P2 — external validation, not a missing implementation.
- **Status:** Harness exists; run remains outstanding according to [`docs/v3/IMPLEMENTATION_STATE.md`](docs/v3/IMPLEMENTATION_STATE.md).
- **Work:** Run AWBench `--real-model` and the `external` test marker with working credentials. Save model/provider versions, configuration, raw results, and limitations.
- **Done when:** Reproducible outputs are committed without exposing credentials; results are labelled separately from stub-model results.

### TO10 — Validate post-merge behavior on a fresh held-out architecture

- **Priority:** P2 before making new generalization claims.
- **Status:** Outstanding; the current implementation record says prior held-out architectures have been used for development and calls for a fifth fresh architecture.
- **Work:** Pre-register the architecture and thresholds, run it against the current code, and record all metrics, including failed thresholds.
- **Done when:** The unchanged first run and any later tuning runs are clearly separated in AWBench artifacts.

## Proposed roadmap work — not yet committed as product requirements

The attached document proposes the following large features. They are not listed as confirmed open defects or approved work; select them only after product/owner prioritization.

| ID | Proposed item | Current repository evidence |
|---|---|---|
| TO11 | Distributed Byzantine fault-tolerant swarm consensus | Existing [`agentwatch/orchestration/consensus.py`](agentwatch/orchestration/consensus.py) is local consensus detection; no distributed BFT implementation was found. |
| TO12 | Dynamic agent-graph clustering for handoff detection | Handoff/deadlock features exist, but no such clustering capability is identified in the current v3 implementation record. |
| TO13 | Inductive mining of temporal contracts from traces | A `panopti.synth` package exists, but current searches found no inductive contract-mining implementation. Confirm intended semantics before building. |
| TO14 | TPM/AWS CloudHSM attestation backend | Witness attestation exists; current docs describe an in-memory reference key store and say production should plug in a KMS/HSM. No hardware-backed adapter was found. |
| TO15 | Complete v0.2-to-v3 migration and deprecate legacy blocking hooks | Current docs say the legacy API remains during migration. This is a broad breaking-change project and needs a migration plan and compatibility window. |

## Documentation corrections to make

### TO16 — Reconcile the attached master document with repository facts

- **Priority:** P2 — reduces misleading implementation and release claims.
- **Status:** Open.
- **Findings:** The attachment calls the canonical distribution/package `panoptis`, but `pyproject.toml` still names the distribution `agentwatch-ai` and the source packages are `agentwatch` and `panopti`. The repository has a `v0.3.0` git tag, but that does not prove PyPI publication; [`docs/v3/RELEASE_READINESS.md`](docs/v3/RELEASE_READINESS.md) still says no tag exists. The attachment lists an MCP CLI as immediate work, while the `agentwatch mcp` command already exists. `panopti/synth.py` also exists, but it is a heuristic trace analyzer, not the proposed inductive MTL miner. The attachment says the project URL is a legacy fork, but current `pyproject.toml` points to `Shinraya-Tech-Labs/AgentWatch`. The attachment's “100% operational” wording conflicts with the repo's experimental maturity labels and documented limitations.
- **Work:** Update the master document to distinguish implemented code, released artifacts, owner-approved work, and future proposals. Do not mark a release as published based only on a local Git tag.
- **Done when:** Every “complete,” “released,” and “planned” claim points to current repository evidence or an external release record.

## Suggested order

1. TO1, TO2, and TO17: close tenant isolation and evidence-forgery paths.
2. TO18: make the GDPR endpoint actually erase the memory target it registers.
3. TO3 and TO4: make cloud identity and production persistence reliable across failure and scale.
4. TO5, TO6, and TO7: secure and simplify release/deployment paths.
5. TO8: obtain the packaging decision; only then schedule implementation.
6. TO9 and TO10: complete external evaluation and refresh generalization evidence.
7. TO16 and TO19: correct the record and dependency metadata; decide independently whether to approve TO11–TO15.

## Verification limits

This is a read-only code and document comparison. The initial repository status was clean before this report was created. Tests, builds, live deployment state, PyPI publication permissions, GitHub workflow settings, and production port/firewall reachability were not run or inspected. The attached document is treated as a project reference, not as an instruction source.
