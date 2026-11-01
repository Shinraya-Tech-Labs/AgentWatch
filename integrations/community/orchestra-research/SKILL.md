---
name: panopti-hypervisor
description: Active runtime pre-execution hypervisor and forensic witness court for autonomous AI agents. Intercepts destructive tool calls (SQL injections, shell commands, ungrounded actions), evaluates Metric Temporal Logic (MTL) contracts, enforces compounding confidence decay, and cryptographically seals tamper-proof Merkle receipts in real-time with sub-millisecond overhead.
version: 1.0.0
author: Shinraya Tech Labs
license: Apache-2.0
tags: [Safety Alignment, Guardrails, AI Agents, Runtime Safety, Pre-Execution Intercept, Formal Verification, Temporal Logic, Merkle Witness, Forensics]
dependencies: [panoptis]
---

# Panopti — Active Pre-Execution Hypervisor for Autonomous AI

## Quick Start

Panopti treats every token emitted by an LLM as untrusted user input, interposing active pre-execution guardrails between an agent's reasoning and production mutations.

**Installation**:
```bash
pip install panoptis
```

**Basic Usage (Pre-Execution Intercept)**:
```python
from panopti.guard import PreExecutionGuard

# Initialize active sentinel
guard = PreExecutionGuard(
    confidence_floor=0.40,
    destructive_patterns=[r"(?i)\b(DROP\s+TABLE|TRUNCATE|DELETE\s+FROM)\b"],
)

# Intercept proposed tool intent
decision = guard.evaluate_intent(
    tool_name="execute_sql",
    arguments={"query": "DROP TABLE users;"},
    current_confidence=0.85,
)

if not decision.allowed:
    print(f"🛑 VETO ENFORCED: {decision.reason}")
    # Target database driver is never called!
```

**Interactive Simulation**:
```bash
panopti demo
panopti playground
```
