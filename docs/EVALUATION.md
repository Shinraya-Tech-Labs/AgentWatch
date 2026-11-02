# Security evaluation and overhead

The protection pipeline is measured with a reproducible, versioned evaluation (TO-036) and overhead benchmark (TO-037):

```bash
python -m benchmarks.protect_eval.run --out benchmarks/results/protect_eval --iterations 300
```

The command writes `REPORT.md` (methodology, environment, results by threat category and integration, false positives, overhead, limitations), `results.json` (every case's outcome and latency) and `corpus_v1.json` (the exact cases). The committed run is in [`benchmarks/results/protect_eval/`](https://github.com/Shinraya-Tech-Labs/AgentWatch/blob/main/benchmarks/results/protect_eval/REPORT.md).

## Corpus

`benchmarks/protect_eval/corpus.py`, version 1.0.0, contains 82 synthetic, harmless cases, each labelled `attack` or `benign`. Tool calls are analysed, never executed, and the evaluation tools are no-op stubs. Injections only ask for "BANANA", and credentials are fake or the documented AWS example key. Nothing is downloaded.

| Category | Attacks | Benign controls |
|---|---|---|
| dangerous tool arguments (shell, SQL, Python, SSRF, traversal, exfiltration) | 24 | 12 |
| secret exposure (arguments) | 4 | 2 |
| prompt injection and secrets in tool output | 7 | 5 |
| policy violations (RBAC) | 6 | 3 |
| loops | 4 | 3 |
| intent mismatch | 7 | 5 |

Integrations measured on the dangerous-arguments category:

- the pipeline itself;
- `protect_tool`, which also counts tool executions;
- the MCP gateway (in-process JSON-RPC);
- the Claude Code hook, for cases expressible as Claude tools.

## Metrics

| Metric | Meaning |
|---|---|
| detection rate | attacks the decision did not allow (blocked, escalated, mutated or sanitized) |
| block rate / escalate rate | the share of attacks with each verdict |
| false-positive rate | benign controls not allowed; for outputs, not returned byte-for-byte unchanged |
| missed | ids of attacks that were allowed |
| sanitization correctness | for output attacks: the injection or secret was removed **and** the legitimate content kept |
| latency | per-decision wall time, p50/p95/p99 |

## Results (committed run)

On this corpus, every attack category is detected with 0 false positives on its benign controls, and every integration agrees. Output sanitization is correct for 7 of 7 attack outputs, and all 5 benign outputs are returned unchanged. Intent findings escalate rather than block, by design.

The overhead of a decision through `enforce_call` (decide, run a no-op tool, inspect, record) is about 0.26–0.36 ms (p50) without evidence recording; an escalation answered by an approver costs about 0.6 ms. With a local SQLite evidence store, a decision is about 1.5–1.8 ms and an approved escalation about 6 ms, and the p99 is dominated by disk writes. Throughput is about 2,800–3,300 decisions/s on the measured machine. See the report for the exact numbers and environment.

## Read these numbers with care

- **The corpus was written by the same team as the rules.** A perfect score shows the implemented rules handle *these* cases. It does not show that unseen attacks are caught. Paraphrased injections and novel command forms can evade pattern rules.
- **The misses the first run found have since been fixed.** The first run found 2 real defects, which were then fixed:
  - `read_file ../../../../etc/shadow` was allowed;
  - an injected HTML comment caused the whole line around it to be dropped.

  The corpus was **not** changed to make them pass. This is a known source of optimism: new cases that were not used during development are the better measure. Add them, with a new corpus version.
- **No external dataset was used, and no competitor comparison is made.**
- **Hardware and disk affect latency.** Set performance targets from product requirements, not from this run.

## Reliability

Timeouts, cancellation, upstream failures and degraded dependencies are covered by tests rather than timed:

- the gateway's upstream timeout, disconnect and error;
- approval timeout, cancellation and emergency-stop withdrawal;
- fail-closed enforcement errors;
- store failure in the API;
- an unreachable API in the CLI.

The report lists the test files.
