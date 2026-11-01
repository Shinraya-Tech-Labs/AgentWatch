# Tenancy and Trust Boundaries

How AgentWatch decides which tenant a request acts for, and which paths enforce it. Written for TO-003 and kept current as routes change.

## Rule

A tenant is never chosen by the caller. It comes from a validated credential:

| Deployment | Credential | Tenant |
|---|---|---|
| Self-hosted, single tenant (default) | `AGENTWATCH_API_KEY` (or no key in development) | always `default` |
| Self-hosted, several tenants | `AGENTWATCH_TENANT_KEYS` (a server-side key → tenant mapping) | the key's tenant |
| Cloud mode (`AGENTWATCH_CLOUD_MODE`) | a tenant API key from the tenant store | the key's tenant |

`X-Tenant-ID` may be sent only as a *selector* that repeats the credential's tenant. A header naming any other tenant is rejected with **403**, never silently honoured.

## Audit (2026-11-02, before the TO-003 fixes)

| Path | Scoped before? | Problem found |
|---|---|---|
| `/api/v3/*` (`build_v3_router`) | partly | Every route takes the tenant from one dependency, `_v3_tenant`, and reads by id check `obs.tenant_id`. But `_v3_tenant` returned `X-Tenant-ID` verbatim outside cloud mode, and fell back to it in cloud mode. |
| `POST /api/v1/events`, `POST /api/v1/sessions` | **no** | No tenant recorded. Cloud events were teed into the v3 `default` tenant. |
| `GET /api/v1/sessions`, `GET /api/v1/sessions/{id}` and its `/events`, `/trace`, `/confidence`, `/reasoning`, `/cost`, `/replay`, `/checkpoints`, plus `POST …/simulate`, `POST …/rollback` | **no** | `_require_api_key` accepts *any* tenant's key in cloud mode, so tenant A could read or act on tenant B's sessions by id. |
| `DELETE /api/v1/sessions/prune` | **no** | Prunes every tenant's sessions. |
| `GET /api/v1/dashboard/summary`, `/top`, `/safety/blocked`, `/compliance/audit-log`, `/governance/*`, `/ingestion/metrics`, `/system/status` | **no** | Cross-tenant aggregates, visible to any tenant key. |
| `GET` / `POST /api/v1/tenants`, `GET /api/v1/tenants/{id}` | **no** | Any tenant key could list or create tenants. |
| `/api/v1/tenants/{id}/api-keys`, `/usage` | yes | `_require_tenant_ownership`. |
| `POST /api/v1/gdpr/erase` | partly | `_require_tenant_context`, but the session-scope delete ignored the tenant (TO-004). |
| `WS /ws/events` | **no** | Checked only the legacy key, then forwarded every event on the bus to every client, whatever the tenant. |
| CLI (`agentwatch …`) | n/a | Runs against a local store with the OS user's permissions; `--tenant` selects within a store the user can already read. This is a local trust boundary, not a network one. |
| Protect pipeline / MCP gateway | yes | `ToolAction.tenant_id` is set by the integration from its configuration, never from the agent; evidence is recorded under it (TO-002). |
| Celery tasks (`agentwatch/tasks.py`) | n/a | No tenant-sensitive task reads request data. |

The fixes are listed under TO-003 in `docs/IMPLEMENTATION_EVIDENCE.md`.
