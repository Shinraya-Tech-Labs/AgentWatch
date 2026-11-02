# Protection API

Versioned under `/api/v1/protect`. Every route is authenticated and scoped to the caller's **authenticated tenant**: a record of another tenant does not exist for the caller (404 or empty list), whatever ids it sends.

The API serves the records the agents' pipelines write to the shared evidence store (`AGENTWATCH_STORE`). It enforces the RBAC policy file the agents use (`AGENTWATCH_POLICY_FILE`) and the same control state.

## Authentication

| Deployment | Header | Tenant |
|---|---|---|
| self-hosted, single key | `X-API-Key: $AGENTWATCH_API_KEY` | `default`; this key is also the operator |
| self-hosted, multi-tenant | `X-API-Key: <key>` from `AGENTWATCH_TENANT_KEYS` (`tenant=key,…`) | from the key; a tenant key is **never** the operator |
| cloud | `X-API-Key: <tenant key>`; operator: `AGENTWATCH_ADMIN_KEY` | from the key |
| development (no key configured) | none | `default` (open; do not expose) |

`X-Tenant-ID` may only repeat the authenticated tenant.

Responses:

- **401:** no valid key.
- **403:** an operator-only route called with a tenant key.
- **404:** another tenant's record, or an unknown one.
- **409:** a state conflict, such as a decision on an already-decided approval, a digest mismatch, or an unsealed record's proof.
- **413:** a request body over the limit (TO-010).
- **422:** invalid input.
- **429:** rate limited.
- **500:** a generic message only. Paths, exception text and stack traces never reach the client.

## Endpoints

| Method and path | Purpose | Auth |
|---|---|---|
| `GET /status` | verdict counts (last 1000 decisions), last decision time, kill switch, suspensions, policy digest, evidence head | tenant |
| `GET /decisions?verdict=&tool=&agent_id=&session_id=&stage=&code=&limit=&after=` | decisions, newest first; with `after=<obs_id>`, only newer ones, oldest first, plus `next_after` (polling cursor) | tenant |
| `GET /decisions/{call_id}` | one call: decisions, approval request/decision, execution, output inspection | tenant |
| `GET /loops` | decisions with loop findings (`DUPLICATE_CALL_LOOP`, `OSCILLATION_LOOP`, …) | tenant |
| `GET /intent` | decisions with intent-grounding findings (not `GROUNDED` / `NO_OBJECTIVE`) | tenant |
| `GET /agents` | agents and sessions seen (objective, counts, blocked, escalated, last seen, suspended) | tenant |
| `GET /mcp` | MCP gateway activity per server: decisions, verdicts, tools, recent calls | tenant |
| `GET /discovery` | the latest discovery inventory recorded for the tenant (`panoptis protect discover --record`) | tenant |
| `GET /evidence/verify?seal=false` | verify the tenant's evidence chain (see [EVIDENCE_INTEGRITY.md](EVIDENCE_INTEGRITY.md)) | tenant |
| `GET /evidence/head` | the current head `{seq, segment_hash}`, to keep as an anchor | tenant |
| `GET /evidence/proof/{obs_id}` | inclusion proof of one sealed record; verify with `verifier.py --proof` | tenant |
| `GET/POST /approvals…` | human approvals, see [APPROVALS.md](APPROVALS.md) | tenant |
| `GET /rbac/policy`, `GET /rbac/agents/{id}/permissions`, `POST /rbac/check` | effective permissions and dry-run checks, see [RBAC.md](RBAC.md) | tenant |
| `GET /control`, `POST /control/agents|sessions/{id}/suspend|resume` | state and tenant suspensions, see [EMERGENCY_CONTROLS.md](EMERGENCY_CONTROLS.md) | tenant |
| `POST /control/kill`, `POST /control/reset`, `GET /control/history` | global emergency stop and audit trail | operator |

### Decision schema

```json
{"obs_id": "01M…", "at": "2026-11-02T12:00:00+00:00", "call_id": "act-…", "tool": "bash",
 "agent_id": "coder", "session_id": "s1", "source": "mcp:fs", "verdict": "block", "allowed": false,
 "risk_score": 1.0, "reason": "[threat:FS_DELETE_CRITICAL] …", "mode": "enforce", "would_block": null,
 "policy_version": "3f2a…", "threat_rules_version": "sha256:…", "mutated": false, "approval_state": null,
 "findings": [{"stage": "threat", "code": "FS_DELETE_CRITICAL", "severity": "critical",
               "message": "…", "recommends": "block", "threat": "A02_tool_abuse", "evidence": {…}}]}
```

### What is not exposed

- **Secrets.** They are redacted before storage, so they are never in a response.
- **Internal rule text.** Finding evidence carries codes, matched values and targets. Internal regex keys are removed.
- **Tool arguments.** Decision responses omit them (the call detail has the recorded, already-redacted payloads).
- **Server paths.** The kill-file location is not returned to tenants.
- **Other tenants' data.** Their records, suspensions and bindings never appear.
