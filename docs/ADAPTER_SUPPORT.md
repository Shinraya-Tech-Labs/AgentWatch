# Integration support matrix

What each integration does to a tool call. Generated from `panoptis/protect/support.py`, the single source of truth; `tests/protect/test_support_matrix.py` keeps the two in sync and requires an integration test for every enforcement claim.

The three stages:

- **`PRE_EXECUTION_ENFORCEMENT`:** the shared pipeline decides before the tool runs (RBAC, threat analysis, loops, intent, approvals). A blocked call never executes, and the agent receives the sanitized result. When enforcement itself fails, the call is blocked.
- **`POST_EXECUTION_OBSERVATION`:** events are recorded for the dashboard and evidence. Nothing is prevented.
- **`UNSUPPORTED`:** there is no integration.

| Integration | Stage | Entry point | How | Test | Limitations |
|---|---|---|---|---|---|
| protect_tool() (any Python callable) | PRE_EXECUTION_ENFORCEMENT | `panoptis.protect.protect_tool` | wraps the callable | `tests/protect/test_protect_tool.py` | only calls made through the wrapper are covered |
| watch(agent, protection=...) generic agents | PRE_EXECUTION_ENFORCEMENT | `panoptis.core.watcher.GenericAdapter` | tool-like methods (run, invoke, execute, …) go through enforce_call | `tests/protect/test_enforce.py` | only methods whose name looks tool-like; generator results are inspected item by item |
| watch(agent) generic agents, no protection | PRE_EXECUTION_ENFORCEMENT | `panoptis.core.watcher.GenericAdapter` | the SafetyEngine gate runs as the threat stage of the shared pipeline (fail-closed, kill switch) | `tests/test_watcher_fail_closed.py` | pattern rules only: no RBAC, loops, intent, approvals or output sanitization |
| LangChain tools | PRE_EXECUTION_ENFORCEMENT | `panoptis.protect.integrations.protect_langchain_tool` | StructuredTool.func/coroutine or BaseTool._run/_arun | `tests/protect/test_framework_integrations.py` | tools must be protected (directly or via watch(protection=)) before the agent runs them |
| LangGraph ToolNode / compiled graphs | PRE_EXECUTION_ENFORCEMENT | `panoptis.protect.integrations.protect_langgraph_tool_node` | every tool of each ToolNode | `tests/protect/test_framework_integrations.py` | tools executed outside a ToolNode (custom nodes calling functions directly) are not covered |
| smolagents tools / agents | PRE_EXECUTION_ENFORCEMENT | `panoptis.protect.integrations.protect_smolagents_agent` | Tool.forward | `tests/protect/test_framework_integrations.py` | CodeAgent's Python interpreter executes code that is not a Tool call and is not covered |
| AutoGen (agentchat 0.4+) tools / agents | PRE_EXECUTION_ENFORCEMENT | `panoptis.protect.integrations.protect_autogen_tool` | FunctionTool.run_json (agent tools and workbench) | `tests/protect/test_framework_integrations.py` | MCP workbenches route through the MCP gateway instead |
| OpenAI Agents SDK function tools / agents | PRE_EXECUTION_ENFORCEMENT | `panoptis.protect.integrations.protect_openai_agent` | FunctionTool.on_invoke_tool (agent tools and handoff targets) | `tests/protect/test_openai_agents_integration.py` | hosted tools (web/file search, computer use, code interpreter) run on OpenAI's side and are refused, not covered; Agent.mcp_servers route through the MCP gateway instead; a refusal is returned to the model as text by default (`on_block="raise"` ends the run) |
| Claude Code (PreToolUse hook) | PRE_EXECUTION_ENFORCEMENT | `panoptis.protect.claude_hook` | PreToolUse deny/ask/updatedInput; PostToolUse updatedToolOutput | `tests/protect/test_claude_hook.py` | a hook command that cannot start or times out lets the tool run unless installed with onFailure=block (Claude Code 2.1.295+); earlier versions fail open; real-CLI end-to-end test not run; per-process loop/rate state. See [CLAUDE_CODE_HOOK.md](CLAUDE_CODE_HOOK.md) |
| MCP servers via the PANOPTIS gateway (stdio) | PRE_EXECUTION_ENFORCEMENT | `panoptis.protect.mcp_gateway.run_stdio` | proxies JSON-RPC; tools/call goes through aenforce_call before it is forwarded | `tests/protect/test_mcp_gateway.py` | only clients configured to start the gateway (not the server) are covered; one gateway process per client connection. See [MCP_GATEWAY.md](MCP_GATEWAY.md) |
| MCP servers via the PANOPTIS gateway (Streamable HTTP) | PRE_EXECUTION_ENFORCEMENT | `panoptis.protect.mcp_gateway.build_http_app` | proxies POST/GET/DELETE; tools/call is enforced and answered as JSON | `tests/protect/test_mcp_gateway.py` | clients must use the gateway URL; the legacy HTTP+SSE transport (2024-11-05) is not supported |
| MCP message tap | POST_EXECUTION_OBSERVATION | `panoptis.sensors.mcp` | records JSON-RPC messages it is given | — | observation only; use the MCP gateway for enforcement |
| LangChain callback adapter | POST_EXECUTION_OBSERVATION | `panoptis.adapters.langchain` | callbacks record events | — | observation only; use protect_langchain_tool for enforcement |
| LangGraph adapter (observability) | POST_EXECUTION_OBSERVATION | `panoptis.adapters.langgraph` | records graph invocations | — | observation only |
| AutoGen adapter (observability) | POST_EXECUTION_OBSERVATION | `panoptis.adapters.autogen` | records message/reply events | — | observation only |
| smolagents adapter (observability) | POST_EXECUTION_OBSERVATION | `panoptis.adapters.smolagents` | records run/step/tool events | — | observation only |
| CrewAI | POST_EXECUTION_OBSERVATION | `panoptis.adapters.crewai` | lifecycle callbacks | — | observation only; no tool-level enforcement yet |
| OpenAI Agents SDK adapter (observability) | POST_EXECUTION_OBSERVATION | `panoptis.adapters.openai_agents` | lifecycle hooks | — | observation only; use protect_openai_agent for enforcement |
| AutoGPT | POST_EXECUTION_OBSERVATION | `panoptis.adapters.autogpt` | event translation | — | observation only |
| OpenClaw | POST_EXECUTION_OBSERVATION | `panoptis.adapters.openclaw` | event translation | — | observation only |
| Generic CLI agent | POST_EXECUTION_OBSERVATION | `panoptis.adapters.generic` | captures the process output | — | observation only: the subprocess runs its own tools |
| Claude Code (stream-json adapter) | POST_EXECUTION_OBSERVATION | `panoptis.adapters.claude_code` | parses the CLI's event stream | — | the CLI has already run a tool when its event is read; use the PreToolUse hook for enforcement |

## Using enforcement

```python
from panoptis.protect import ProtectionPipeline, protect_tool, protection_context
from panoptis.protect.integrations import protect_tools
from panoptis.core.watcher import watch

pipeline = ProtectionPipeline()
watch(agent, protection=pipeline)          # LangGraph / AutoGen / smolagents / LangChain agents
protect_openai_agent(openai_agent, pipeline=pipeline)  # OpenAI Agents SDK (panoptis.protect.integrations)
protect_tools(my_tools, pipeline=pipeline) # or protect individual framework tools
with protection_context(agent_id="coder", session_id="run-1", objective="fix the failing test"):
    agent.run(...)
```

Every enforcing integration reaches the same `ProtectionPipeline` decision flow (`enforce_call`, `aenforce_call` or, for the Claude Code hook, `pipeline.evaluate`). `tests/protect/test_single_boundary.py` checks this for every row above. Even `watch(agent)` without `protection=` runs its SafetyEngine gate inside the pipeline, so the kill switch applies there too.

`watch(agent, protection=...)` raises if it finds no tools to protect, so asking for enforcement never silently gives you observation only.

## Version compatibility (tested)

| Framework | Version tested |
|---|---|
| langchain-core | 1.2.17 |
| langgraph | 1.0.10 |
| smolagents | 1.26.0 |
| autogen-agentchat / autogen-core / autogen-ext | 0.7.5 |
| openai-agents | 0.24.0 |

Older major versions are untested. AutoGen 0.2 (`pyautogen`, `ConversableAgent`) is observation only.
