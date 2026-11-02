# Human approval of escalated tool calls

A call whose verdict is `ESCALATE_HITL` is held until a human decides. Examples: a force-push, an unbounded `UPDATE`, `sudo`, a role's `require_approval` tools.

**The tool runs only after a valid approval for exactly that action.** Every other outcome leaves it unrun:

- rejection;
- timeout (expiry);
- cancellation;
- no approval channel;
- a failing approver;
- a changed action;
- a decision for a different action;
- a replay;
- another tenant's decision.

Code: `panoptis/protect/approvals.py` (`ApprovalBroker`), `panoptis/api/protect_approvals.py`. Tests: `tests/protect/test_approvals_lifecycle.py`, `tests/protect/test_enforce.py`, `tests/protect/test_framework_integrations.py`.

## The request

| Field | Meaning |
|---|---|
| `request_id` | `apr-…`, unique |
| `tenant_id`, `agent_id`, `session_id`, `call_id`, `tool`, `arguments` | the exact action |
| `action_digest` | SHA-256 over tenant, agent, session, call id, tool and canonical arguments |
| `reason`, `risk_score`, `findings` | why it was escalated |
| `verdict`, `policy_version`, `threat_rules_version` | the decision it belongs to |
| `created_at`, `expires_at` | expiry; the default is 120 s (`ProtectionConfig.approval_timeout_s`) |

Requests and decisions are records in the requesting tenant's evidence ledger: `protect.approval.request` and `protect.approval.decision`. Agents, the API and the dashboard share them through the store (`AGENTWATCH_STORE`), so they work across processes.

## Outcomes

| Outcome | How | Result |
|---|---|---|
| approved | API, dashboard or an approver callback | runs once; edited arguments are re-inspected, and a human cannot approve past a hard block |
| rejected | the same | `APPROVAL_REJECTED`; never runs |
| expired | no decision before `expires_at`; an approval after expiry records the timeout instead | `APPROVAL_TIMEOUT`; never runs |
| cancelled | the async caller was cancelled while waiting (`cancel_for_call`), or `broker.cancel(id)` | `APPROVAL_CANCELLED`; never runs; a later approval cannot revive it |
| unavailable | the pipeline has no approval channel | blocked (`NO_APPROVAL_CHANNEL`), configurable via `escalate_without_approver` |

## Why an approval cannot be reused

- **The decision states what it approves.** A decision may carry the `action_digest` the approver was shown; the API requires it. A decision for a different digest is refused (`ApprovalError`, HTTP 409).
- **The action is re-checked before it runs.** The pipeline compares the digest of the action it is about to run with the request, after the approval and again just before execution. Arguments changed in between are not covered (`APPROVAL_MISMATCH`).
- **The first decision wins.** Later decisions on the same request are ignored.
- **A call id runs at most once.** The approval belongs to one call, so it cannot be replayed for another.

## API

All routes are scoped to the caller's authenticated tenant; another tenant's request is a 404.

```text
GET  /api/v1/protect/approvals?state=pending          → {"approvals": [...], "count": n}
GET  /api/v1/protect/approvals/{request_id}           → the request, its state and outcome
POST /api/v1/protect/approvals/{request_id}
     {"approved": true, "action_digest": "sha256:…", "decided_by": "alice", "comment": "ok",
      "edited_arguments": null}                          → the request with "applied": true|false
```

The recorded decider is `<tenant>:<decided_by>`. `applied: false` means an earlier decision already stood.

## Claude Code

The PreToolUse hook answers an escalation with `"ask"`, so Claude Code's own permission prompt is the approval channel (see [CLAUDE_CODE_HOOK.md](CLAUDE_CODE_HOOK.md)).
