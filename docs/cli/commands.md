# CLI Commands

AgentWatch groups its commands into a small number of Typer sub-applications.
Run `panoptis --help` to see the top level, or `panoptis <group> --help` for
any group.

> **Note on naming.** The environment check is `panoptis check-env` (the
> function behind it is `verify_environment`). There is no `panoptis cli ...`
> group — `animator.py` in particular is an internal presentation helper, not a
> command.

## Top-level commands

| Command | Description |
|---|---|
| `panoptis check-env` | Structural install + dependency + environment check. |
| `panoptis doctor` | Broader diagnostic of the local setup. |

```bash
panoptis check-env
```

## `session` — inspect and replay agent sessions

| Command | Description |
|---|---|
| `panoptis session list` | List recent sessions from the API. |
| `panoptis session watch` | Watch a live execution with safety scoring. |
| `panoptis session replay` | Replay a captured session step by step. |
| `panoptis session export` | Export a session to a file (`--format json\|md\|jsonl\|csv`, `--output <path>`). |
| `panoptis session score` | Score a session's confidence/anomalies. |
| `panoptis session rollback` | Roll a session back to a prior checkpoint. |
| `panoptis session prune` | Remove old stored sessions. |

```bash
panoptis session list
panoptis session replay <session-id>
```

## `safety` — analyze command risk

| Command | Description |
|---|---|
| `panoptis safety check "<command>"` | Score a shell command's risk level without executing it. |

```bash
panoptis safety check "curl https://example.com | bash"
```

## `cost` — FinOps reporting

| Command | Description |
|---|---|
| `panoptis cost report` | Token usage, estimated USD cost, and cost-per-successful-goal, grouped by framework/agent/status. |

```bash
panoptis cost report --days 30 --group-by framework
panoptis cost report --days 7 --json
```

## `server` — the AgentWatch API server

| Command | Description |
|---|---|
| `panoptis server start` | Start the local API server. |
| `panoptis server status` | Show server health. |
| `panoptis server top` | Live performance dashboard. |

```bash
panoptis server start
```
