# Feature Dossier 07: Behavioral Genome & Statistical Drift Testing

**Module Path**: [`panoptis/behaviour/profile.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/behaviour/profile.py) & [`panoptis/behaviour/drift.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/behaviour/drift.py)  
**System Plane**: Plane 3 (Analysis & Attribution Plane)  
**Primary Maintainer**: AI Behavioral Genetics & Statistical Drift Team  
**Obsidian Backlinks**: [[01_ARCHITECTURE_AND_PLANES]], [[01_TEMPORAL_CONTRACTS_AND_EVIDENCELAB]], [[02_COMPUTATIONAL_EVENT_ALGEBRA]], [[04_HYPEREDGE_GRAPH_AND_PROVENANCE]], [[06_LATENT_STATE_AND_MOTIFS]]

---

## 1. Executive Summary & Epistemic Thesis

A fundamental problem in deploying LLM-based autonomous agents to production is **Behavioral Drift**. Unlike deterministic software where identical code yields identical execution paths, autonomous agents exhibit high stochastic variance. Upstream model updates (e.g., an unannounced closed-source API checkpoint update), vector index re-ranking tweaks, or prompt modifications frequently induce subtle, catastrophic regressions:
- An agent stops using a specialized bash tool and falls back to hallucinating file contents.
- Tool usage entropy collapses, concentrating calls on an unreliable fallback endpoint.
- Multi-agent collaboration degrades into excessive delegation handoffs.
- Context window consumption inflates exponentially across releases.

Traditional APM metrics (HTTP 500 rates, p99 latency) fail to detect these subtle structural regressions until downstream customers complain. 

AgentWatch solves this with **Behavioral Genetics for AI Agents**:
1. **The Behavioral Profile (`profile.py`)**: A versioned 25-trait phenotypic fingerprint derived deterministically from an individual execution graph, incorporating topological graph depth, Shannon entropy of tool/delegation distributions, memory dependency, and resource expenditures.
2. **The Behavioral Genome (`profile.py`)**: A statistical aggregation of profiles across a release scope (e.g., `v1.2` vs `v1.3` or `prompt_A` vs `prompt_B`) equipped with non-parametric **Bootstrap Confidence Intervals (95% CI)**.
3. **Statistical Drift Testing (`drift.py`)**: A rigorous two-sample permutation test framework ($R = 2000$ permutations) coupled with **Benjamini-Hochberg False Discovery Rate (FDR)** multi-hypothesis correction to guarantee mathematical rigor without false alarm fatigue.

```
                    BEHAVIORAL DRIFT PIPELINE IN AGENTWATCH
  
  Scope A (Baseline: v1.2)                  Scope B (Candidate: v1.3)
  [Run 1] [Run 2] ... [Run N]              [Run 1] [Run 2] ... [Run M]
         │                                        │
         ▼                                        ▼
  [25-Trait Phenotypic Profiles]           [25-Trait Phenotypic Profiles]
  (Entropy, Graph Depth, Memory)           (Entropy, Graph Depth, Memory)
         │                                        │
         ▼                                        ▼
  [Baseline Genome + 95% CI]               [Candidate Genome + 95% CI]
         │                                        │
         └───────────────────┬────────────────────┘
                             │
                             ▼
              [Multivariate Distance Metrics]
              • L1 Standardized Profile Distance
              • Jensen-Shannon Divergence (Event Kinds)
              • Jensen-Shannon Divergence (Motif Counts)
                             │
                             ▼
              [Two-Sample Permutation Test]
              R = 2,000 Permutations per Trait
                             │
                             ▼
              [Benjamini-Hochberg FDR Control]
              Controls False Discovery Rate at alpha = 0.05
                             │
                             ▼
              [CI/CD Gating: PASS / FAIL Drift Report]
```

---

## 2. The 25-Trait Phenotypic Profile (`profile.py`)

A Behavioral Profile is a deterministic mapping from an execution run's multi-view hypergraph into a compact, standardized vector of 25 quantitative traits:

```python
FEATURES: dict[str, str] = {
    # 1. Structural & Topological Complexity
    "events": "number of events in the run",
    "leaf_ops": "tool + model + retrieval + external + memory events",
    "graph_depth": "longest CONTAINS/DEPENDS_ON chain from a root",
    "mean_branching": "mean structural children over events that have children",
    "multi_parent_events": "events with >1 structural parent (DAG confluence)",
    # 2. Reliability & Resilience
    "error_rate": "fraction of leaf operations with ERROR/TIMEOUT status",
    "retry_rate": "RETRIES relations per leaf operation",
    # 3. Tool Usage & Entropy
    "tool_calls": "TOOL_INVOCATION events count",
    "tool_diversity": "distinct tools / tool calls (0 when no tool calls)",
    "tool_entropy": "Shannon entropy (bits) of tool usage",
    # 4. Model & Agent Orchestration
    "model_calls": "MODEL_INVOCATION events count",
    "distinct_models": "distinct model entities invoked",
    "retrievals": "RETRIEVAL events count",
    "delegations": "DELEGATION + MESSAGE events count",
    "delegation_entropy": "Shannon entropy (bits) of (sender->receiver) hand-offs",
    "actors": "distinct actors participating in the run",
    # 5. Information Flow & Memory Coupling
    "content_derivations": "DERIVES_FROM relations (content containment links)",
    "memory_transfers": "TRANSFERS relations (memory write -> read of same key)",
    "memory_dependency": "fraction of model invocations with memory read ancestor",
    # 6. Operational Efficiency & Financial Cost
    "duration_ms": "run wall-clock duration in milliseconds",
    "log_leaf_latency_p50": "log10(1 + median leaf latency ms)",
    "tokens_total": "declared input + output tokens",
    "model_input_bytes_mean": "mean size of model-invocation inputs (bytes)",
    "model_output_bytes_mean": "mean size of model-invocation outputs (bytes)",
    "cost_usd": "declared monetary cost in USD",
}
```

### 2.1 Information-Theoretic Entropy Formulations
A critical innovation in AgentWatch is the calculation of **Shannon Information Entropy** to monitor behavioral diversity. When an agent enters an obsessive loop or forgets how to use its tools, its entropy drops toward zero.

#### Tool Usage Entropy ($H_{\text{tool}}$)
Let $T = \{t_1, t_2, \dots, t_k\}$ be the set of unique tools invoked, with frequencies $n_i$ and total tool calls $N = \sum n_i$. The probability distribution is $p_i = n_i / N$. The tool entropy in bits is:
$$H_{\text{tool}} = - \sum_{i=1}^k p_i \log_2(p_i)$$

If an agent calls 8 different tools evenly, $H_{\text{tool}} = \log_2(8) = 3.0 \text{ bits}$. If an upstream prompt change causes the agent to spam `read_file` 100 times and never use compiler tools, $H_{\text{tool}} \to 0.0 \text{ bits}$, triggering immediate drift alarms.

#### Delegation Hand-Off Entropy ($H_{\text{delegation}}$)
In multi-agent systems, let $E_{\text{handoff}} = (A_u, A_v)$ represent an ordered delegation from agent $u$ to agent $v$. Delegation entropy is:
$$H_{\text{delegation}} = - \sum_{(u, v)} p(u, v) \log_2 p(u, v)$$

A sudden drop in delegation entropy indicates that multi-agent routing has collapsed into an isolated pairwise ping-pong loop (related to [[06_LATENT_STATE_AND_MOTIFS]] $M003$).

---

## 3. The Behavioral Genome & Bootstrap Confidence Intervals

A single run is an isolated phenotype; a **Behavioral Genome** characterizes a collective population of runs over a release scope $\mathcal{S}$ (e.g., all CI evaluation runs for commit `c785f1f`).

### 3.1 Non-Parametric Bootstrap Resampling
Because software latency and token expenditures follow heavy-tailed, non-Gaussian distributions, AgentWatch rejects standard Student's $t$ approximations. It computes **Empirical Percentile Bootstrap Confidence Intervals** ($B = 1000$ resamples with replacement):

```python
def _bootstrap_ci(
    values: list[float], n_boot: int = 1000, alpha: float = 0.05
) -> tuple[float, float]:
    """Computes a 95% bootstrap confidence interval with fixed deterministic seed."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(42)  # noqa: S311 # nosec B311 - seeded statistical bootstrap
    n = len(values)
    means = [mean(rng.choices(values, k=n)) for _ in range(n_boot)]
    means.sort()
    lower_idx = int((alpha / 2) * n_boot)
    upper_idx = int((1 - alpha / 2) * n_boot)
    return (round(means[lower_idx], 4), round(means[upper_idx], 4))
```

For every trait $f \in \text{FEATURES}$, the genome publishes:
$$\text{Trait Summary}(f) = \Big\langle \bar{x}_f, \text{median}_f, \text{CI}_{0.025}(f), \text{CI}_{0.975}(f), \sigma_f \Big\rangle$$

---

## 4. Multivariate Distance Candidates (`drift.py`)

To quantify the divergence between baseline population $\mathcal{A}$ and candidate population $\mathcal{B}$, AgentWatch evaluates three orthogonal distance metrics:

### 4.1 Standardized L1 Feature Distance
To combine heterogeneous units (milliseconds, dollars, entropy bits, integers) into a single scalar, each feature delta is scaled by the **pooled standard deviation**:

$$D_{L1}(\mathcal{A}, \mathcal{B}) = \sum_{f \in \text{FEATURES}} \frac{|\bar{x}_{f, \mathcal{A}} - \bar{x}_{f, \mathcal{B}}|}{s_{\text{pooled}}(f)}$$

where:
$$s_{\text{pooled}}(f) = \sqrt{\frac{(N_{\mathcal{A}} - 1) s_{\mathcal{A}}^2 + (N_{\mathcal{B}} - 1) s_{\mathcal{B}}^2}{N_{\mathcal{A}} + N_{\mathcal{B}} - 2}}$$

If pooled variance is zero, $s_{\text{pooled}}$ falls back to $|\bar{x}_{\text{pooled}}| \lor 1.0$ to guarantee numerical stability.

### 4.2 Jensen-Shannon Divergence ($D_{\text{JS}}$) over Categorical Distributions
AgentWatch evaluates discrete distribution shifts over event kinds and motif frequencies using the symmetric, bounded Jensen-Shannon Divergence ($D_{\text{JS}} \in [0, 1]$):

$$D_{\text{JS}}(P \parallel Q) = \frac{1}{2} D_{\text{KL}}(P \parallel M) + \frac{1}{2} D_{\text{KL}}(Q \parallel M), \quad M = \frac{1}{2}(P + Q)$$

```python
def _js(p: dict[str, float], q: dict[str, float]) -> float:
    keys = set(p) | set(q)
    sp, sq = sum(p.values()) or 1.0, sum(q.values()) or 1.0
    pp = {k: p.get(k, 0.0) / sp for k in keys}
    qq = {k: q.get(k, 0.0) / sq for k in keys}
    m = {k: (pp[k] + qq[k]) / 2 for k in keys}

    def kl(a: dict[str, float]) -> float:
        return sum(a[k] * math.log2(a[k] / m[k]) for k in keys if a[k] > 0)

    return round(0.5 * kl(pp) + 0.5 * kl(qq), 6)
```

---

## 5. Two-Sample Permutation Hypothesis Testing

When evaluating whether a candidate version has drifted on trait $f$, parametric tests (like ANOVA or Student's $t$) make invalid normality assumptions. AgentWatch employs exact **Two-Sample Monte Carlo Permutation Tests**:

1. **Null Hypothesis ($H_0$)**: The baseline and candidate runs originate from identical underlying distributions ($F_{\mathcal{A}} = F_{\mathcal{B}}$).
2. **Observed Test Statistic**: The absolute difference in sample means:
   $$T_{\text{obs}} = |\bar{x}_{\mathcal{A}} - \bar{x}_{\mathcal{B}}|$$
3. **Resampling under $H_0$**: The combined pool $\mathcal{A} \cup \mathcal{B}$ ($N = N_{\mathcal{A}} + N_{\mathcal{B}}$) is randomly shuffled $R = 2000$ times using a seeded PRNG (`random.Random(42)`). In each iteration $r$, the pool is partitioned into synthetic groups $\mathcal{A}_r'$ (size $N_{\mathcal{A}}$) and $\mathcal{B}_r'$ (size $N_{\mathcal{B}}$), yielding surrogate statistic $T_r = |\bar{x}_{\mathcal{A}_r'} - \bar{x}_{\mathcal{B}_r'}|$.
4. **Empirical $p$-value Calculation**:
   $$p = \frac{1 + \sum_{r=1}^R \mathbb{I}(T_r \ge T_{\text{obs}})}{R + 1}$$

```python
# Permutation test in panoptis/behaviour/drift.py
def _permutation_p(
    va: list[float], vb: list[float], permutations: int = 2000, seed: int = 42
) -> float:
    t_obs = abs(mean(va) - mean(vb))
    combined = va + vb
    na = len(va)
    rng = random.Random(seed)  # noqa: S311 # nosec B311 - seeded statistical sampling
    count = 0
    for _ in range(permutations):
        shuffled = combined[:]
        rng.shuffle(shuffled)
        sa = shuffled[:na]
        sb = shuffled[na:]
        if abs(mean(sa) - mean(sb)) >= t_obs - 1e-9:
            count += 1
    return (count + 1) / (permutations + 1)
```

---

## 6. Benjamini-Hochberg False Discovery Rate (FDR) Control

Testing 25 individual traits simultaneously introduces a severe **Multiple Testing Problem**. If each test operates at significance level $\alpha = 0.05$, the Family-Wise Error Rate (FWER) balloons:
$$\text{FWER} = 1 - (1 - 0.05)^{25} \approx 72.3\%$$
Under naive testing, over $70\%$ of release checks would report false drift alarms!

AgentWatch prevents this by controlling the **False Discovery Rate (FDR)** via the **Benjamini-Hochberg (BH) Procedure**:

```
                     BENJAMINI-HOCHBERG RANKING PROCEDURE
  
  [Raw P-values: p_1, p_2, ..., p_m] (m = 25 traits)
                 │
                 ▼
  [Sort in Ascending Order: p_(1) <= p_(2) <= ... <= p_(m)]
                 │
                 ▼
  [Compute Critical Threshold: (k / m) * alpha]
                 │
                 ▼
  [Find Largest Rank k* such that: p_(k*) <= (k* / m) * alpha]
                 │
                 ▼
  [Declare Drift on all Traits with Rank k <= k*]
  (Calculates adjusted q-values to prevent p-hacking)
```

### 6.1 The Mathematical Algorithm
1. Let $m$ be the number of tested features ($m \le 25$).
2. Sort the calculated permutation $p$-values in ascending order:
   $$p_{(1)} \le p_{(2)} \le \dots \le p_{(m)}$$
3. Compute the Benjamini-Hochberg critical value for each rank $k \in \{1, \dots, m\}$:
   $$c_k = \frac{k}{m} \cdot \alpha \quad (\alpha = 0.05)$$
4. The adjusted false-discovery $q$-value is derived step-up:
   $$q_{(k)} = \min \left( 1.0, \min_{j \ge k} \left\{ \frac{m}{j} p_{(j)} \right\} \right)$$
5. A trait is formally certified as experiencing **Statistically Significant Drift** if and only if:
   $$q \le 0.05$$

```python
def benjamini_hochberg(p_values: dict[str, float]) -> dict[str, float]:
    """Computes monotonic Benjamini-Hochberg adjusted q-values across traits."""
    m = len(p_values)
    if not m:
        return {}
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    q: dict[str, float] = {}
    running_min = 1.0
    for rank in range(m, 0, -1):
        name, p = ordered[rank - 1]
        val = min(1.0, (m / rank) * p)
        running_min = min(running_min, val)
        q[name] = round(running_min, 6)
    return q
```

---

## 7. Sample Size Invariant: The `MIN_RUNS` Guard

AgentWatch enforces a strict empirical sample guard:
$$\text{MIN\_RUNS} = 5$$

If either the baseline scope $\mathcal{A}$ or candidate scope $\mathcal{B}$ contains fewer than 5 execution runs, AgentWatch refuses to compute drift hypothesis tests. It emits an explicit status `status="insufficient_data"`, explaining:
> *"Permutation drift tests require at least 5 runs per group to provide meaningful statistical power. Found $N_{\mathcal{A}}=2, N_{\mathcal{B}}=3$."*

This prevents junior developers or automated scripts from failing CI builds due to high-variance single-run noise.

---

## 8. CI/CD Deployment Gating & Drift Reports

AgentWatch integrates into GitHub Actions via the CLI command `panoptis compare --drift`:

```bash
# In CI Workflow (.github/workflows/regression.yml)
panoptis compare \
    --baseline "scope:tag=v1.2.0" \
    --candidate "scope:commit=HEAD" \
    --alpha 0.05 \
    --output json > drift_report.json
```

### Concrete Output Schema
```json
{
  "status": "DRIFT_DETECTED",
  "sample_sizes": { "baseline": 25, "candidate": 25 },
  "distances": {
    "l1_standardized": 4.821,
    "js_kinds": 0.0412,
    "js_motifs": 0.1895
  },
  "significant_features": [
    {
      "feature": "tool_entropy",
      "baseline_mean": 2.84,
      "candidate_mean": 1.12,
      "raw_p_value": 0.00049,
      "adjusted_q_value": 0.00625,
      "verdict": "DRIFT_CONFIRMED"
    },
    {
      "feature": "retry_rate",
      "baseline_mean": 0.04,
      "candidate_mean": 0.28,
      "raw_p_value": 0.00199,
      "adjusted_q_value": 0.01250,
      "verdict": "DRIFT_CONFIRMED"
    }
  ]
}
```

If any critical trait experiences statistically significant drift ($q \le 0.05$), the CI pipeline exits with code `1`, halting the pull request before bad agent configurations reach production.

---

## 8.1 Programmatic Execution via Python SDK

Developers can run behavioral drift testing directly within their test suites or continuous evaluation pipelines using the native Python API:

```python
from panoptis.query.workspace import Workspace
from panoptis.behaviour.profile import ProfileAnalyzer
from panoptis.behaviour.drift import drift, distances

# Initialize workspace connected to central repository
ws = Workspace.from_env()

# Resolve run IDs for two comparative cohorts
baseline_runs = ws.search_runs(tag="release:v1.2.0")
candidate_runs = ws.search_runs(tag="candidate:v1.3.0-rc1")

# Extract profiles for both scopes
analyzer = ProfileAnalyzer(ws)
profiles_baseline = [analyzer.profile(r["run_id"]) for r in baseline_runs]
profiles_candidate = [analyzer.profile(r["run_id"]) for r in candidate_runs]

# Calculate multivariate distance metrics
dists = distances(profiles_baseline, profiles_candidate)
print(f"L1 Standardized Distance: {dists['l1_standardized']:.4f}")
print(f"Event Kind JSD:           {dists['js_kinds']:.4f}")
print(f"Motif Frequency JSD:      {dists['js_motifs']:.4f}")

# Execute two-sample permutation tests with Benjamini-Hochberg FDR control
drift_report = drift(profiles_baseline, profiles_candidate, permutations=2000, alpha=0.05, seed=42)

# Inspect certified drift anomalies
for trait, result in drift_report["traits"].items():
    if result["drift_detected"]:
        print(f"🚨 Significant drift in trait '{trait}':")
        print(
            f"   Baseline Mean: {result['baseline_mean']:.3f} | Candidate Mean: {result['candidate_mean']:.3f}"
        )
        print(f"   Adjusted q-value: {result['q_value']:.5f} (raw p: {result['p_value']:.5f})")
```

---

## 8.2 Mathematical Proof of FDR Control Under Positive Dependency

In multi-agent telemetry, traits such as `events`, `leaf_ops`, and `duration_ms` exhibit positive regression dependency (PRDS - Positive Regression Dependency on each one from a Subset). 

Benjamini and Yekutieli (2001) proved that the standard step-up Benjamini-Hochberg procedure controls the False Discovery Rate at level $\le \frac{m_0}{m} \alpha \le \alpha$ for test statistics that satisfy PRDS on the subset of true null hypotheses:

$$\mathbb{E}[\text{FDR}] = \mathbb{E}\left[ \frac{V}{\max(R, 1)} \right] \le \frac{m_0}{m} \alpha \le \alpha$$

Where:
- $m_0$ is the number of true null hypotheses (traits without true drift).
- $m$ is the total number of tested traits ($m=25$).
- $V$ is the number of false discoveries (false alarms).
- $R$ is the total number of declared discoveries.

By implementing step-up rank ordering with monotonic fallback (`running_min`), AgentWatch guarantees that even when traits are correlated (e.g., token consumption scales with duration), the expected false discovery rate is strictly bounded below 5%.

---

## 9. Verification Suite & Unit Proofs

The behavioral profile and drift subsystems are verified in [`tests/v3/test_analysis.py`](file:///data/data/com.termux/files/home/AgentWatch/tests/v3/test_analysis.py):

```text
tests/v3/test_analysis.py
├── test_benjamini_hochberg_ordering             [PASSED]
├── test_drift_insufficient_runs_suppression     [PASSED]
├── test_permutation_test_seed_determinism       [PASSED]
├── test_jensen_shannon_bounded_zero_to_one      [PASSED]
└── test_genome_bootstrap_confidence_intervals  [PASSED]
```

### Key Verified Invariants
1. **BH Monotonicity**: Asserts that adjusted $q$-values maintain strict monotonicity: $q_{(1)} \le q_{(2)} \le \dots \le q_{(m)}$.
2. **Seed Determinism**: Confirms that two identical calls to `drift()` with fixed seed produce 100% bit-identical $p$-values and $q$-values.
3. **Empty Anchor Guard**: Confirms that empty run scopes gracefully return empty profiles without unhandled exceptions.

---

## 10. External Standards & Academic Citations

*Researched and verified via `agent-reach` across statistics, information theory, and machine learning literature:*

1. **Benjamini, Y., & Hochberg, Y. (1995)**: *"Controlling the False Discovery Rate: A Practical and Powerful Approach to Multiple Testing"*. Journal of the Royal Statistical Society: Series B (Methodological), 57(1), 289–300.
   - *Influence*: Foundation for multi-hypothesis FDR control in `panoptis/behaviour/drift.py`.
2. **Shannon, C. E. (1948)**: *"A Mathematical Theory of Communication"*. Bell System Technical Journal, 27(3), 379–423.
   - *Influence*: Mathematical formulation of tool and delegation diversity entropy in `panoptis/behaviour/profile.py`.
3. **Efron, B. (1979)**: *"Bootstrap Methods: Another Look at the Jackknife"*. The Annals of Statistics, 7(1), 1–26.
   - *Influence*: Non-parametric confidence interval estimation for behavioral genome traits.
4. **Lin, J. (1991)**: *"Divergence Measures Based on the Shannon Entropy"*. IEEE Transactions on Information Theory, 37(1), 145–151.
   - *Influence*: Mathematical properties of bounded Jensen-Shannon divergence ($D_{\text{JS}}$).
5. **Good, P. (2000)**: *"Permutation Tests: A Practical Guide to Resampling Methods for Testing Hypotheses"*. Springer Science & Business Media.
   - *Influence*: Exact Monte Carlo permutation test implementations for distribution-free hypothesis testing.

---

## 11. Cross-Reference Backlinks & Obsidian Map

| Concept / Dossier | Architectural Relationship | Obsidian Backlink |
| :--- | :--- | :--- |
| **Architecture Overview** | Analysis & Attribution Plane (Plane 3) | [[01_ARCHITECTURE_AND_PLANES]] |
| **EvidenceLab Contracts** | Contract verification over drift thresholds | [[01_TEMPORAL_CONTRACTS_AND_EVIDENCELAB]] |
| **Computational Event Algebra** | Raw event attributes forming profile inputs | [[02_COMPUTATIONAL_EVENT_ALGEBRA]] |
| **Hyperedge Graph Store** | Graph depth and structural metrics | [[04_HYPEREDGE_GRAPH_AND_PROVENANCE]] |
| **Latent State & Motifs** | Motif frequencies fed into JSD divergence | [[06_LATENT_STATE_AND_MOTIFS]] |
| **Cost Governance & Router** | Cost USD and token metrics tracked in profiles | [[10_COST_GOVERNANCE_AND_CACHE]] |
| **Roadmap Issue #734** | Autonomous drift detection and remediation | [[06_ROADMAP_AND_ISSUES_729_738]] |
