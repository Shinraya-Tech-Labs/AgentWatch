# Loop and stuck-agent detection

An agent can loop while every call succeeds, so the failure-counting circuit breaker misses it. The loop stage of the protection pipeline (`Stage.LOOP`) watches each session's recent calls for repetition that makes no progress. It decides before the call runs.

Code: `panoptis/protect/loops.py` (`LoopDetector`, `LoopConfig`). Tests: `tests/protect/test_loop_detection.py`.

## What is tracked

For each session the detector keeps the last `window` calls (default 10). The key is the pipeline's tenant-scoped `tenant/session`. Each call records:

- the tool;
- a hash of its canonical arguments;
- its meaningful text (the `command`/`query`/`path`/… argument);
- whether the tool mutates, judged by its name;
- its outcome and a hash of its output, once known.

Memory is bounded. At most `max_sessions` sessions are tracked (default 10 000), and the least recently used session is dropped first. A call leaving its window also leaves the call-id index. Call ids are unique per session, not globally.

## Findings

| Code | Fires when | Default threshold | Recommends |
|---|---|---|---|
| `DUPLICATE_CALL_LOOP` | the same tool with identical arguments again, and the most recent identical calls failed or returned the same output | 3rd identical call (`duplicate_threshold`); 9th for polling tools | `verdict` (ESCALATE_HITL) |
| `OSCILLATION_LOOP` | a cycle of 2 or 3 distinct read-only calls (A→B→A→B, A→B→C→A→B→C) whose outputs did not change | `oscillation_cycles` = 2 | `verdict` |
| `SEMANTIC_THRASHING_LOOP` | the same tool with *similar* arguments, after `thrash_failures` similar calls failed with a repeating error | similarity > 0.80, 3 failures | `verdict` |
| `REPEATED_FAILURE` | the same tool failed N times in a row, whatever the arguments | `failure_threshold` = 4 | `verdict` |
| `NO_PROGRESS` | the last 8 calls were read-only and returned at most one new output | `no_progress_window` = 8 | ALLOW (informational) |

Every finding carries its evidence for the dashboard and ledger: the repeated call ids, the similarity scores, and the distinct outputs and failures.

### Similarity

Two calls are similar when either measure is strictly above `similarity_threshold`:

- **Levenshtein similarity:** `1 − distance / max(len)` over the first `similarity_chars` characters (default 256). It is computed with a band, so it never costs more than the threshold allows.
- **Jaccard similarity:** the overlap of the alphanumeric token sets, computed over the full text.

Identical arguments are never "similar"; they are duplicates.

## Progress, polling and legitimate repeats

- **Changing results are progress.** A repeated call counts as a loop only if its recent identical results show no change. Polling whose status changes each time (`running 10%` → `running 20%` → `done`) is never flagged.
- **A stuck poll is flagged.** Two identical results followed by a third identical call is enough.
- **Polling tools get more room.** A tool whose name matches `repeatable_tools` (`wait`, `poll`, `status`, `health`, `tail`, `watch`, …) tolerates `repeatable_multiplier` (3) times the threshold.
- **A new edit resets the count.** A mutating call not seen before in the window means that later identical reads observe a changed state. Edit → test → different edit → the same test is not a duplicate loop. Repeating the *same* edit does not reset the count.
- **Different meaningful arguments are not duplicates.** Reading ten different files is not a loop.
- **Similar arguments need failure evidence.** Similar commands that succeed are never flagged as thrashing. Similar failures whose errors differ each time count as progress.
- **Oscillation ignores mutating calls.** Cycles that include mutating tools are left to the duplicate check, so an edit/test alternation is not called oscillation.

## Enforcement and the circuit breaker

- **Approval.** A confirmed loop asks for human approval by default (`LoopConfig.verdict = ESCALATE_HITL`). It is blocked when there is no approval channel (`ProtectionConfig.escalate_without_approver`). Set `verdict=Verdict.BLOCK` to always block, or ALLOW to only record. An approved loop call runs.
- **Circuit breaker.** After `ProtectionConfig.trip_breaker_after_loops` confirmed loop decisions in a session (default 2), the session's circuit breaker opens. Every further call in that session is then blocked (`CIRCUIT_OPEN`) until the cooldown (`breaker_cooldown_s`) or an operator reset. Other sessions are unaffected.
- **Blocked calls count as failures.** A blocked call is recorded in the loop window as a failure, so retrying a blocked call is itself evidence of a loop.
- **Ledger.** Loop findings are part of the decision record in the evidence ledger (`protect.decision`).

## Limitations and false positives

- **Mutation is guessed from the tool's name** (`write`, `edit`, `delete`, `post`, …). A mutating tool with a neutral name is treated as read-only. Its repeated calls are still checked as duplicates, but they do not reset the count.
- **Outputs are compared by exact hash.** An output that embeds a timestamp always looks new, so a stuck poll whose output carries a changing timestamp is not flagged as a duplicate (`NO_PROGRESS` cannot catch it either). Normalise such outputs in the tool, or lower the thresholds for it.
- **Without recorded results there is no evidence of progress.** If outcomes are never recorded (for example `check` used without `record_result`), identical calls count as no progress. The pipeline always records them.
- **A false positive costs one approval.** Polling tools can be named so that they match `repeatable_tools`, or passed via a custom pattern. A loop decision on a legitimate repeat costs one approval, not a lost action. Thresholds are per pipeline: `ProtectionPipeline(loops=LoopDetector(LoopConfig(...)))`.
