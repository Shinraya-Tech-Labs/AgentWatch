---
name: panopti-guard
description: Pre-execution checks for AI agent tool calls. Evaluates calls routed through it against risk patterns and contracts such as Disallows and RequiresPrior, and records a hash-chained witness log. It is not a sandbox and does not see calls made outside it.
version: 1.1.0
author: Shinraya Tech Labs
license: Apache-2.0
tags: [Safety Alignment, Guardrails, AI Agents, Runtime Safety, Pre-Execution Checks, Temporal Contracts, Audit Log]
dependencies: [panoptis]
---

# Panopti: pre-execution checks for AI agent tools

## Quick start

Panopti checks a proposed tool call before your code runs it. Your code must call the guard and honour its decision: a call that does not go through the guard is not checked.

**Installation**:
```bash
pip install panoptis
```

**Basic usage**:
```python
from panopti.contracts import Disallows
from panopti.guard import PanoptiGuard

guard = PanoptiGuard(critical_confidence_floor=0.40)
guard.register_contract(Disallows(target_tool="drop_table", reason="Schema drops are not allowed."))

decision = guard.check(tool_name="drop_table", arguments={"table": "users"}, confidence=0.85)

if decision.blocked:
    print(f"Vetoed: {decision.reason}")  # do not run the tool
```

**Illustrations**:
```bash
panopti demo        # scripted illustration: no database, no tools, not the real pipeline
```

`panopti demo` only tells a story. To see the real pipeline, API, MCP gateway and evidence verifier run from a clone of the repository:

```bash
python examples/walkthrough/live_demo.py
```

## Limits

- Detection is pattern-based and contract-based, so it does not catch every harmful call.
- It is not a sandbox and does not isolate the agent's process.
- The witness log is tamper-evident only against a head kept outside it.
