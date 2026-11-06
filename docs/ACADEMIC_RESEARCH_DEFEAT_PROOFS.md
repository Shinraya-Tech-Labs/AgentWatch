# Regression Tests Inspired by Published Agent Attacks

**System under test:** `panoptis.protect` and `panoptis.contracts`

These are project-written regression tests for forms of attacks described in security research. They check that a component handles the inputs written for it. They are not a reproduction of any paper, an independent evaluation, or evidence that an attack or its adaptive variants cannot succeed.

This file replaces an earlier version that used the words "defeated", "immune", "proof" and "certifying", compared other products without evidence, and quoted test code and APIs that do not exist. Its paper citations were not verified and have been removed, so the sections below name attack classes instead. The earlier text remains in Git history. Current claims are in [CLAIMS.md](CLAIMS.md).

Every component below is **off by default** unless stated otherwise. `ProtectionConfig.sovereign()` turns on several of them together.

## Coverage by attack class

| Attack class | Component | How to enable | Tests |
|---|---|---|---|
| Secrets moved through other encodings | `DynamicTaintTracker`, `InformationFlowEngine` | `enable_ifc` | `test_dynamic_taint_tracker_*`, `test_ifc_blocks_base64_transformed_secret_to_public_sink` |
| A goal split across harmless-looking steps | `LTLTrajectoryVerifier` | `enable_ltl_trajectory` | `test_ltl_blocks_multi_hop_exfiltration`, `test_ltl_flags_untrusted_input_to_shell_chain` |
| Side effects during speculative runs | `SpeculativeTransactor`, `CounterfactualEngine` | `enable_counterfactual_forking` | `test_speculative_transactor_intercepts_network_in_fork` |
| Patching guard functions in memory | `RuntimeIntegrityGuard` | `enable_integrity_guard` | `test_runtime_integrity_guard_detects_memory_monkey_patching` |
| Missing telemetry read as false | `OpenWorldMFOTLMonitor` | Used directly | `test_open_world_universal_preservation_and_refutation` |
| Dormant instructions written to memory | `MemorySentry` | `enable_memory_sentry` | `test_memory_sentry_*` |
| Gradual multi-turn steering | `CrescendoDriftMonitor` | `enable_crescendo_drift` | `test_crescendo_drift_*` |
| Instructions hidden in retrieved documents | `RAGSanitizer` | `enable_rag_sanitizer` | `test_rag_sanitizer_*` |

## What each component does, and where it stops

### Encoded secrets
The tracker looks for **registered** secrets in text, including base64 and hex forms and partial fragments above a similarity threshold. The information-flow engine then refuses a labelled value at a lower-labelled sink.

**Limits:** a secret nobody registered is invisible to it. So is a transformation it does not decode, such as encryption or a custom encoding, or a fragment below the threshold.

### Multi-step trajectories
The verifier records steps per session and applies fixed rules: a sensitive read followed by network egress, untrusted input followed by execution, a credential read followed by a write to a startup path, and dangerous code assembled across files.

**Limits:** despite the name, these are hand-written rules over tool names, argument patterns and code indicators, not a general temporal-logic model checker. It does not track data provenance. A tool or path outside its lists is not recognised. Memory limits and overflow behaviour are described in [THREAT_ANALYSIS.md](THREAT_ANALYSIS.md).

### Speculative side effects
During a counterfactual run the tool executes in a temporary copy of the workspace, and calls to `urllib.request.urlopen` are recorded and answered with a mock.

**Limits:** only `urllib.request.urlopen` is intercepted. `requests`, `httpx`, raw sockets and subprocesses are not, so this is not network isolation. There is no two-phase commit. For isolation, use an operating-system sandbox.

### Runtime integrity
The guard hashes sealed functions and reports `MEMORY_BYTECODE_TAMPERING_DETECTED` when one is replaced or its bytecode changes.

**Limits:** this is detection inside the same process, not isolation. Code with the same privileges can change the guard or anything it did not seal. `panoptis/protect/kernel_sandbox.py` is a separate, Linux-only mechanism.

### Missing telemetry
The monitor uses three-valued logic, so an open-world formula with missing facts evaluates to `UNKNOWN` instead of collapsing to true or false.

**Limits:** it reports uncertainty and does not recover missing events. Callers decide what `UNKNOWN` means.

### Memory poisoning
`MemorySentry` inspects text at write time for conditional triggers and attempts to redefine trusted endpoints, and marks indirect memories as untrusted.

**Limits:** it is pattern-based, so a paraphrase that matches no pattern is accepted. It only sees writes routed through it.

### Multi-turn steering
The monitor compares each turn with the session's stated objective and tracks rising risk across consecutive turns.

**Limits:** it is a heuristic with thresholds. Slow or indirect steering can stay under them, and legitimate conversations can cross them.

### Poisoned retrieval
`RAGSanitizer` rewrites imperative phrases into passive text, strips invisible characters and delimiters, and blocks chunks with a high ratio of command words.

**Limits:** it is pattern-based and does not detect false statements written as ordinary prose.

## Running the tests

```bash
pytest tests/protect/test_academic_frontier.py tests/protect/test_academic_frontier_v2.py tests/contracts/test_mfotl_engine.py -v
```
