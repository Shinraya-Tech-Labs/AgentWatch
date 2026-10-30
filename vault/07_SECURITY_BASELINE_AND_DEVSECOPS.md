# 07 — Security Baseline & DevSecOps Standards

> **Primary Policy File:** [`SECURITY-BASELINE.md`](file:///data/data/com.termux/files/home/AgentWatch/SECURITY-BASELINE.md)  
> **CI Workflows:** [`.github/workflows/`](file:///data/data/com.termux/files/home/AgentWatch/.github/workflows/)  

---

## 🛡️ The Zero-Baseline Security Philosophy

AgentWatch rejects global security baseline files (such as `bandit-baseline.json`). 

### Why Baseline Files Are Prohibited
1. **Positional Mute Vulnerability**: Baseline files match issues by file and line number. When code moves, the baseline drifts silently—either suppressing a new vulnerability that lands near an old location, or pointing at harmless lines.
2. **False Diligence**: A passing scan with a 50-item baseline file looks clean on CI dashboards, giving developers a false sense of security while critical issues remain unreviewed.

### The Mandatory Inline Rule
Every security suppression must live directly in the code, on the line affected, formatted as:

```python
rng = random.Random(seed)  # noqa: S311  # nosec B311 — seeded statistics, not security
assert anchor is not None  # nosec B101 — invariant: caller must pass start or end
```

Three mandatory components:
1. `# nosec <TEST-ID>`: Scoped strictly to the specific Bandit check (never a bare `# nosec`).
2. `# noqa: <RULE>`: Scoped for Ruff linter consistency.
3. **Prose Justification**: An explicit explanation of why standard cryptographic safeguards do not apply to this specific computation.

---

## 📋 Active Suppression Inventory

| Check | Location | Accepted Architectural Justification |
| :--- | :--- | :--- |
| **B311** | `orchestration/shapley.py:58` | Monte Carlo sampling for Shapley value attribution. Seeded with `random.Random(0)` intentionally so attribution results are deterministic and reproducible. |
| **B311** | `platform/prompts.py:90` | A/B routing between prompt variants. No cryptographic or security impact. |
| **B311** | `tracing/sampling.py:34, 58, 86` | Trace reservoir sampling. Performance mechanism, not an access control. |
| **B311** | `panoptis/state/latent.py:72` | Seeded statistical permutation tests and K-means centroid initialization. |
| **B101** | `panoptis/events/normalizer.py:45` | Internal invariant check ensuring caller passed either `start` or `end` timestamp. |
| **B101** | `panoptis/storage/keys.py:88` | Internal invariant check verifying tenant partition keys are formatted properly. |

---

## 🚀 CI/CD Pipeline Matrix

All pushes to `main` and active PRs are gated by GitHub Actions:

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        GitHub Actions Matrix                           │
├──────────────────┬─────────────────┬──────────────────┬────────────────┤
│   Bandit Scan    │    AgentWatch   │     PR Tests     │  Documentation │
│                  │       CI        │                  │     Build      │
├──────────────────┼─────────────────┼──────────────────┼────────────────┤
│ bandit -r        │ ruff check      │ pytest tests/    │ mkdocs build   │
│ panoptis       │ ruff format     │ coverage gate    │ links verify   │
│ (Zero baseline)  │ type check      │ (> 70%)          │                │
└──────────────────┴─────────────────┴──────────────────┴────────────────┘
```

Every PR must achieve 100% green status across all jobs before merge approval.
