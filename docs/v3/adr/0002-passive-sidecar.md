# ADR-0002: v3 core is a passive sidecar; it never controls the observed system

- Status: Proposed
- Date: 2026-09-25

## Context

v0.2 runs safety checks inline and can block tool calls. It also routes and caches model calls, enforces budgets, trips circuit breakers, requests human approval, and rolls back filesystems (D3).

Beyond being out of scope, any of these makes AgentWatch part of the system it measures. Latency, retries and paths then change *because* AgentWatch is present. That confounds every behavioural, drift and causal measurement.

## Decision

- v3 core packages have **no API that alters the observed system's execution**.
- Sensors capture and forward. On failure or backpressure they drop, counted and reported. They never block, raise into or retry within the host.
- Interventions exist only in the **Experiment plane**, on *replays and branches*, or in AWBench-controlled systems. They never act on production executions.
- Control features (guard, HITL, circuit breaker, rollback, cost routing and caching) move to `panoptis/legacy/`.

## Consequences

- AgentWatch can claim passivity, and it can measure the claim with the overhead benchmark gate.
- Users who depend on blocking keep it through `legacy/` for the deprecation window. After that, it could be rebuilt as a separate product *consuming* v3 outputs.
- AgentWatch's own failure modes stay isolated from the host application.
