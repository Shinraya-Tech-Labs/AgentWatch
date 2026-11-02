# `panoptis protect` command reference

Install: `pip install panoptis`. Add `panoptis[discovery]` for the process scan.

Inspection commands run in one of two modes:

- **API mode:** `--api URL` / `AGENTWATCH_API_URL`, with `--key` / `AGENTWATCH_API_KEY`. The command calls the [protection API](PROTECT_API.md) and is authorized exactly like it: the tenant comes from the key, and operator routes are refused to tenant keys.
- **Local mode** (the default): the command reads the evidence store directly (`--store` / `AGENTWATCH_STORE`). Access to the store file is operator access, so local mode takes `--tenant`.

| Exit status | Meaning |
|---|---|
| 0 | success |
| 1 | invalid input or config, not found, or a refused check (`rbac check`) |
| 2 | evidence verification failed (`evidence verify`) |
| 3 | not authorized: a missing, wrong or tenant key on an operator route |
| 4 | service unavailable: API unreachable, store unreadable, server error |

Errors print one line on stderr, never a traceback. Keys are never printed, and records are already redacted at storage.

## Inspect

```bash
panoptis protect status                                   # verdict counts, kill switch, suspensions, policy, evidence head
panoptis protect decisions --verdict block --limit 20     # also --tool, --agent, --stage loop|intent|rbac|threat|…
panoptis protect agents                                    # agents/sessions seen: seen is not protected (use 'discover')
panoptis protect discover [--project DIR] [--json] [--record]
panoptis protect mcp-gateway status
panoptis protect control status [--tenant acme]
```

## Approvals

```bash
panoptis protect approvals list [--state pending|approved|rejected|expired|cancelled|all]
panoptis protect approvals approve apr-1234 --digest sha256:… --by alice [--comment "ok"]
panoptis protect approvals reject  apr-1234 --digest sha256:…
```

The digest is the `action_digest` shown by `list`. An approval names exactly the action it covers (see [APPROVALS.md](APPROVALS.md)).

## Policies (RBAC)

```bash
panoptis protect rbac validate policy.yaml                 # every problem at once; exit 1 if invalid
panoptis protect rbac permissions coder [--policy FILE]    # roles, tools, restrictions, rate limits
panoptis protect rbac check coder bash --args '{"command": "sudo ls"}'   # dry run; exit 1 if denied
```

## Emergency controls

```bash
panoptis protect control kill --reason "incident 42"
panoptis protect control reset --reason "resolved"
panoptis protect control suspend-agent coder --tenant acme --reason "investigating"
panoptis protect control resume-agent coder --tenant acme --reason "cleared"
panoptis protect control history
```

Through the API, `kill`/`reset` need the operator key (see [EMERGENCY_CONTROLS.md](EMERGENCY_CONTROLS.md)).

## Evidence

```bash
panoptis protect evidence verify [--tenant acme] [--expect-head SEQ:HASH]   # exit 2 on tampering
panoptis protect evidence export -o bundle.json
panoptis protect evidence verify-bundle bundle.json
panoptis protect evidence proof <obs_id> -o proof.json
```

## Integrations

```bash
panoptis protect claude-hook install [--on-failure-block]
panoptis protect mcp-gateway stdio --server-name fs -- <server command>
panoptis protect mcp-gateway http --upstream http://127.0.0.1:9000/mcp
panoptis protect mcp-gateway config --server-name fs -- <server command>
panoptis protect discover wrap --config ~/.cursor/mcp.json --server fs [--apply]
```

## Diagnostics

`panoptis protect doctor [--policy FILE] [--store URL]` checks:

- the evidence store and its integrity;
- the kill switch;
- the RBAC policy file;
- `psutil` for discovery;
- whether the Claude Code hook is installed in this project.

Each check is `ok`, `warn` or `fail`, and the command exits 1 if any check fails.
