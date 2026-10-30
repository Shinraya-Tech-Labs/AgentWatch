# Security scanning: suppressions and how to add one

AgentWatch runs [Bandit](https://bandit.readthedocs.io/) over `panoptis/` on every push and pull
request. This document records how findings are suppressed, why the ones that are suppressed are
acceptable, and what you have to do if you need to add another.

For a project whose subject matter is safety and observability, "we suppressed some findings and
nobody remembers why" is a bad answer. This file exists so that answer never has to be given.

## The rule

**Suppressions live in the code, inline, with a reason. There is no baseline file.**

```python
rng = random.Random(0)  # noqa: S311  # nosec B311 — Monte Carlo sampling, not crypto
```

Three parts, all required:

- `# nosec <TEST-ID>` — scoped to the specific check, never a bare `# nosec`, which suppresses
  everything on the line including checks that haven't been written yet
- `# noqa: <RULE>` — Ruff runs the same checks under different identifiers, so both scanners need
  telling
- **a reason, in prose** — the part that matters. It is what a reviewer, an auditor, or you in six
  months will actually read

If you add one, say so in the pull request description. A new suppression is a security decision, and
it should be reviewed as one rather than arriving as an unremarked line in a diff.

## Why there is no baseline file

There used to be one — `bandit-baseline.json`, holding nine findings, wired into CI with
`bandit -r panoptis -b bandit-baseline.json`.

By the time it was examined it had stopped doing anything. Every finding it listed had since been
either fixed or suppressed inline with a justification, and Bandit reported **zero findings with the
baseline and zero without it**. Its line numbers no longer matched the code: the entry for
`api/server.py:1042` pointed at a dictionary literal, and the one for `cli/main.py:574` pointed at a
call to `raise_for_status()`. The code had moved and the baseline had not.

That is not a harmless leftover. A baseline is a broad, positional mute: it matches findings by file
and check rather than by the specific line of code that was reviewed. A genuinely new problem landing
near a baselined one can be swallowed without anyone seeing it — and it fails quietly, which is the
worst way for a security control to fail. It also *looks* like diligence, which makes it less likely
anyone will go and check.

So the baseline was removed and CI now runs plain `bandit -r panoptis`. Any new finding fails the
build, and the only way to accept one is to write down why, next to the code, where it will be read.

## The suppressions that exist today

Grouped by check. Each is suppressed inline at the location given.

### B311 — standard pseudo-random generators are not suitable for cryptographic purposes

| location | why it's accepted |
|---|---|
| `orchestration/shapley.py:58` | Monte Carlo sampling for Shapley value estimation. Seeded with a constant (`random.Random(0)`) *deliberately*, so attribution results are reproducible. A cryptographic RNG would be both slower and non-reproducible, which is the opposite of what's wanted. |
| `platform/prompts.py:90` | A/B routing between prompt variants. An attacker predicting which variant they get learns nothing and gains nothing. |
| `tracing/sampling.py:34, 58, 86` | Trace sampling decisions, including reservoir sampling. Sampling is a performance mechanism, not an access control — predicting whether your own trace is kept has no security consequence. |
| `cli/animator.py:48, 72` | Choosing characters for a terminal animation. |
| `behaviour/drift.py:101` | Permutation test for empirical drift detection. Seeded statistical sampling where reproducibility is required; not a security control. |
| `behaviour/profile.py:211` | Bootstrap confidence intervals for agent behavioral profiles. Seeded statistical resampling; not a security control. |
| `state/latent.py:83` | Seeded K-means centroid initialization for latent state clustering. Deterministic clustering for repeatable analysis; not cryptographic. |

The common thread: none of these values gates access to anything, and none is a secret. `secrets` would
buy nothing except a slower RNG and, in the Shapley case, a loss of reproducibility.

### B101 — use of assert detected

| location | why it's accepted |
|---|---|
| `events/normalize.py:147` | Internal invariant check ensuring start or end observation is provided before deriving temporal basis. |
| `events/normalizers/langchain.py:104` | Invariant check asserting observation group contains at least a start or end observation. |
| `events/normalizers/llm_api.py:75` | Invariant check asserting event group contains a request or response observation. |
| `events/normalizers/mcp.py:77, 121` | Invariant check asserting MCP message group contains a request or response. |
| `events/normalizers/native.py:165` | Invariant check asserting event builder receives a start or end observation. |
| `state/latent.py:150` | Invariant check verifying silhouette evaluation loop executed and yielded a cluster configuration. |
| `storage/store.py:502` | Invariant check verifying data key was retrieved or auto-created for the subject. |

### B404 / B603 — subprocess usage

| location | why it's accepted |
|---|---|
| `plugins/sandbox.py:129, 131` | `SandboxedPlugin.safe_exec` is the *permission-enforced* execution path, and its defences are the reason it exists: `cmd` must be a **list** — a shell string is rejected outright — so the OS exec family is used directly and shell metacharacters (`;`, `\|`, backticks) are never interpreted; `shell=False` is passed explicitly; and the call is gated behind `self._check("subprocess_exec", …)`, an allow-list check on the plugin's declared permissions. Bandit cannot see any of that; it sees `subprocess`. |
| `cli/_utils/run_cmd.py:10, 100, 154` | The CLI's own command runner. Commands are constructed by AgentWatch from list literals, not assembled from user input. |
| `cli/main.py:2424, 2444` | A `docker info` probe to check whether Docker is available. The path comes from `shutil.which("docker")`, and the argument list is a literal. |

### B104 — possible binding to all interfaces

| location | why it's accepted |
|---|---|
| `security/exfiltration.py:29` | Not a bind. `_ALLOWLIST = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}` is a set of *string literals* the exfiltration detector matches network destinations against. Bandit flagged the detection pattern itself — in the module whose job is spotting exfiltration. |

### B105 — possible hardcoded password

| location | why it's accepted |
|---|---|
| `circuit_breaker/breaker.py:52` | `TOKEN_BUDGET = "token_budget_exceeded"` — an enum member naming a trip reason. The word "token" is doing the work here, not a credential. |
| `security/entitlement_store.py:27` | `_TOKEN_KEY = "entitlement_token"` — the *name of a key* in a TOML file, not its value. |

### B110 — try/except/pass

| location | why it's accepted |
|---|---|
| `adapters/langchain.py:117` | Parsing optional token-usage metadata off a LangChain response. The shape varies across LangChain versions and providers, and it is genuinely optional — a malformed or absent block means the trace records no token counts, which is the correct outcome. Failing the user's agent run because a telemetry field couldn't be parsed would be a much worse bug than the one this suppression hides. |
| `sensors/groq_sensor.py` | Defensive degradation across Groq API responses, token usage telemetry, payload extraction, and optional SDK imports. Telemetry extraction failures must never interrupt the primary application execution flow. |
| `sensors/openrouter_sensor.py` | Defensive degradation across OpenRouter OpenAI-compatible payloads, token usage extraction, and optional SDK detection. Telemetry parsing must fail open without crashing calling processes. |

### B613 — bidirectional control characters

| location | why it's accepted |
|---|---|
| `core/injection.py:26` | The bidi characters are *in a detection regex*, exactly as intended — this is the check that catches bidi-override prompt injection. Same shape as the `exfiltration.py` finding: the scanner flagged the detector for containing the thing it detects. |

## Reviewing this list

Two of the nine originally-baselined findings turned out to be Bandit flagging AgentWatch's own
detection patterns — the `0.0.0.0` in the exfiltration allow-list, and the bidi characters in the
injection regex. That's a predictable hazard for a security tool, and it's worth recognising the shape
when it recurs: a finding *inside a detector* usually means the detector is working.

The rest are ordinary and unremarkable. None of them is load-bearing for security, and each says so at
the point of use.
