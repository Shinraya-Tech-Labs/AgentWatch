# MCP gateway

The AgentWatch MCP gateway is an enforcing proxy between an MCP client (Claude Desktop, Cursor, Claude Code, an agent built on the MCP SDK) and an MCP server. The client talks to the gateway as if it were the server. Every `tools/call` is decided by the shared protection pipeline **before** it reaches the server, and every result is inspected **before** it reaches the client.

Code: `panoptis/protect/mcp_gateway.py`. Tests: `tests/protect/test_mcp_gateway.py`, including end-to-end runs of the official MCP SDK client → gateway → MCP SDK server, over stdio and over Streamable HTTP.

## What happens to a message

| Message | Gateway |
|---|---|
| `tools/call` request | Validated, decided by the pipeline (`aenforce_call`), forwarded **only** if admitted (with the admitted, possibly redacted, arguments), and answered with the sanitized result |
| a refused `tools/call` | Answered by the gateway with an MCP tool error (`isError: true`, the reason, and `_meta.agentwatch` with the verdict and finding codes). **The server never sees it.** |
| everything else: `initialize`, `tools/list`, resources, prompts, notifications, server requests (sampling, elicitation, roots), responses | Passed through unchanged; ids, errors, capabilities and tool schemas are preserved exactly |

The pipeline applies these stages in order:

1. identity;
2. the kill switch and suspensions;
3. threat analysis (shell, SQL, paths, SSRF, secrets);
4. RBAC (tools, arguments, rate limits, tenant);
5. loop detection;
6. intent grounding (with `--objective`);
7. human approval.

An escalated call waits for a decision through the approval API or dashboard, which share the evidence store. It is forwarded only if approved, and the timeout is `AGENTWATCH_APPROVAL_TIMEOUT_S` (120 s). Results then go through output inspection: prompt-injection lines are quarantined, secrets redacted, and oversize content withheld. Decisions, approvals and executions are recorded in the evidence ledger (secrets are redacted before storage), and `initialize` records the session with the client and server info.

## Setup

### stdio: the gateway starts the server

Replace the server's command in the client's configuration with the gateway, followed by `--` and the original command:

```bash
panoptis protect mcp-gateway config --server-name fs -- npx -y @modelcontextprotocol/server-filesystem /work
```

prints an `mcpServers` entry such as:

```json
{"mcpServers": {"fs": {"command": "/path/to/python", "args": ["-m", "panoptis.protect.mcp_gateway", "stdio", "--server-name", "fs", "--", "npx", "-y", "@modelcontextprotocol/server-filesystem", "/work"]}}}
```

Run directly: `panoptis protect mcp-gateway stdio --server-name fs -- <server command>`. The server's stderr is passed through.

### Streamable HTTP: the gateway forwards to a server URL

```bash
AGENTWATCH_TENANT_KEYS="acme=<key>" panoptis protect mcp-gateway http --upstream http://127.0.0.1:9000/mcp --port 8765
```

- **Where clients connect.** Clients use `http://127.0.0.1:8765/mcp`. `POST`, the `GET` event stream and `DELETE` are proxied, with the `Mcp-Session-Id` / `MCP-Protocol-Version` headers. A `tools/call` is answered as `application/json`; everything else is streamed back as the server sent it.
- **Tenant keys.** With `AGENTWATCH_TENANT_KEYS`, every request must carry `X-AgentWatch-Key`, and the tenant comes from the key, never from the client. The key header is not forwarded to the server.
- **Public bind.** The gateway refuses to listen on a non-loopback address without keys.

`GET /healthz` reports per-tenant counters.

### Settings

| Setting | Meaning |
|---|---|
| `--agent-id` / `AGENTWATCH_AGENT_ID` | the agent identity used for RBAC and evidence (default `mcp-client`) |
| `--session-id` / `AGENTWATCH_SESSION_ID` | session (default: one per gateway connection; for HTTP, per `Mcp-Session-Id`) |
| `--tenant` / `AGENTWATCH_TENANT_ID` | tenant for stdio (HTTP: from the key) |
| `--server-name` | recorded as the source `mcp:<name>` |
| `--tool-prefix` | e.g. `github.`: policies see `github.create_issue`; the server still receives `create_issue` |
| `--objective` / `AGENTWATCH_OBJECTIVE` | the task, for intent grounding |
| `AGENTWATCH_POLICY_FILE` | RBAC policy (re-read on change) |
| `AGENTWATCH_STORE` | evidence store shared with the API/dashboard |
| `--timeout` | seconds to wait for the server per call (300) |
| `--max-message-bytes` | the largest JSON-RPC message accepted (8 MiB) |

`panoptis protect mcp-gateway status` summarises recent gateway decisions per server, tool and verdict.

## Failure handling

| Situation | Result |
|---|---|
| invalid JSON | `-32700`, not forwarded |
| not an object, a batch, `jsonrpc` ≠ 2.0, a bad id or params | `-32600`, not forwarded |
| bad `tools/call` params (no name, arguments not an object) | `-32602`, not forwarded |
| a message larger than the limit | `-32600` (stdio) / HTTP 413, not forwarded |
| a second `tools/call` with an id already in flight | `-32600` |
| more than 64 tool calls in flight | `-32002` |
| enforcement itself fails | refused (fail closed) |
| the client cancels (`notifications/cancelled`) while the call is being decided or waits for approval | the call never runs, the approval request is cancelled, and no response is sent (per MCP) |
| the server answers with a JSON-RPC error | passed to the client unchanged; recorded as a failed execution |
| no answer within `--timeout` | `-32001` to the client, `notifications/cancelled` to the server |
| the server exits or disconnects | pending calls and later requests get `-32000` |
| a malformed server message | dropped and counted; it cannot be routed |

## Security model and limitations

- **Coverage.** Only clients configured to use the gateway are covered. A client that starts the server directly bypasses it. Agent discovery (`TO-024`) reports MCP servers that are not behind the gateway as unmonitored.
- **What is enforced.** The gateway enforces `tools/call`. Resource reads (`resources/read`) and prompts are passed through and not inspected.
- **Timeouts.** A timed-out call may already have had effects on the server. The gateway cancels it, but cannot undo it.
- **Batches.** JSON-RPC batches are refused. They were removed from MCP in 2025-06-18.
- **Transports.** The legacy HTTP+SSE transport (2024-11-05) is not supported.
- **Process-local counters.** Rate-limit and loop counters belong to one gateway process (stdio: one per client connection).
