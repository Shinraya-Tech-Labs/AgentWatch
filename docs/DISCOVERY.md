# Agent and MCP discovery

`panoptis protect discover` lists the agent runtimes and MCP servers on this machine, and says for each whether AgentWatch actually enforces on it. **Detection is never reported as protection.** An item is `MONITORED` only when there is evidence that enforcement is in place.

Code: `panoptis/protect/discovery.py`. Tests: `tests/protect/test_discovery.py`.

```bash
panoptis protect discover                        # table; --json for machine output
panoptis protect discover --project ~/src/app    # also check a project's .mcp.json, .claude/, .cursor/, .vscode/
panoptis protect discover --no-processes         # config files only
panoptis protect discover --record               # store the inventory (protect.discovery) for the dashboard
panoptis protect discover wrap --config ~/.cursor/mcp.json --server fs            # show the change
panoptis protect discover wrap --config ~/.cursor/mcp.json --server fs --apply    # write it (with a backup)
```

## Status

| Status | Meaning | Evidence |
|---|---|---|
| `MONITORED` | AgentWatch enforces on it | an MCP server whose configured command runs the AgentWatch gateway; a Claude Code settings file with the AgentWatch PreToolUse hook; a running gateway process, or an MCP server process whose parent is one; a Claude Code process whose applicable settings (`~/.claude/settings.json`, `<cwd>/.claude/settings*.json`) have the hook |
| `UNMONITORED` | supported, but not protected | the same runtime without that evidence: a server started directly, Claude Code without the hook |
| `UNKNOWN` | cannot tell | a remote server URL (it may or may not be a gateway); an unreadable, invalid or oversized config file; a process whose working directory or parent cannot be read |
| `UNSUPPORTED` | no AgentWatch integration for the runtime's own tools | Cursor, VS Code, Windsurf, Claude Desktop, Codex, Aider, Goose. Their MCP servers are listed separately and can be put behind the gateway. |

Every item records:

- the kind (`runtime`, `mcp_server`, `process`);
- the runtime, the name and the source (a file path or `pid:<n>`);
- the discovery timestamp and a confidence (0–1);
- the reason, and details: the redacted command, the transport, the names of env vars, and the listening ports of recognised processes.

A look-alike does not count. A hook command that only mentions AgentWatch, or a PostToolUse-only entry, is not the PreToolUse hook.

## Sources and platform support

| Source | Paths / method | Platforms |
|---|---|---|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json`; `~/Library/Application Support/Claude/…` (macOS); `~/.config/Claude/…` (Linux) | all |
| Claude Code | `~/.claude.json` (global and per-project servers), `~/.claude/settings.json`, `<project>/.mcp.json`, `<project>/.claude/settings*.json` | all |
| Cursor | `~/.cursor/mcp.json`, `<project>/.cursor/mcp.json` | all |
| VS Code | `<project>/.vscode/mcp.json` | all |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | all |
| processes | `psutil` (optional extra `panoptis[discovery]`): name and command line, matched against known signatures; Electron helper processes are skipped | Windows, macOS, Linux; other users' processes need privileges and are skipped when inaccessible |
| listening ports | `psutil` connections of *recognised* processes only | as above; macOS may need elevated rights |

## Boundaries

- **Read-only and bounded.** Only the files above are read, each up to 2 MB. At most 5 000 processes and 500 servers per file are examined. Without `psutil`, the process scan reports `UNKNOWN` instead of failing.
- **No secrets.** Environment variables are never read; only the *names* of variables a server config sets are listed. Command-line arguments that look like credentials (`--token X`, `--api-key=…`, known key formats) are redacted, and so are URL query tokens.
- **Unrelated processes are not reported.** Only processes matching an agent or MCP signature appear.
- **Nothing is killed or modified.** The one lifecycle control, `discover wrap`, rewrites a single stdio server entry to start through the gateway, and only with `--apply`. It writes `<file>.agentwatch.bak` first, replaces the file atomically, leaves other entries alone, and does nothing for an entry that is already wrapped. The client must be restarted to pick the change up. HTTP servers cannot be wrapped this way; run `mcp-gateway http` in front of them.

## Limitations

- **Unknown runtimes are not seen.** Signatures cover the runtimes listed above.
- **Applicable settings are approximated.** A Claude Code process's settings are taken to be those of its working directory and home. Enterprise-managed settings and `--settings` files passed on the command line are not inspected, so such a session may be reported `UNMONITORED` while it is in fact protected. The error is always in the safe direction.
- **Remote servers stay `UNKNOWN`,** even when their URL is a gateway's.
