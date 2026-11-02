# Per-agent RBAC

The RBAC stage (`Stage.RBAC`) decides, before a tool runs, whether this agent may call this tool with these arguments in this tenant. Every decision names the role and the rule that produced it.

Code: `panoptis/protect/rbac.py` (`parse_policy`, `PolicyStore`, `RBACEngine`, `RateLimiter`), `panoptis/api/protect_rbac.py`. Tests: `tests/protect/test_rbac.py`.

## Policy

```yaml
version: "1.0"                 # 1.x is supported
default: deny                  # deny (recommended) | allow
agents:                        # agent id glob -> roles, in every tenant
  coder: [coder]
  "research-*": [researcher]
roles:
  coder:
    allowed_tools: [bash, read_file, write_file]
    forbidden_tools: []
    require_approval: [write_file]          # always escalated to a human
    rate_limits:
      max_calls_per_minute: 120             # per agent, per tenant
      per_session_per_minute: 30            # per agent, per session
      per_tool: {bash: 20}                  # per agent, per tool glob
    restricted_arguments:
      bash:
        blocked_commands: ["sudo *"]
        allowed_commands: [ls, cat, git, pytest]   # every command in the line must be listed
        blocked_paths: ["**/.env", "~/.ssh/**"]
        allowed_paths: ["./**"]
        allowed_hosts: [pypi.org, "*.github.com"]
        blocked_hosts: []
        max_length: 4000
        fields: {timeout: {max: 600}, branch: {pattern: "^feature/"}, mode: {enum: [fast, full]}}
tenants:                       # optional, per-tenant bindings
  globex:
    inherit: false             # default true: the top-level agents also apply here
    agents: {coder: [researcher]}
```

## Rules

- **Default deny.** An agent with no role in the call's tenant is denied (`RBAC_UNKNOWN_AGENT`), and so is a tool no role allows (`RBAC_TOOL_NOT_ALLOWED`).
- **Default allow is an explicit exception.** `default: allow` lets agents without a role through (`RBAC_DEFAULT_ALLOW`). It also lets through roles that list only `forbidden_tools`. Known agents' roles still apply.
- **Forbidden beats allowed,** across all of an agent's roles (`RBAC_TOOL_FORBIDDEN`).
- **Argument restrictions** use the pipeline's own argument analysis: the parsed shell commands, paths, hosts and SQL. Codes: `RBAC_COMMAND_BLOCKED`, `RBAC_COMMAND_NOT_ALLOWED`, `RBAC_PATH_BLOCKED`, `RBAC_PATH_NOT_ALLOWED`, `RBAC_HOST_BLOCKED`, `RBAC_HOST_NOT_ALLOWED`, `RBAC_ARGUMENTS_TOO_LARGE`, `RBAC_FIELD_*`.
- **`require_approval`** escalates the call to a human (`RBAC_REQUIRES_APPROVAL`, see [APPROVALS.md](APPROVALS.md)).
- **Rate limits** are 60-second sliding windows, counted per tenant and agent, per tool glob or per session (`RBAC_RATE_LIMIT`). All limits are checked before any is counted, so a refused call consumes no budget. The counters are bounded in memory.
- **No policy** is reported as `RBAC_NO_POLICY` (ALLOW). Without a policy file, tool authorization is not enforced, and the decision says so.

## Tenant and identity binding

The engine looks up roles with `PolicyStore.policy.roles_for(agent_id, tenant_id)`. The pipeline passes the call's `tenant_id` and `session_id`, and the tenant comes from the authenticated identity of the integration or API.

- **Tenant bindings.** A tenant's `agents` bindings add to the global ones. With `inherit: false` they replace them.
- **Separate counters.** Rate-limit counters of different tenants never mix.
- **The evidence names the tenant.** Every RBAC finding records `tenant_id`, `agent_id`, the role, the rule and the policy digest.

Agent identity itself is checked earlier, in the identity stage:

- `ProtectionConfig.require_registered_agents` rejects agents that were not registered.
- `require_context` rejects calls without an explicit agent or session.
- `bind_sessions` binds a session id to the first agent that uses it.

## Validation

`parse_policy` rejects a policy, listing every error at once, if it has any of:

- duplicate YAML keys (normally the last one silently wins);
- unknown keys at any level;
- an unsupported `version`;
- both `roles` and `agent_roles`;
- a tool pattern that is both allowed and forbidden by the same role (ambiguous);
- empty patterns;
- an unknown role in `agents` or `tenants`;
- non-integer or negative rate limits;
- a field spec that is not a number (`max`/`min`) or a list (`enum`), or has `min > max`;
- an invalid regex.

## Reload

`PolicyStore(path=...)` re-reads the policy file when it changes, at most every `auto_reload_s` (2 s, checked on authorization). `reload_if_changed()` and `reload()` are available for explicit control.

- **The swap is atomic.** A new policy replaces the old reference in one step, so concurrent authorizations see either the old policy or the new one, never none or a partial policy.
- **An invalid file leaves the previous policy active.** The error is reported in `status()["last_error"]` and in `history`.
- **A file that changes while it is being read is not activated** (size or mtime differ before and after the read), so a half-written file is never enforced. Write policies atomically (write a temp file, then rename) to make reloads immediate.

## API

All routes are scoped to the caller's authenticated tenant.

```text
GET  /api/v1/protect/rbac/policy                         digest, version, default, roles, and the bindings in force for this tenant only
GET  /api/v1/protect/rbac/agents/{agent_id}/permissions  roles, allowed/forbidden/approval tools, restrictions, rate limits, effect
POST /api/v1/protect/rbac/check  {"agent_id", "tool", "arguments", "session_id"?}
     → {"verdict", "authorized", "reasons": [...], "findings": [...]}   a dry run: no rate budget used
```

The API reads `AGENTWATCH_POLICY_FILE`, the file agents enforce. The dashboard's permissions view is TO-029.

## Limitations

- **Globs, not regexes.** Tool and agent names match with shell-style globs (case-insensitive for tools).
- **Mutation is judged by the tool name.** An unusually named mutating tool needs an explicit rule.
- **Rate-limit counters are per process.** Several agent processes each enforce their own window.
