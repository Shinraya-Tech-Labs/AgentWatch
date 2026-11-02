# AgentWatch --- Master Engineering Task List

> **Package rename (2026-11-02):** `main` renamed the package `agentwatch` → `panoptis` (commit `db95c31`). This branch was merged onto that layout in `4c2025d`; File paths below that start with `agentwatch/` now live under `panoptis/`.

> **Objective:** Complete AgentWatch as a real, end-to-end AI Agent
> Security & Runtime Protection Platform. This is an implementation and
> verification checklist, not a feature wishlist or presentation
> checklist.
>
> **Completion rule:** The repository is **not complete** because an AI
> agent says the tasks are done. It is complete only when every required
> task below has implementation evidence, automated test evidence,
> integration/E2E evidence where specified, documentation, and a clean
> final validation report.

## Project control rules

-   [x] **CTRL-001 --- Establish the authoritative working baseline.**
    Record repository URL, active branch, base commit, working-tree
    status, Python/Node versions, operating system, and available
    services. Preserve unrelated user changes.
-   [x] **CTRL-002 --- Keep implementation isolated.** Perform work on a
    dedicated feature branch. Do not commit directly to `main`; do not
    overwrite, reset, stash-drop, or edit unrelated user changes.
-   [ ] **CTRL-003 --- Build a task-to-evidence ledger.** For every
    task, record changed files, tests added, exact commands run,
    results, commit hash, and any remaining limitation.
-   [ ] **CTRL-004 --- Never mark a task complete from code inspection
    alone.** Require tests that exercise the behavior. For runtime
    claims, require an integration or E2E test that observes the actual
    tool execution.
-   [ ] **CTRL-005 --- Do not fake evidence.** No fabricated agent
    events, telemetry, benchmark numbers, screenshots, test results,
    security verdicts, or successful integrations.
-   [ ] **CTRL-006 --- Preserve honest status.** Use `NOT STARTED`,
    `IN PROGRESS`, `BLOCKED`, `FAILED`, or `DONE`. A task blocked by
    missing credentials, hardware, external services, or platform hooks
    must remain blocked with the exact reason and a reproducible
    alternative where possible.
-   [ ] **CTRL-007 --- Keep the task list current.** Update this file as
    work progresses. Do not delete unfinished tasks to make the project
    appear complete.
-   [ ] **CTRL-008 --- Final review.** Before declaring completion,
    inspect the complete diff, check for secrets, run all required
    validation, verify the working tree, and produce the final evidence
    report.

------------------------------------------------------------------------

# P0 --- Security integrity and trust boundaries

## TO-001 --- Eliminate fail-open tool execution — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-001)

-   [x] Trace `watch()` and every synchronous/asynchronous path through
    `agentwatch/core/watcher.py`, including `watcher._wrap`.
-   [x] Replace swallowed safety-check exceptions with a fail-closed
    outcome. A failed, unavailable, timed-out, or malformed security
    decision must never silently permit execution.
-   [x] Preserve the public exception contract: `ActionBlocked` must
    remain compatible with `AgentWatchBlockedError` handling.
-   [x] Ensure sync and async wrappers behave consistently.
-   [x] Ensure the original tool is invoked at most once; retries inside
    the security layer must not duplicate side effects.
-   [x] Add regression tests where the safety checker raises, times out,
    returns an invalid verdict, or cannot load policy.
-   [x] Add tests proving the wrapped tool's side-effect counter remains
    zero whenever enforcement fails.
-   [x] Add tests proving legitimate allowed calls still execute exactly
    once.
-   [x] Run existing watcher, adapter, and full Python test suites.

**Acceptance evidence:** Regression tests demonstrate that every
enforcement failure blocks execution and that allowed calls execute
exactly once.

## TO-002 --- Establish one authoritative enforcement pipeline — **IN PROGRESS** (boundary done; framework adapters and the MCP gateway adopt it in TO-011–016 and TO-023)

-   [x] Implement or complete the shared enforcement module under
    `agentwatch/protect/` (planned path:
    `agentwatch/protect/enforce.py`).
-   [x] Define a stable action representation containing agent/session
    identity, tool name, arguments, user objective/context, policy
    identity, and trace/evidence identifiers.
-   [x] Implement a single decision flow for `ALLOW`, `BLOCK`,
    `ESCALATE_HITL`, and `MUTATE_AND_ALLOW`.
-   [x] Run argument inspection and policy checks before execution.
-   [x] Apply approved argument mutation before execution and record the
    mutation.
-   [x] Execute the underlying tool exactly once only after an allow
    decision.
-   [x] Inspect and sanitize the tool result before returning it to the
    agent.
-   [x] Record decision, execution, approval, mutation, and
    output-sanitization events in the evidence system.
-   [x] Bind the enforcement context to the correct agent and session;
    reject mismatched or missing context according to policy.
-   [x] Expose a documented public `protect_tool()` API for protecting
    individual tools.
-   [ ] Ensure all integrations call this shared pipeline rather than
    reimplementing security decisions.
-   [x] Add unit, integration, sync, async, exception-path, and
    side-effect-count tests.

**Acceptance evidence:** One tested enforcement boundary is used by all
supported execution integrations. Tests prove ordering: inspect → decide
→ optional approval/mutation → execute once → inspect output → return.

## TO-003 --- Fix tenant identity and authorization — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-003)

-   [x] Audit every tenant-sensitive API route, storage operation,
    WebSocket path, background job, and evidence lookup.
-   [x] Stop treating `X-Tenant-ID` as authenticated identity by itself.
-   [x] Derive tenant identity from a validated authentication
    principal, signed credential, or trusted server-side mapping.
-   [x] If a tenant header is retained as a selector, verify it matches
    the authenticated principal's authorized tenant.
-   [x] Reject missing, malformed, expired, revoked, or cross-tenant
    credentials.
-   [x] Ensure tenant identity cannot be changed by request-body or
    header manipulation.
-   [x] Add tests for forged headers, mismatched tenant claims, missing
    credentials, revoked API keys, and cross-tenant reads/writes.
-   [x] Verify tenant scoping in REST, WebSocket, MCP, CLI, and
    background processing paths.

**Acceptance evidence:** Cross-tenant access attempts fail in automated
API/integration tests, including attempts to substitute another tenant's
header or identifier.

## TO-004 --- Make session erasure tenant-safe — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-004)

-   [x] Fix session-scope erasure in `agentwatch/api/server.py` (audit
    finding around line 1801) so the tenant constraint is part of the
    deletion query itself.
-   [x] Do not fetch by session ID and delete before checking tenant
    ownership.
-   [x] Apply the same ownership rule to related messages, traces,
    evidence, cached data, and derived records.
-   [x] Define and test behavior for unknown sessions and sessions owned
    by another tenant without leaking ownership information.
-   [x] Add two-tenant regression tests proving tenant A cannot erase
    tenant B's session or associated records.
-   [x] Verify deletion behavior for every supported erasure scope.

**Acceptance evidence:** A cross-tenant erase test proves zero records
belonging to the other tenant are modified or deleted.

## TO-005 --- Secure attestation keys and verification — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-005)

-   [x] Remove public/default HMAC secrets from production and
    cloud-mode defaults.
-   [x] Reject startup in production/cloud mode when required signing
    keys are absent, weak, known-default, or invalid.
-   [x] Define development-only key behavior explicitly and prevent it
    from being enabled accidentally in production.
-   [x] Load secrets through supported secret-management/environment
    configuration without logging them.
-   [x] Define key identifiers, rotation, versioning, and verification
    behavior.
-   [x] Ensure verification uses the correct key version and fails
    closed for unknown or revoked keys.
-   [x] Add tests for missing keys, default keys, wrong keys, modified
    payloads, modified signatures, rotation, and valid verification.
-   [x] Document key generation, configuration, rotation, backup, and
    incident response.

**Acceptance evidence:** Independent verification succeeds for valid
attestations and rejects tampered attestations and unsafe production
configuration.

## TO-006 --- Secure tenant and API-key persistence — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-006)

-   [x] Audit the in-memory tenant/API-key implementation and its
    current persistence contract.
-   [x] Implement durable storage using the project's supported database
    architecture.
-   [x] Persist tenant records, API-key metadata, revocation state,
    permissions, and required audit metadata.
-   [x] Store only securely hashed API-key material where practical;
    never persist or log plaintext secrets.
-   [x] Add migrations, indexes, uniqueness constraints, and transaction
    handling.
-   [x] Add restart tests proving tenants, keys, permissions, and
    revocations survive process restarts.
-   [x] Add concurrent create/revoke tests and database-failure tests.
-   [x] Increase tests for `api/tenant_auth.py`, currently reported at
    19% coverage.

**Acceptance evidence:** Integration tests against the configured
database prove persistence, revocation, and authorization behavior
across restarts.

## TO-007 --- Remove silent database-to-memory degradation — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-007)

-   [x] Audit the database connection fallback identified in the audit.
-   [x] Do not silently switch to in-memory mode when a production
    database connection fails.
-   [x] Define explicit development/test fallback configuration,
    disabled by default in production.
-   [x] Surface database unavailability through startup failure or a
    clear unhealthy state.
-   [x] Ensure security-sensitive writes cannot appear successful when
    they were not persisted.
-   [x] Add tests for unavailable database, failed transactions,
    reconnect, and explicit development fallback.
-   [x] Document operational behavior and recovery.

**Acceptance evidence:** Production-mode tests prove database failure
cannot silently create an ephemeral security state.

## TO-008 --- Protect the unauthenticated safety-check endpoint — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-008)

-   [x] Review `/api/v1/safety/check` and determine whether it must be
    public.
-   [x] Require authentication/authorization if it exposes custom policy
    matches or tenant-specific rule information.
-   [x] If a public check endpoint is intentionally retained, return
    only a deliberately limited response that does not reveal internal
    rule names, regexes, custom policy details, or sensitive
    diagnostics.
-   [x] Apply rate limits and bounded input size/complexity.
-   [x] Add tests for unauthenticated access, tenant isolation,
    information disclosure, abuse limits, and valid authorized use.
-   [x] Document the endpoint's intended trust model.

**Acceptance evidence:** Tests prove unauthorized callers cannot
enumerate private policy rules or obtain tenant-specific security
information.

## TO-009 --- Secure container and service exposure — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-009)

-   [x] Review Compose and deployment manifests for every published port
    and bind address.
-   [x] Remove unnecessary host-wide port exposure.
-   [x] Bind development-only services to loopback unless remote access
    is explicitly required.
-   [x] Secure Redis with authentication/ACLs and appropriate network
    isolation; do not expose unauthenticated Redis on all interfaces.
-   [x] Remove fixed/default credentials from Postgres and other
    services; use documented secret configuration.
-   [x] Define production-safe network boundaries, volumes, health
    checks, and least-privilege service users.
-   [x] Add configuration validation or security checks that detect
    unsafe exposed services.
-   [x] Document secure local and production deployment profiles.
-   [x] Validate with Compose configuration inspection and a deployment
    smoke test.

**Acceptance evidence:** Deployment checks prove Redis and database
services are not unintentionally exposed and cannot be accessed with
default credentials.

## TO-010 --- Add request and payload limits — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-010)

-   [x] Add configurable maximum HTTP request-body size.
-   [x] Add bounded observation-batch size, item count, string length,
    and nesting depth where relevant.
-   [x] Apply limits to upload, API, WebSocket, and batch ingestion
    paths as appropriate.
-   [x] Reject oversized requests with predictable error responses
    before expensive processing.
-   [x] Add tests for boundary-sized, oversized, deeply nested, and
    malformed requests.
-   [x] Document defaults and configuration.

**Acceptance evidence:** Automated tests prove oversized payloads are
rejected without unbounded memory/CPU use.

------------------------------------------------------------------------

# P1 --- Runtime interception and protection capabilities

## TO-011 --- Audit and enforce every framework adapter — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-011)

-   [x] Inventory all framework adapters and classify each as
    `PRE_EXECUTION_ENFORCEMENT`, `POST_EXECUTION_OBSERVATION`, or
    `UNSUPPORTED`.
-   [x] Trace how `watch()`, `GenericAdapter`, and framework attach
    functions reach actual tool execution.
-   [x] Route every adapter that claims prevention through TO-002's
    authoritative enforcement pipeline.
-   [x] Do not describe an observation-only adapter as blocking or
    controlling execution.
-   [x] Add per-adapter integration tests that execute a real test tool
    with a side-effect counter.
-   [x] For each supported adapter, prove a blocked call never reaches
    the underlying tool.
-   [x] Prove allowed calls execute once and results are returned
    correctly.
-   [x] Test sync/async execution, exceptions, cancellation, and
    streaming where supported.
-   [x] Update support matrix and documentation with actual enforcement
    boundaries.

**Acceptance evidence:** Each adapter marked as enforcing has an
integration test proving pre-execution blocking. Observation-only
integrations are explicitly labelled.

## TO-012 --- Implement genuine Claude Code pre-tool enforcement — **BLOCKED** (implemented and contract-tested; the real `claude -p` E2E needs the owner to sign the CLI in: evidence docs/IMPLEMENTATION_EVIDENCE.md#to-012)

-   [x] Replace or supplement output-stream-only observation with Claude
    Code's supported `PreToolUse` hook/permission mechanism.
-   [x] Ensure the hook invokes the authoritative enforcement decision
    before the CLI executes the tool.
-   [x] Implement correct allow/deny/approval behavior for supported
    hook responses.
-   [x] Inspect tool name and arguments; apply policy and risk checks.
-   [ ] Ensure a blocked tool cannot execute and cannot be relabelled as
    blocked after the fact.
-   [x] Capture post-tool output only where the supported lifecycle
    permits it; do not claim output sanitization if the integration
    cannot perform it.
-   [ ] Add an end-to-end test using a harmless local tool that writes a
    marker file; prove the marker is absent when blocked and present
    once when allowed.
-   [x] Document hook installation, permissions, limitations, and
    supported CLI versions.

**Acceptance evidence:** A real Claude Code hook test proves the blocked
command did not execute. Output-stream observation alone does not
satisfy this task.

## TO-013 --- Complete LangGraph enforcement — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-013-to-014-to-015)

-   [x] Trace the LangGraph adapter's actual tool execution lifecycle.
-   [x] Attach enforcement at a pre-tool execution boundary, not only at
    agent entry or event observation.
-   [x] Preserve tool schemas, state, callbacks, streaming, and
    return/error semantics.
-   [x] Add tests for allowed, blocked, escalated, mutated,
    failed-check, and sanitized-output cases.
-   [x] Include a real integration test with a deterministic test graph
    and side-effect counter.
-   [x] Document version compatibility and unsupported execution modes.

**Acceptance evidence:** LangGraph integration tests prove pre-execution
blocking and post-execution output handling.

## TO-014 --- Complete AutoGen enforcement — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-013-to-014-to-015)

-   [x] Trace AutoGen tool/function invocation and interception points.
-   [x] Route tool calls through TO-002 before the underlying function
    executes.
-   [x] Preserve supported AutoGen message, tool schema, and approval
    semantics.
-   [x] Add allowed, blocked, escalated, mutated, failure, and
    output-sanitization tests.
-   [x] Add a deterministic integration test with a side-effect counter.
-   [x] Document supported AutoGen versions and limitations.

**Acceptance evidence:** AutoGen integration tests prove blocked
functions never execute.

## TO-015 --- Complete smolagents enforcement — **DONE** (evidence: docs/IMPLEMENTATION_EVIDENCE.md#to-013-to-014-to-015)

-   [x] Trace the smolagents tool wrapping and invocation path.
-   [x] Ensure every supported tool invocation enters TO-002.
-   [x] Preserve tool metadata, input/output contracts, and exceptions.
-   [x] Add allowed, blocked, escalated, mutated, failed-check, and
    output-sanitization tests.
-   [x] Add a deterministic integration test with a side-effect counter.
-   [x] Document supported versions and limitations.

**Acceptance evidence:** smolagents integration tests prove blocked
tools never execute.

## TO-016 --- Complete generic and direct tool protection

-   [ ] Ensure `GenericAdapter` cannot bypass the enforcement boundary.
-   [ ] Provide and document `protect_tool()` for tools not managed by a
    framework adapter.
-   [ ] Define behavior for nested tools, dynamically registered tools,
    and tools registered after agent startup.
-   [ ] Prevent double-wrapping and duplicate execution.
-   [ ] Add tests for direct calls, nested calls, repeated registration,
    sync/async tools, and wrapper exceptions.
-   [ ] Provide an explicit compatibility mode for observation-only
    operation, visibly distinct from enforcement mode.

**Acceptance evidence:** Direct and generic tool calls are tested
against the same enforcement contract as framework adapters.

## TO-017 --- Complete pre-execution threat analysis

-   [ ] Inspect tool names, arguments, structured payloads, and relevant
    execution context.
-   [ ] Cover shell commands, Python execution, SQL, filesystem paths,
    URLs/network destinations, and other supported high-risk tool types.
-   [ ] Detect destructive operations, privilege escalation, credential
    exposure, suspicious command composition, and policy violations.
-   [ ] Use structured parsing/AST or domain-aware validation where
    appropriate; avoid relying only on brittle string matching.
-   [ ] Define risk scoring and policy thresholds with explainable
    reasons.
-   [ ] Ensure detection errors fail closed according to policy.
-   [ ] Add positive, negative, edge-case, and bypass-oriented tests for
    each detector family.
-   [ ] Record rule/policy version and decision rationale in evidence.

**Acceptance evidence:** Threat-analysis tests demonstrate both attack
detection and benign-content preservation, with documented
false-positive behavior.

## TO-018 --- Complete post-execution output inspection

-   [ ] Inspect tool results before they are returned to the agent.
-   [ ] Detect prompt-injection instructions in retrieved or
    tool-produced content.
-   [ ] Detect secrets and sensitive data in outputs.
-   [ ] Sanitize or withhold malicious content without destroying
    unrelated benign content.
-   [ ] Define behavior for structured, text, binary, oversized, and
    streaming results.
-   [ ] Ensure sanitized output---not the original unsafe output---is
    what the agent receives.
-   [ ] Add synthetic harmless fixtures for detection, sanitization,
    benign preservation, and false-positive tests.
-   [ ] Add integration tests that inspect the exact value returned to
    the agent.
-   [ ] Record sanitization actions in evidence without storing
    unnecessary secrets.

**Acceptance evidence:** Tests prove malicious content is
removed/withheld before reaching the agent and benign content remains
usable.

## TO-019 --- Complete human-in-the-loop approvals

-   [ ] Define approval request identity, context, expiry, and one-time
    use.
-   [ ] Bind approvals to the exact agent, session, tool, arguments, and
    policy decision.
-   [ ] Prevent approval replay or reuse after arguments change.
-   [ ] Support approve, reject, timeout, cancellation, and
    unavailable-approver outcomes.
-   [ ] Ensure no tool executes before a valid approval.
-   [ ] Record approval actor, timestamp, decision, and bound action in
    evidence.
-   [ ] Add API/UI and integration tests for the full approval
    lifecycle.

**Acceptance evidence:** End-to-end tests prove execution occurs only
after a valid approval for the exact requested action.

## TO-020 --- Complete semantic loop and stuck-agent detection

-   [ ] Track the last 10 tool calls per session, with configurable
    bounds.
-   [ ] Detect exact repeated tool/argument calls at the defined
    threshold (initial target: 3 repeats).
-   [ ] Detect oscillation patterns such as A→B→A→B across two or three
    tools.
-   [ ] Detect repeated failures with small argument/command edits using
    a documented similarity method and threshold (initial target:
    similarity \>0.80).
-   [ ] Integrate loop decisions with the circuit breaker and runtime
    control policy.
-   [ ] Distinguish productive polling from stuck polling using
    observable state change or progress evidence.
-   [ ] Avoid treating different meaningful arguments as duplicates
    without supporting evidence.
-   [ ] Add synthetic tests for repeated identical calls, A/B
    oscillation, edited failing retries, productive polling, and
    legitimate repeated operations.
-   [ ] Add session-isolation and memory-bound tests.
-   [ ] Document thresholds, limitations, and false-positive handling.

**Acceptance evidence:** The complete loop test matrix passes, including
a productive-polling case that must not be flagged.

## TO-021 --- Complete intent grounding

-   [ ] Capture or accept the user's stated objective and relevant
    authorized context.
-   [ ] Compare each proposed tool/action and arguments against that
    objective.
-   [ ] Implement entity/token/operation matching as evidence, not as
    sole proof of intent.
-   [ ] Detect destructive actions under read-only objectives and
    unrelated tool calls.
-   [ ] Support ambiguous objectives through escalation or explicit
    policy behavior.
-   [ ] Use optional model verification only as a secondary signal with
    bounded, explainable behavior.
-   [ ] Do not let similarity alone authorize a risky action.
-   [ ] Add tests for aligned, unrelated, destructive, ambiguous, and
    adversarially phrased actions.
-   [ ] Record grounding rationale and confidence in the evidence trail.

**Acceptance evidence:** Tests prove destructive actions inconsistent
with read-only objectives are blocked/escalated while legitimate aligned
actions remain usable.

## TO-022 --- Complete per-agent RBAC

-   [ ] Define agent roles and an explicit allowed/forbidden tool
    matrix.
-   [ ] Support argument restrictions and resource constraints for
    sensitive tools.
-   [ ] Add per-agent/session rate limits where policy requires.
-   [ ] Define default-deny behavior and explicit configurable
    exceptions.
-   [ ] Validate policy schemas and reject invalid or ambiguous
    policies.
-   [ ] Support safe policy reload without a window of unintended
    authorization.
-   [ ] Bind authorization to authenticated agent identity and tenant.
-   [ ] Add tests for allowed/forbidden tools, restricted arguments,
    rate limits, default deny, invalid policy, reload, and tenant
    boundaries.
-   [ ] Expose effective permissions and denial reasons in the
    dashboard/API.

**Acceptance evidence:** RBAC integration tests prove unauthorized tool
names and arguments are rejected before execution.

## TO-023 --- Build standalone MCP gateway/proxy

-   [ ] Implement a standalone MCP gateway/proxy using the supported
    project architecture.
-   [ ] Support stdio transport.
-   [ ] Support the relevant HTTP transport(s) required by the supported
    MCP protocol/version.
-   [ ] Correctly parse and preserve JSON-RPC messages, IDs, errors,
    notifications, initialization, capabilities, and tool schemas.
-   [ ] Intercept `tools/call` before forwarding it upstream.
-   [ ] Route tool name and arguments through TO-002.
-   [ ] Forward only allowed/approved/mutated calls.
-   [ ] Inspect and sanitize tool responses before returning them to the
    client.
-   [ ] Handle malformed messages, oversized payloads, disconnects,
    timeouts, and upstream failures safely.
-   [ ] Add redaction, tenant/session binding, rate limits, and evidence
    recording.
-   [ ] Provide CLI commands/configuration for starting and inspecting
    the gateway.
-   [ ] Add protocol-level unit tests and end-to-end tests with a local
    fake MCP server/client.
-   [ ] Document setup, transport support, security model, and
    limitations.

**Acceptance evidence:** An end-to-end MCP client → AgentWatch gateway →
test server scenario proves a blocked `tools/call` never reaches the
upstream server and unsafe output is sanitized before returning.

## TO-024 --- Build local agent and MCP discovery

-   [ ] Discover supported local agent runtimes using documented process
    and configuration inspection.
-   [ ] Discover relevant MCP configuration files, sockets, and
    listening ports using safe, bounded methods.
-   [ ] Identify known runtimes and distinguish `MONITORED`,
    `UNMONITORED`, `UNKNOWN`, and `UNSUPPORTED`.
-   [ ] Avoid assuming that a discovered process is protected merely
    because it is detected.
-   [ ] Show discovery source, timestamp, confidence, and monitoring
    status.
-   [ ] Define permission boundaries and avoid collecting unrelated
    process data or secrets.
-   [ ] Do not automatically kill or modify discovered processes.
-   [ ] Add safe lifecycle controls only where explicitly supported and
    authorized.
-   [ ] Add tests using fixture processes/configurations and tests for
    malformed or inaccessible sources.
-   [ ] Document platform-specific support and limitations.

**Acceptance evidence:** Discovery tests prove supported agents are
identified accurately and unmonitored agents are never represented as
protected.

## TO-025 --- Complete runtime controls and emergency stop

-   [ ] Verify kill switch behavior across all supported execution
    paths.
-   [ ] Ensure kill switch state is shared correctly across
    processes/instances where required.
-   [ ] Ensure suspension and resume operations are authorized,
    auditable, and race-safe.
-   [ ] Define behavior for in-flight calls and pending approvals during
    emergency stop.
-   [ ] Ensure emergency stop blocks new actions before tool execution.
-   [ ] Add concurrency, restart, multi-process, and failure-path tests.
-   [ ] Expose status and authorized controls through API/CLI/dashboard.

**Acceptance evidence:** Integration tests prove the kill switch blocks
new tool execution across the supported runtime topology.

------------------------------------------------------------------------

# P2 --- Evidence, API, dashboard, and operational platform

## TO-026 --- Verify append-only evidence and Merkle integrity

-   [ ] Audit the existing v3 evidence store and all write/read paths.
-   [ ] Verify append-only behavior and detect unauthorized mutation,
    deletion, reordering, or truncation.
-   [ ] Verify Merkle proof/root generation and independent
    verification.
-   [ ] Bind evidence to tenant, agent, session, action, policy version,
    and relevant timestamps.
-   [ ] Ensure evidence records distinguish observed, allowed, blocked,
    escalated, mutated, executed, and sanitized events.
-   [ ] Define retention, export, and tenant-scoped erasure behavior
    without silently breaking integrity guarantees.
-   [ ] Add tampering tests for modified payloads, missing entries,
    reordered entries, and invalid proofs.
-   [ ] Add an independent verification command or documented verifier.
-   [ ] Avoid recording plaintext secrets or unnecessary sensitive
    content.

**Acceptance evidence:** An independent verifier accepts valid evidence
and rejects tampered or incomplete evidence.

## TO-027 --- Complete API integration

-   [ ] Expose the authoritative protection decisions and runtime events
    through versioned API routes.
-   [ ] Implement authenticated, tenant-scoped endpoints for agents,
    policies, approvals, loops, intent decisions, MCP, discovery,
    evidence, and emergency controls as applicable.
-   [ ] Validate request and response schemas.
-   [ ] Apply authorization, rate limits, request-size limits, and safe
    error handling consistently.
-   [ ] Ensure API responses do not expose secrets, internal regexes, or
    other tenants' information.
-   [ ] Add API integration tests for success, denial, malformed input,
    tenant isolation, and service failure.
-   [ ] Document endpoint contracts and authentication.

**Acceptance evidence:** API integration tests exercise the real
protection pipeline and verify tenant isolation.

## TO-028 --- Complete CLI

-   [ ] Provide commands to inspect status, agents, policies, approvals,
    evidence, MCP gateway, discovery, and emergency controls.
-   [ ] Provide a command to run evidence verification.
-   [ ] Provide safe configuration validation and diagnostics.
-   [ ] Ensure commands use the same authorization and policy rules as
    the API.
-   [ ] Avoid printing secrets or claiming a runtime is protected when
    it is only discovered.
-   [ ] Add CLI tests for valid use, invalid configuration,
    authorization failure, and service unavailability.
-   [ ] Document installation and command examples.

**Acceptance evidence:** CLI smoke tests run against a real local test
deployment and verify returned state.

## TO-029 --- Complete the security dashboard

-   [ ] Provide a truthful security overview using actual backend data.
-   [ ] Provide agent inventory and clear monitored/unmonitored status.
-   [ ] Provide runtime protection status and recent
    allow/block/escalate decisions.
-   [ ] Provide policy/RBAC management views.
-   [ ] Provide MCP gateway and discovery views.
-   [ ] Provide loop and intent-grounding events.
-   [ ] Provide human approval workflows.
-   [ ] Provide evidence/Merkle verification views.
-   [ ] Provide authorized emergency-stop controls.
-   [ ] Include loading, empty, stale, error, and disconnected states.
-   [ ] Ensure all displayed data is tenant-scoped and
    permission-checked.
-   [ ] Do not use fabricated events or static values presented as live
    telemetry.
-   [ ] Add frontend unit, component, accessibility, and browser E2E
    tests for critical flows.
-   [ ] Verify type-check, lint, and production build.

**Acceptance evidence:** Browser E2E tests operate against a running
backend and prove that displayed decisions and controls correspond to
real backend state.

## TO-030 --- Fix dependency and package integrity

-   [ ] Declare `jsonschema` in the correct package/dependency group if
    it is required; otherwise remove the undeclared import and use a
    supported alternative.
-   [ ] Verify every runtime import is declared in packaging metadata.
-   [ ] Verify both `agentwatch/` and `panopti/` are included and tested
    as shipped.
-   [ ] Check wheel and source distribution contents.
-   [ ] Test clean installation from built wheel and source distribution
    in isolated environments.
-   [ ] Resolve or explicitly document dependency advisories with
    severity, reachability, affected versions, and mitigation.
-   [ ] Update dashboard `next` from the audited 14.2.30 version to a
    verified patched supported version; rerun all frontend checks and
    E2E tests.
-   [ ] Pin or constrain dependencies reproducibly and review transitive
    dependency changes.

**Acceptance evidence:** Clean-install tests pass and dependency audit
output is attached to the release evidence.

## TO-031 --- Repair memory-erasure integration

-   [ ] Fix the missing function import identified in the memory-erasure
    target (TO18 in the existing task list).
-   [ ] Verify the intended function exists, is exported, and has the
    expected semantics.
-   [ ] Add tests for successful erasure, missing target, authorization
    failure, and tenant isolation.
-   [ ] Verify erasure does not bypass evidence-retention or integrity
    rules.
-   [ ] Document the supported erasure behavior.

**Acceptance evidence:** The memory-erasure path executes successfully
in integration tests and rejects unauthorized cross-tenant requests.

## TO-032 --- Strengthen CI and static security checks

-   [ ] Make CI lint all shipped Python packages, including
    `agentwatch/` and `panopti/`.
-   [ ] Resolve the 49 repo-wide Ruff findings or document narrowly
    justified suppressions.
-   [ ] Add/retain type checking and coverage gates.
-   [ ] Make dependency auditing actionable; do not silently configure
    `pip-audit` to always succeed.
-   [ ] Add a maintained static security scanner (for example Bandit or
    Semgrep) if compatible with the repository and CI policy.
-   [ ] Add checks for secrets, unsafe configuration, dependency drift,
    and packaging errors.
-   [ ] Ensure security-critical tests run on pull requests.
-   [ ] Set meaningful coverage thresholds for security-sensitive
    modules, including tenant auth and adapters.
-   [ ] Test CI workflow behavior for fork pull requests and
    least-privilege permissions.
-   [ ] Ensure CI checks the exact code that will be released.

**Acceptance evidence:** A clean CI run demonstrates lint, type, tests,
coverage, package checks, and configured security checks all pass on the
release candidate.

## TO-033 --- Secure release and publishing workflows

-   [ ] Consolidate duplicate GitHub Release creation workflows or
    define a single authoritative release path.
-   [ ] Require green CI and the full required test suite before PyPI
    publication.
-   [ ] Ensure `v*` tags cannot publish untested artifacts.
-   [ ] Replace any placeholder/echo-only integrity validation with real
    verification.
-   [ ] Verify build provenance, package metadata, artifact contents,
    and signatures/attestations where supported.
-   [ ] Restrict workflow permissions to least privilege.
-   [ ] Audit fork-PR comment/report workflows for cross-PR write or
    comment abuse.
-   [ ] Add release dry-run tests and document rollback/yank procedures.
-   [ ] Verify the published artifact matches the tested commit and
    build.

**Acceptance evidence:** A release-candidate workflow refuses
publication when tests or required checks fail and publishes only the
verified artifact.

## TO-034 --- Architecture and dead-code review

-   [ ] Map runtime entry points, API routes, CLI commands, adapters,
    dashboard calls, package exports, and tests.
-   [ ] Review the reported approximately 54 importable but unreachable
    modules (\~6,500 lines).
-   [ ] Classify each module as required library API, internal
    dependency, experimental, dead code, or undocumented feature.
-   [ ] Do not delete modules solely because a simple static
    reachability scan cannot find dynamic imports or plugin entry
    points.
-   [ ] Remove genuinely dead code or wire required functionality into
    documented entry points.
-   [ ] Consolidate duplicated subsystems only where behavior and
    compatibility are understood.
-   [ ] Establish dependency direction and avoid circular or duplicated
    enforcement logic.
-   [ ] Add tests before removing or consolidating behavior.
-   [ ] Update package exports and documentation.

**Acceptance evidence:** The architecture map accounts for the reviewed
modules and every retained public capability has a supported entry point
or explicit internal purpose.

------------------------------------------------------------------------

# P3 --- Documentation, benchmarks, and presentation readiness

## TO-035 --- Correct product claims and support matrix

-   [ ] Correct the README contradiction about whether v3 blocks or
    controls agents.
-   [ ] Distinguish prevention, observation, detection, alerting, and
    post-execution labelling.
-   [ ] Correct the "40+ critical patterns" claim to match the actual
    verified pattern inventory (audit observed 15 built-in patterns, 5
    blocking by default; re-count after implementation).
-   [ ] Publish an adapter support matrix showing enforcement stage and
    limitations for every integration.
-   [ ] Document what happens when policy evaluation fails.
-   [ ] Document security boundaries, threat model, non-goals,
    deployment assumptions, and known limitations.
-   [ ] Ensure every security claim links to a test, benchmark, or
    reproducible procedure.
-   [ ] Remove claims that cannot be demonstrated by the current
    release.

**Acceptance evidence:** Documentation review maps every product claim
to implementation and verification evidence.

## TO-036 --- Build reproducible security evaluation suite

-   [ ] Create a versioned, harmless synthetic test corpus for prompt
    injection, secret exposure, dangerous tool arguments, policy
    violations, loops, and intent mismatch.
-   [ ] Include benign controls to measure false positives and content
    preservation.
-   [ ] Keep test fixtures clearly labelled as defensive tests; do not
    download external datasets or run third-party code without explicit
    authorization.
-   [ ] Define metrics: detection rate, false-positive rate, block rate,
    missed-attack rate, sanitization correctness, and decision latency.
-   [ ] Report results by threat category and integration.
-   [ ] Record hardware, software versions, configuration, seeds, and
    exact commands.
-   [ ] Ensure benchmarks are repeatable and distinguish synthetic
    results from external dataset results.
-   [ ] Do not claim competitor superiority without a fair, comparable,
    independently reproducible evaluation.

**Acceptance evidence:** A reproducible benchmark report includes
methodology, raw results, limitations, and false-positive measurements.

## TO-037 --- Measure runtime overhead and reliability

-   [ ] Benchmark allow, block, escalation, mutation, and
    output-sanitization paths.
-   [ ] Measure median and tail latency, throughput, memory use, and
    behavior under concurrency.
-   [ ] Measure overhead with and without evidence recording where
    configuration permits.
-   [ ] Test timeouts, cancellation, upstream failures, and degraded
    dependencies.
-   [ ] Publish raw benchmark data and reproduction commands.
-   [ ] Define performance targets based on measured product
    requirements rather than invented numbers.

**Acceptance evidence:** Performance report is generated from
reproducible runs on identified hardware/software.

## TO-038 --- Create end-to-end product walkthrough

-   [ ] Create a clean local deployment or documented reproducible
    environment.
-   [ ] Demonstrate a real agent/tool call that is allowed.
-   [ ] Demonstrate a dangerous action blocked before execution using a
    side-effect marker.
-   [ ] Demonstrate a HITL action that executes only after approval.
-   [ ] Demonstrate a malicious tool output sanitized before it reaches
    the agent.
-   [ ] Demonstrate a loop detected and circuit breaker response.
-   [ ] Demonstrate an intent mismatch blocked or escalated.
-   [ ] Demonstrate RBAC denial.
-   [ ] Demonstrate an MCP call intercepted by the gateway.
-   [ ] Demonstrate agent discovery with monitored versus unmonitored
    status.
-   [ ] Demonstrate evidence/Merkle verification and tamper rejection.
-   [ ] Demonstrate tenant isolation with two test tenants.
-   [ ] Capture genuine screenshots/logs from the running product.
-   [ ] Provide setup, run, reset, and troubleshooting instructions.

**Acceptance evidence:** A reviewer can reproduce the walkthrough from a
clean checkout using the documented commands. Screenshots and logs
correspond to that run.

------------------------------------------------------------------------

# Final release gate --- End-to-end definition of DONE

## TO-039 --- Full repository validation

-   [ ] Run the complete Python test suite, including all new `protect/`
    tests and all existing package tests.
-   [ ] Run all frontend unit/component tests.
-   [ ] Run browser E2E tests against the running backend.
-   [ ] Run integration tests for every supported framework adapter.
-   [ ] Run MCP protocol and gateway E2E tests.
-   [ ] Run tenant isolation, erasure, attestation, RBAC, approval,
    kill-switch, and evidence-integrity tests.
-   [ ] Run lint and type checks across all shipped packages.
-   [ ] Run package build and clean-install tests.
-   [ ] Run dependency and configured security scans.
-   [ ] Run production frontend build.
-   [ ] Run deployment/Compose security checks.
-   [ ] Record exact commands, exit codes, totals, skips, failures,
    coverage, and environment details.
-   [ ] Fix failures; do not hide, skip, xfail, or weaken tests merely
    to obtain a green run.
-   [ ] Explain every skip and every test that could not run.

**Acceptance evidence:** A complete validation report for the exact
release-candidate commit, with no unexplained failures.

## TO-040 --- Final code, security, and release review

-   [ ] Review the full diff from the recorded base commit.
-   [ ] Confirm no unrelated user changes were modified or discarded.
-   [ ] Scan for credentials, tokens, private keys, generated secrets,
    and sensitive test data.
-   [ ] Verify all critical/high audit findings are fixed or explicitly
    documented as release-blocking.
-   [ ] Verify all task statuses against their evidence.
-   [ ] Verify docs and support claims match tested behavior.
-   [ ] Verify release artifacts correspond to the tested commit.
-   [ ] Record final branch, commit hash, working-tree status, and
    release artifact checksums.
-   [ ] Produce `docs/IMPLEMENTATION_EVIDENCE.md` (or equivalent) with
    task IDs, changed files, test commands/results, screenshots/log
    references, benchmarks, and known limitations.
-   [ ] Produce a concise final report with completed tasks, blocked
    tasks, unresolved risks, and exact reproduction instructions.
-   [ ] Only declare the repository complete when all required tasks are
    `DONE` and the final release gate passes.

**Acceptance evidence:** A reviewer can check every `DONE` task against
concrete evidence and independently reproduce the critical end-to-end
security claims.

------------------------------------------------------------------------

# Final AI-agent completion contract

An AI coding agent must follow these rules before reporting "all tasks
completed":

1.  Read this entire file and the existing repository task list before
    editing.
2.  Inspect the actual repository and current branch; never assume a
    task is already done because a similarly named file exists.
3.  For each task, implement the behavior, add/adjust tests, run the
    relevant tests, and record evidence.
4.  Never mark a task `DONE` because code was generated, imports
    succeeded, a mock passed, or documentation was written.
5.  For any claim that an action is blocked, test the real execution
    boundary and prove the underlying side effect did not occur.
6.  For every integration, test the actual framework/protocol path; a
    unit test of a helper is not sufficient.
7.  Do not fabricate test output, screenshots, metrics, benchmark
    results, or citations.
8.  Do not silently skip tests, reduce coverage requirements, weaken
    assertions, or change security defaults to make CI green.
9.  Do not install/run third-party competitor software or download
    external datasets unless explicitly authorized.
10. Do not commit to `main`, overwrite unrelated changes, or claim a
    commit succeeded unless Git confirms it.
11. If blocked, record the exact blocker, attempted commands, output,
    and what access/input is required. Leave the task `BLOCKED`.
12. At the end, report every task ID and status. If even one required
    task is not `DONE`, report **NOT COMPLETE**.

## Required final report format

-   Base commit and implementation commit:
-   Branch:
-   Working-tree status:
-   Tasks completed / total:
-   Tasks blocked:
-   Tasks failed:
-   Python test results:
-   Frontend test results:
-   Browser E2E results:
-   Adapter integration results:
-   MCP E2E results:
-   Tenant/security regression results:
-   Lint/type-check results:
-   Dependency/security scan results:
-   Package build/clean-install results:
-   Coverage:
-   Benchmark report:
-   Walkthrough evidence:
-   Remaining risks and limitations:
-   Exact reproduction commands:

> **Definition:** "Repository complete" means the implementation, tests,
> integrations, deployment, documentation, and evidence all work
> together end to end on the same verified release-candidate commit. A
> green unit-test suite or an AI-generated completion summary alone is
> not sufficient.
