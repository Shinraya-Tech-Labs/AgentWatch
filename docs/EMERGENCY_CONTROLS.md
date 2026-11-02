# Emergency stop and suspensions

Three controls stop protected agents **before their next tool call**:

- **The kill switch:** a global emergency stop.
- **Agent suspension:** stops one agent in one tenant.
- **Session suspension:** stops one session in one tenant.

None of them terminates a process: they stop agents from *acting*.

Code: `panoptis/protect/control.py` (`ControlState`), `panoptis/api/protect_control.py`, `panoptis protect control …`. Tests: `tests/protect/test_emergency_controls.py`.

## Where it applies

Each control is checked at three points:

1. by the pipeline's control stage, before any other stage;
2. **again immediately before the tool runs**, so a stop issued while a call was being decided or waiting for approval still prevents it;
3. before each chunk of a streamed result.

These execution paths share it, because they all run in the one pipeline:

- `protect_tool`;
- `enforce_call` / `aenforce_call`;
- `watch(agent, protection=…)`;
- `watch(agent)` without protection (its SafetyEngine gate runs inside the pipeline);
- LangChain, LangGraph, AutoGen and smolagents tools;
- the MCP gateway;
- the Claude Code hook.

## How it reaches other processes

| Channel | Reach | Latency |
|---|---|---|
| kill file (`AGENTWATCH_KILL_FILE`, default `~/.agentwatch/protect.kill`) | every process on the host | immediate (checked on every call) |
| `protect.control` records in the evidence store (`AGENTWATCH_STORE`) | every process sharing the store, on any host | `ControlState.refresh_s` (1 s by default; the API and CLI use 0) |

- **The kill file is the host's shared state.** It exists exactly while the switch is tripped: `trip` writes it atomically, and `reset` removes it, so a reset by any process resumes all of them.
- **A failed write still stops this process.** If the file cannot be written, the tripping process still stops itself.
- **An unreadable kill-file location blocks.** If the kill file's location cannot be checked, calls are blocked (fail closed).
- **State survives restarts.** It is the fold of the append-only control records, so a restarted agent sees the trip and the suspensions.

## Calls in flight, approvals and streams

| Situation | Behaviour |
|---|---|
| a call not yet started | blocked (`KILL_SWITCH`, `AGENT_SUSPENDED`, `SESSION_SUSPENDED`); it never runs |
| a call waiting for human approval | the request is withdrawn (`cancelled`, decided by `system:emergency-stop`); the call never runs, and a late approval cannot revive it |
| a call approved just before the stop | blocked by the re-check before execution |
| a tool already running | **not interrupted**; it finishes, and its output is still inspected and recorded. Only new calls are stopped. |
| a streamed result | the stream ends with `ActionBlocked`; no chunk produced after the stop is delivered |
| concurrent callers | a call that begins after the trip never runs (tested with 8 threads); a call admitted just before may finish |

## Scope and authorization

- **The kill switch is global.** It stops every tenant, and only the operator can trip or reset it: in self-hosted mode the `AGENTWATCH_API_KEY` holder, in cloud mode `AGENTWATCH_ADMIN_KEY`. A tenant key gets 403.
- **Suspensions are tenant-scoped.** A tenant key suspends `<its tenant>/<agent>` or `<its tenant>/<session>`. It can neither see nor lift another tenant's suspensions.
- **Every action needs a reason and is audited.** Each is an append-only record with its actor (`operator:<name>`, `<tenant>:<name>` or `cli:<user>`) and timestamp. `GET /api/v1/protect/control/history` (operator) and `panoptis protect control history` show the trail.

## API and CLI

```text
GET  /api/v1/protect/control                                  state; suspensions of the caller's tenant
GET  /api/v1/protect/control/history                          audit trail (operator)
POST /api/v1/protect/control/kill     {"reason", "actor"?}     operator
POST /api/v1/protect/control/reset    {"reason", "actor"?}     operator
POST /api/v1/protect/control/agents/{id}/suspend|resume       {"reason"}   caller's tenant
POST /api/v1/protect/control/sessions/{id}/suspend|resume     {"reason"}   caller's tenant
```

```bash
panoptis protect control kill --reason "incident 42"
panoptis protect control status [--tenant acme]
panoptis protect control suspend-agent coder --tenant acme --reason "investigating"
panoptis protect control resume-agent coder --tenant acme --reason "cleared"
panoptis protect control reset --reason "resolved"
panoptis protect control history
```

The dashboard view is TO-028.

## Limitations

- **Running tools are not stopped.** Use the platform's own controls (process supervision, container stop) if a running action must be interrupted.
- **Other hosts lag.** A host that shares only the store sees a trip within `refresh_s`; it is not instantaneous.
- **The legacy `panopti` CLI is separate.** Its `.panopti.kill` switch is not the protect kill file. Set `AGENTWATCH_KILL_FILE` to the same path to unify them.
