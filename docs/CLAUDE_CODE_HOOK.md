# Claude Code enforcement: the PreToolUse hook

AgentWatch enforces on Claude Code through the CLI's own [hook mechanism](https://code.claude.com/docs/en/hooks). Before the CLI runs any tool, it sends the call to the AgentWatch hook, and the shared protection pipeline decides. That pipeline is the same one used by `protect_tool()` and the framework integrations: RBAC, threat analysis, loops, intent and the kill switch.

The stream-json adapter (`panoptis.adapters.claude_code`) is **observation only**. It reads tool calls after the CLI has run them, so a policy violation it finds is recorded as a detection (`policy_violation`, `detected_after_execution`), never as a block.

## Install

```bash
pip install agentwatch-ai
panoptis protect claude-hook install              # writes ./.claude/settings.local.json
panoptis protect claude-hook install --settings ~/.claude/settings.json   # every project
panoptis protect claude-hook print > aw-hook.json # for: claude --settings aw-hook.json
panoptis protect claude-hook uninstall
```

Install merges into an existing settings file. Your other settings and hooks are kept, and re-installing replaces the AgentWatch entries instead of duplicating them. The hook runs on `PreToolUse`, `PostToolUse` and `PostToolUseFailure` for every tool (`"matcher": "*"`), including MCP tools.

The command it installs is the absolute path of the current Python interpreter running `panoptis/protect/claude_hook_entry.py`. Re-install after moving or recreating the virtualenv.

## What the hook answers

| Pipeline verdict | Hook answer | Effect |
|---|---|---|
| `ALLOW` | no decision | Claude Code's own permission rules and prompts apply unchanged. The hook never widens permissions. |
| `BLOCK` | `permissionDecision: "deny"` with the reason | The tool does not run, and Claude sees the reason. |
| `ESCALATE_HITL` | `permissionDecision: "ask"` | Claude Code's permission prompt is the approval channel. In `-p` runs, where nobody can answer, the CLI denies the call. |
| `MUTATE_AND_ALLOW` | `"ask"` with `updatedInput` | The user sees and approves the redacted arguments. Set `AGENTWATCH_HOOK_MUTATION=allow` to run them without a prompt. |

**After the tool runs:**

- **`PostToolUse`:** the result is inspected. If it carries a prompt injection or a secret, the hook returns `updatedToolOutput`, the sanitized result in the tool's own output shape, plus a short `additionalContext` note. The tool has already run at that point, so only what Claude sees changes; side effects have happened.
- **`PostToolUseFailure`:** the failure is recorded.

**Evidence:** every decision, output finding and execution is recorded in the AgentWatch evidence store (`protect.decision`, `protect.output`, `protect.execution`). The records carry:

- `source = "hook:claude-code"`;
- the Claude Code `session_id`;
- a `call_id` of `cc-<tool_use_id>`, so the pre- and post-tool records of one call match.

## Failure behaviour

**The hook fails closed.** It exits with code 2, which Claude Code treats as a block, on:

- invalid input;
- a policy file that does not load;
- an evidence-store error;
- any pipeline exception;
- a broken AgentWatch install (an import failure).

The entry script uses only the standard library until it imports AgentWatch, so a broken install still exits 2. A plain `python -m …` would exit 1, which Claude Code treats as a non-blocking error.

> **Fail-open warning.** Before Claude Code 2.1.295, a hook that fails to start or times out lets the tool run. Do not describe this integration as fail-closed unless the CLI is 2.1.295 or later and the hook was installed with `--on-failure-block`. The hook contract is tested, but no end-to-end run against a real signed-in CLI has been done on any version. The development machine used for this release has Claude Code 2.1.229, which is in the fail-open range.

**What the hook cannot control:** if the hook command cannot start at all (the interpreter was deleted) or times out (default 30 s), Claude Code runs the tool anyway. Claude Code 2.1.295 and later support `"onFailure": "block"`:

```bash
panoptis protect claude-hook install --on-failure-block
```

Use this on those versions. Older versions do not have this setting.

## Settings

| Variable | Meaning | Default |
|---|---|---|
| `AGENTWATCH_POLICY_FILE` | RBAC policy (YAML/JSON) | none (no RBAC stage) |
| `AGENTWATCH_AGENT_ID` | agent id the policy's `agents:` section matches | `claude-code` |
| `AGENTWATCH_TENANT_ID` | tenant | `default` |
| `AGENTWATCH_OBJECTIVE` | session objective, for intent grounding | none |
| `AGENTWATCH_STORE` | evidence store | `~/.agentwatch/agentwatch.db` |
| `AGENTWATCH_HOOK_MUTATION` | `ask` or `allow` for redacted arguments | `ask` |
| `AGENTWATCH_KILL_FILE` | kill-switch file (shared with every enforcement point) | `~/.agentwatch/protect.kill` |

Claude Code tool names are mapped to the names policies use:

| Claude Code tool | Policy name |
|---|---|
| `Bash` | `bash` |
| `PowerShell` | `powershell` |
| `Write` | `write_file` |
| `Edit` / `MultiEdit` / `NotebookEdit` | `edit_file` |
| `Read` | `read_file` |
| `Glob` | `glob` |
| `Grep` | `grep` |
| `WebFetch` | `fetch_url` |
| `WebSearch` | `web_search` |
| `Task` / `Agent` | `task` |

MCP tools keep their `mcp__server__tool` name.

## Limitations

- **Process lifetime:** each hook call is a new process, so per-process state does not carry over between calls in one session: loop detection, rate limits and circuit breakers. Decisions and evidence persist; the kill switch is a file and applies across processes.
- **Bash command coverage:** the decision covers the command string Claude Code passes to the tool. Commands that a script started by Bash runs later are not visible to the hook.
- **Output rewriting:** `updatedToolOutput` must match the tool's output shape. The hook keeps the shape it received and only replaces strings inside it.

## Tested

- **Hook contract:** `tests/protect/test_claude_hook.py` (no model involved).
  - Every verdict mapping.
  - `updatedInput` keeps the other fields.
  - `PostToolUse` sanitization keeps the output shape.
  - Failures are recorded.
  - The exact installed command string, run through a shell, denies and allows.
  - A broken install exits 2.
  - Bad input, a bad policy and a pipeline exception each exit 2.
  - Install merges and is idempotent; uninstall; the CLI.
- **Stream adapter:** `tests/test_adapter_claude_code.py`. A violation read from the stream is a detection, not a block.
- **Real CLI:** `tests/e2e_claude/test_claude_code_hook_e2e.py`, opt-in with `AGENTWATCH_E2E_CLAUDE=1 pytest tests/e2e_claude -m external`. It runs `claude -p` with the hook and a harmless command that appends to a marker file:
  - **denied by policy:** the marker is absent and no execution is recorded;
  - **allowed:** the marker holds exactly one line, and one execution is recorded under the same `call_id`.
- **Versions:** written against Claude Code 2.1.229 and the current hooks reference.
