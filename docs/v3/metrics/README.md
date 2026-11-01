# Metric definitions

Every number AgentWatch exposes beyond raw counts is defined here. All are **EXPERIMENTAL** unless `panoptis status` says otherwise.

## Provenance (`panoptis/provenance/lineage.py`)

| Metric | Definition |
|---|---|
| `provenance_depth` | Length of the longest path in the lineage tree from the queried node to a node with no INFORMATION-view ancestors. Relations used: PRODUCES, CONSUMES, DERIVES_FROM, CONTAINS_ITEM, TRANSFERS. Traversal is time-respecting. |
| `transformations` | Number of distinct events in the lineage tree. |
| `origins` | Lineage leaves (nodes with no further ancestors). |
| `relations_by_basis` | Count of lineage steps per relation basis (DECLARED, CONTENT_MATCH, KEY_MATCH). |
| `weakest_path_confidence` | Minimum, over root-to-leaf paths, of the minimum relation confidence along the path. |
| `generated_without_traced_input` | Lineage leaves that are events other than EXTERNAL_INPUT or RETRIEVAL, i.e. content whose inputs were not traced. This measures *traceability*, not truth or factuality. |

**Content containment** (DERIVES_FROM, basis CONTENT_MATCH): for artifacts *y* (earlier) and *x* (later) in the same run, compute word 5-gram shingle sets *S(y)* and *S(x)*. Require |S(y)| ≥ 3. Emit a relation when |S(x) ∩ S(y)| / |S(y)| ≥ 0.6. The confidence equals that ratio. The computation is exact, using a prefix-filtered similarity join.

## Comparison (`panoptis/compare/runs.py`)

| Metric | Definition |
|---|---|
| signature | `kind \| operation \| object` of an event |
| `alignment_similarity` | difflib `SequenceMatcher.ratio()` over the two runs' signature sequences, in time order |
| earliest divergence | The earlier (by position in run B) of (a) the first non-equal alignment opcode and (b) the first aligned pair with a different status, output artifact set or input artifact set |
| cone share | Share of run B's events, leaf latency and tokens that lie in the divergence event's EXECUTION + INFORMATION descendant cone. This is a **dependency** share, not a causal attribution. |

## Behaviour profile, features v2 (`panoptis/behaviour/profile.py`)

Definitions are in the `FEATURES` dict. The code is the single source.

Examples:

- `retry_rate` = RETRIES relations / leaf operations
- `delegation_entropy` = Shannon entropy (bits) of sender→receiver pairs
- `memory_dependency` = share of model invocations with a memory read among their INFORMATION ancestors
- `model_input_bytes_mean` / `model_output_bytes_mean` = mean artifact bytes per model invocation

**Genome:** feature means with 1,000-sample bootstrap 95% CIs (seed 7), plus motif frequency (share of runs with ≥1 instance).

## Distances and drift (`panoptis/behaviour/drift.py`)

| Name | Definition |
|---|---|
| `scaled_l1_features` | Mean over features of \|mean_A − mean_B\| / pooled population s.d. |
| `js_kind_distribution` | Jensen–Shannon divergence (base 2) of summed event-kind distributions |
| `js_motif_distribution` | JS divergence of summed motif counts (null when either side has none) |
| `energy_distance` | Energy distance of s.d.-scaled feature vectors (needs ≥2 runs per side) |
| drift test | Per-feature two-sample permutation test on the difference of means (2,000 permutations, seed 11), Benjamini–Hochberg q-values, α = 0.05 |
| `min_achievable_q` | max(1/2001, 2/C(n_a+n_b, n_a)) × number of features. When it is ≥ α the status is `underpowered`. |

No distance is canonical. See RESEARCH_HYPOTHESES H6.

## Confidence values

Every `confidence` object carries `calibrated: false` unless a calibration model has been fitted. Treat uncalibrated values as ordinal only.
