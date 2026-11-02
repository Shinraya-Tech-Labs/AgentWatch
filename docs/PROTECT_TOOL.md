# Protecting tools directly: `protect_tool()`

Use `protect_tool()` for any Python callable that a framework integration does not manage. It enforces the same contract as the LangChain, LangGraph, smolagents and AutoGen integrations and the Claude Code hook:

1. **Decided before it runs.** The shared pipeline runs, in order: identity, kill switch, RBAC, threat analysis, loops, intent, approvals.
2. **A blocked call never runs.** The wrapper raises `ActionBlocked`, a subclass of `AgentWatchBlockedError`, which carries the full `Decision`.
3. **Mutated arguments reach the tool.** For example, a secret in an argument is redacted, and the call is mapped back onto your positional and keyword arguments.
4. **The tool runs at most once.** Its own exceptions propagate unchanged.
5. **The result is inspected before you get it.** Injected instructions are quarantined and secrets redacted. Generators and async generators are inspected chunk by chunk.
6. **Enforcement failures block.** If the pipeline itself fails (a policy that does not load, a store error), the call is blocked, not run.
7. **Everything is evidence.** Decisions, executions (outcome, duration, error type) and output findings go to the evidence ledger.

```python
from panoptis.protect import ProtectionPipeline, protect_tool, protection_context

pipeline = ProtectionPipeline()

@protect_tool(tool="bash", pipeline=pipeline)
def bash(command: str) -> str: ...

@protect_tool(pipeline=pipeline)            # the tool name defaults to the function name
async def fetch_url(url: str) -> str: ...

safe_query = protect_tool(run_query, tool="sql", pipeline=pipeline)   # wrap an existing callable

with protection_context(agent_id="coder", session_id="run-42", objective="fix the failing test"):
    bash("pytest -q")
```

**Identity** comes from the keyword arguments, then from `protection_context(...)`. A call with neither is recorded as `default-agent` / `default-session`. With `ProtectionConfig(require_context=True)`, such a call is blocked instead.

## Nested tools

A protected tool that calls another protected tool is decided again for the inner call. The inner decision records `parent_call_id`, which is the outer call's `call_id`, so the evidence shows the call tree. The parent is tracked with a context variable, so it follows `asyncio` tasks and resets after the outer call returns.

## Tools registered at runtime

`watch(agent, protection=...)` and `protect_agent(...)` protect the tools that exist when they are called. A tool added to the agent later is not covered. Keep runtime-registered tools in a `ProtectedToolRegistry`:

```python
from panoptis.protect import ProtectedToolRegistry

tools = ProtectedToolRegistry(pipeline=pipeline, agent_id="coder")
tools["bash"] = bash_fn                 # protected on insertion
tools.update(more_tools)                # every path protects: update, setdefault, register
@tools.register(name="deploy")
def deploy(env: str): ...
```

It accepts plain callables and LangChain, smolagents and AutoGen tools; framework tools are protected in place.

## Double wrapping

Protecting an already protected callable or framework tool returns it unchanged, so it is never decided or run twice. `watch()` is idempotent in the same way.

## `watch(agent, protection=...)` never degrades silently

When enforcement is requested, `watch()` raises instead of returning an unprotected agent in these cases:

- instrumentation fails;
- a tool-like method is read-only (for example on a slotted class);
- the agent has no tool-like method to enforce on;
- a framework agent has no tools.

Without `protection=`, `watch()` keeps its observation-only, invisible-when-healthy behaviour.

## Observation-only mode

```python
pipeline = ProtectionPipeline(config=ProtectionConfig(mode="observe"))
```

This is a compatibility mode for rolling out policies. Every call is decided and recorded, but nothing is enforced. It differs from enforcement in four ways:

| | `mode="enforce"` (default) | `mode="observe"` |
|---|---|---|
| Blocked or escalated call | raises `ActionBlocked`, never runs | **runs**; recorded with `would_block: true` |
| Mutated arguments | the tool gets the redacted arguments | the tool gets the **original** arguments |
| Unsafe output | sanitized | returned **unchanged**; findings are recorded (streams are not inspected) |
| Approvals | waited for | not requested to wait |

Two things stay the same in both modes:

- **The kill switch and suspensions** block.
- **Enforcement failures** (an error inside the pipeline) block.

It is visible as observe mode in three places:

- every decision record carries `"mode": "observe"`;
- `pipeline.status()["mode"]` reports `"observe"`;
- a warning is logged when such a pipeline is created.

Tests: `tests/protect/test_protect_tool.py`, `tests/protect/test_generic_and_direct.py`, `tests/protect/test_enforce.py`.
