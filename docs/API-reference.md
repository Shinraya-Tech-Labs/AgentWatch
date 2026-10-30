# AgentWatch API and SDK Reference

Welcome to the central reference for the AgentWatch observability, safety, and reliability ecosystem. This document serves as the single source of truth for interacting with AgentWatch, whether you are utilizing the Python SDK directly inside your agent frameworks or communicating via our REST API endpoints.

---

## Response Conventions

This section describes what the API does today, so a new route has something to conform to. It is a
description, not a proposal — everything below was checked against the running application, including
the parts that are inconsistent.

### Success responses have no envelope

Payloads are returned at the top level. There is no `data` wrapper, no `success` flag, no metadata
block.

```http
GET /api/v1/dashboard/summary
200 OK

{
  "total_sessions": 12,
  "active_sessions": 3,
  "blocked_sessions": 1,
  "estimated_cost_usd": 0.42
}
```

A new route should return its payload directly and let the status code carry the success/failure
signal.

Five routes declare a `response_model` (`SessionListResponse`, `PruneResponse`, `TraceResponse`,
`ConfidenceResponse`, `SafetyCheckResponse`); the other thirty return plain dictionaries or
`model_dump()` output. Declaring a `response_model` is preferable for anything new — it validates the
response, and it is what populates the OpenAPI schema that clients generate from.

### Error responses come in three shapes

This is the part worth knowing before you write a client, because **a single error parser will not
work**.

**Most errors** — 400, 404, 500 — use FastAPI's default handler, where `detail` is a string:

```http
GET /api/v1/sessions/does-not-exist
404 Not Found

{ "detail": "Session does-not-exist not found" }
```

**Validation errors** (422) reuse the same key with a *different type*. `detail` is a list of objects,
not a string:

```http
GET /api/v1/sessions?limit=9999
422 Unprocessable Entity

{
  "detail": [
    {
      "type": "less_than_equal",
      "loc": ["query", "limit"],
      "msg": "Input should be less than or equal to 200"
    }
  ]
}
```

**Rate limiting** (429) uses a different key entirely — `error`, not `detail`:

```http
429 Too Many Requests

{ "error": "rate_limit_exceeded" }
```

That last one comes from the custom handler in `server.py`; the other two are FastAPI's defaults.

So a client reading errors has to check `detail` as a string, `detail` as a list, and `error`. That is
the current contract. It is worth documenting precisely because it is the sort of thing that gets
harder to change the more clients depend on it — and any consolidation would be a breaking change, so
it belongs in a deliberate decision rather than a drive-by fix.

### Status codes in use

| code | meaning | used by |
|---|---|---|
| `200` | success | all successful reads and writes |
| `400` | malformed request the schema can't catch | 3 routes |
| `404` | the named resource does not exist | 10 routes |
| `422` | request failed schema validation | raised automatically by FastAPI |
| `429` | rate limit exceeded | the rate-limit middleware |
| `500` | unhandled server-side failure | 2 routes |

There is no `201` on creation and no `204` on deletion — creates and deletes return `200` with a body.
Match that rather than introducing a new convention in a single route.

### Pagination

List endpoints take a `limit` query parameter with a server-side ceiling, and nothing else:

| endpoint | default | max |
|---|---|---|
| `GET /api/v1/sessions` | 50 | 200 |
| `GET /api/v1/sessions/{id}/events` | 500 | 2000 |
| `GET /api/v1/safety/blocked` | 50 | 200 |

**There is no `offset`, no `page` and no cursor.** Once a collection exceeds the ceiling, the rest of it
is not reachable through the API. Exceeding the maximum is a `422`, not a silent clamp.

If you add a list endpoint, follow the `limit` + `le=` pattern so the behaviour stays predictable. If
you need to page beyond the ceiling, that is a design change worth raising as an issue rather than
solving locally in one route.

### Rate-limit headers

Every response — including successful ones and the `429` itself — carries four headers:

```
X-RateLimit-User-Limit        X-RateLimit-User-Remaining
X-RateLimit-Global-Limit      X-RateLimit-Global-Remaining
```

Per-user and global budgets are tracked separately, so a client can be rejected by the global limit
while its own budget is untouched. Clients should read both.


## Python SDK Reference

To start monitoring your AI agents, make sure you import the core functions from the root package:

'''python
import panoptis'''

Core SDK Methods
1. panoptis.watch
What it does: This is the primary entry point for AgentWatch. It dynamically attaches monitoring hooks to an active AI agent instance. Once wrapped, it automatically streams execution trace events, logs performance metrics, and applies active safety policies in real time.

Returns: The monitored, safety wrapped agent instance.

2. panoptis.detect_framework
What it does: Inspects a live runtime object to identify which orchestration library is being used behind the scenes.

Supported Frameworks: Dynamically identifies popular frameworks like LangChain, CrewAI, and AutoGen.

Returns: A reference to the identified framework adapter class.

3. panoptis.detect_framework_label
What it does: A helper method that identifies the running agent framework and returns a clean, human readable string such as langchain, crewai, or autogen.

Returns: A string label.

4. panoptis.GenericAdapter
What it does: The base adapter class used for parsing agent lifecycles. If you are running an in house or unsupported agent framework, you can subclass GenericAdapter to build custom telemetry parsing logic.

SDK Exceptions
panoptis.AgentWatchBlockedError
What it does: This exception is instantly raised at runtime if an agent action, reasoning step, or tool invocation violates your active safety guardrails, such as attempting an illegal file system command.

REST API Endpoints
Authentication and Security Governance
The API uses three clear authentication and authorization mechanisms depending on your account setup and environment settings.

API Key Access
For standard automated client telemetry tracking data ingestion:

Header Key: X-Api-Key

Value: Your secret API key string

SAML SSO and Role Based Access Control
For dashboard interactions and operational endpoints when the SAML secret configuration is active:

Header Key: Authorization

Value: Bearer your_saml_session_token_here

Premium Feature Entitlements
Certain advanced endpoints require a premium tier verification token to validate your software license configuration and avoid bypass attempts:

Header Key: X-Entitlement-Token

Value: Your cryptographically signed license entitlement string

Optional Header Key: X-Machine-Id (A text-based unique identifier string passed along as a basic machine validation sanity check)

Role Hierarchy Enforcements
When SAML single sign on is enabled, security profiles are categorized and ordered by minimum privileges:

Viewer tier

Operator tier

Admin tier

Owner tier

System and Health Metrics
Get System Status
Endpoint: GET /api/v1/system/status

Description: Retrieves the underlying system configuration, including database connectivity mode such as persistent and the running environment.

Requires Auth: Yes

Service Health Check
Endpoint: GET /health

Description: A quick, publicly accessible health check used by load balancers and uptime monitors to ensure the server, database connections, and telemetry loops are operating smoothly.

Example Response:

JSON
{
  "status": "ok",
  "version": "0.2.0",
  "timestamp": "2026-07-09T11:15:00Z",
  "database_connected": true
}
Prometheus Operational Metrics
Endpoint: GET /metrics

Description: Exposes raw application runtime performance and scraping metrics formatted specifically for Prometheus tracking instances.

Session and Ingestion Telemetry
List Agent Sessions
Endpoint: GET /api/v1/sessions

Description: Fetches a paginated history of monitored agent execution tracks.

Optional Query Filters:

limit: integer, default is 50, maximum is 200

framework: string to filter by specific framework type

status: string to filter by execution state such as active, completed, or blocked

since_hours: integer lookback window for sessions

Ingest a New Session
Endpoint: POST /api/v1/sessions

Description: Pre-registers an agent execution trace context block tracker before running tracking sequences.

Fetch Session Details
Endpoint: GET /api/v1/sessions/{session_id}

Description: Grabs complete high level summary logs and metadata for a single target session.

Retrieve Trace Events
Endpoint: GET /api/v1/sessions/{session_id}/events

Description: Returns the granular timeline of events belonging to a session.

Optional Query Filters:

event_type: string to isolate events like tool calls, model outputs, or errors

limit: integer, default is 500

Stream Raw Trace Events
Endpoint: POST /api/v1/events

Description: Main payload ingestion pipeline. Accepts fine grained agent updates like tool payloads, reasoning steps, or pricing metrics.

Data Pruning
Endpoint: DELETE /api/v1/sessions/prune

Description: Helps manage disk space by removing historical tracing data older than a designated timeframe.

Query Parameters:

older_than_hours: integer, required

dry_run: boolean, default is false

Ingestion Engine Metrics
Endpoint: GET /api/v1/ingestion/metrics

Description: Retrieves real time data processing performance, throughput rates, and backlog statistics from the live telemetry ingestion engine pipeline.

Advanced Analysis and Evaluation Tools
Evaluate Session Traces
Endpoint: GET /api/v1/sessions/{session_id}/trace

Description: Extracts raw step by step latency, path sequencing, and execution flows.

Trace Session Confidence Scoring
Endpoint: GET /api/v1/sessions/{session_id}/confidence

Description: Analyzes agent actions against expected alignment goals to discover hidden anomalies.

Evaluate Agent Reasoning
Endpoint: GET /api/v1/sessions/{session_id}/reasoning

Description: Evaluates the context, system prompts, and logic chains utilized by the large language model during execution.

Budget and Cost Allocation Logs
Endpoint: GET /api/v1/sessions/{session_id}/cost

Description: Tracks total financial spend and token distribution profiles tied to a specific session run.

Counterfactual Scenario Replay
Endpoint: GET /api/v1/sessions/{session_id}/replay

Description: Prepares the system state to interactively review and run testing adjustments based on past failures.

Simulate Experiment Trials
Endpoint: POST /api/v1/sessions/{session_id}/simulate

Description: Runs offline execution alternatives against a past session path to check for performance variance.

Guardrails and Safety Management
Enumerate Git or File State Checkpoints
Endpoint: GET /api/v1/sessions/{session_id}/checkpoints

Description: Looks up isolated filesystem checkpointing logs captured during runtime tracking.

Trigger State Rollback
Endpoint: POST /api/v1/sessions/{session_id}/rollback

Description: Automatically rolls back file adjustments and system actions to reverse unwanted or destructive agent behaviors.

Command Execution Safety Check
Endpoint: POST /api/v1/safety/check

Description: Validates terminal commands and scripts inside an offline simulation layer to catch threats before real execution occurs.

Manage Active Safety Policies
GET /api/v1/safety/policy — Requires `policy:read`; returns the active policy.
PUT /api/v1/safety/policy — Requires `policy:write`; updates the active policy.

Real-Time Data Streaming WebSockets
Live Event Ingestion Feed
Endpoint: WS /ws/events

Description: Establishes a persistent, low-latency WebSocket connection used to push raw, real-time agent framework activities directly to attached browser visualization dashboards.

How to Authenticate:

For Browser Clients: Pass your API token directly as a query parameter in the connection URL: ws://<host>/ws/events?api_key=your_api_key_here

For Non-Browser Clients (such as backend consumers): Pass a short-lived token or 'API key' via the standard X-Api-Key header during the initial connection handshake.

Cloud and Multi Tenant Administration
When running AgentWatch in a multi tenant cloud environment, use these endpoints to isolate teams and handle access controls:

POST /api/v1/tenants — Create and initialize a new isolated organization space.

GET /api/v1/tenants — List all registered organizational spaces.

GET /api/v1/tenants/{tenant_id} — View configuration settings for a given tenant space.

POST /api/v1/tenants/{tenant_id}/api-keys — Generate a new secret API token for authentication.

GET /api/v1/tenants/{tenant_id}/api-keys — View all active access tokens for a specific tenant.

DELETE /api/v1/tenants/{tenant_id}/api-keys/{key_id} — Instantly revoke an API key to block access.

GET /api/v1/tenants/{tenant_id}/usage — Retrieve platform usage logs and api metrics for billing and account limits.