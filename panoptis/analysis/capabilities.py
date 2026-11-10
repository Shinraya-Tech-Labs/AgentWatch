"""Capability registry: every user-facing v3 capability with its maturity and the
tests/benchmark results that back the label (ADR-0010). Labels are not upgraded because
a feature "looks good"; VALIDATED requires committed AWBench results meeting thresholds."""

from __future__ import annotations

from panoptis.analysis.maturity import Capability, Maturity, capabilities, register

_DEFINED = False


def all_capabilities() -> list[Capability]:
    global _DEFINED
    if not _DEFINED:
        _define()
        _DEFINED = True
    return capabilities()


def _define() -> None:
    E, V = Maturity.EXPERIMENTAL, Maturity.VALIDATED  # noqa: N806 - table shorthand
    items = [
        (
            "evidence.immutable_store",
            "1",
            V,
            "append-only observations, DB-enforced immutability, sealed Merkle segments",
            ("tests/v3/test_evidence.py",),
        ),
        (
            "evidence.edge_redaction",
            "1",
            V,
            "secret redaction before storage with manifests; inputs never mutated",
            ("tests/v3/test_evidence.py",),
        ),
        (
            "evidence.crypto_shredding",
            "1",
            V,
            "per-subject payload encryption; erasure destroys the key and purges derived copies",
            ("tests/v3/test_erasure.py",),
        ),
        (
            "events.normalization.native",
            "1",
            V,
            "native SDK spans → computational events",
            ("tests/v3/test_normalizers.py",),
        ),
        (
            "events.normalization.legacy",
            "1",
            V,
            "v0.2 AgentEvent translation with loss accounting",
            ("tests/v3/test_normalizers.py",),
        ),
        ("events.normalization.otel", "2", E, "OTel/GenAI spans → events", ()),
        (
            "events.normalization.otel_extension",
            "1",
            E,
            "PANOPTIS panoptis.* OTel attributes: artifact operations, declared lineage "
            "(ADR-0018; PANOPTIS's own convention, not an OTel standard)",
            ("tests/v3/test_normalizers.py",),
        ),
        (
            "events.normalization.langchain",
            "1",
            E,
            "LangChain callbacks → events (parent_run_id preserved)",
            (),
        ),
        (
            "events.normalization.claude_code",
            "1",
            E,
            "Claude Code stream-json/transcripts → events",
            (),
        ),
        (
            "events.normalization.provider_sdks",
            "1",
            E,
            "OpenAI/Anthropic client wrappers, MCP tap",
            (),
        ),
        (
            "graph.execution",
            "1",
            V,
            "declared-structure execution graph",
            # H2 execution_f1 1.0 on the development set and on the FIRST scored run of each
            # held-out architecture (map_reduce, hybrid_rag_cache, async_event_pipeline)
            (
                "benchmarks/awbench/results/awbench-20260926T074623Z.json",
                "benchmarks/awbench/results/awbench-20260926T104846Z.json",
                "benchmarks/awbench/results/awbench-20260926T145401Z.json",
            ),
        ),
        (
            "graph.information",
            "3",
            E,
            "information instances with an evidence hierarchy; ambiguity preserved (ADR-0017). "
            "Held-out precision 0.95, recall of certain links 0.51 (below threshold)",
            ("tests/v3/test_information_lineage.py", "tests/v3/test_graph_invariants.py"),
        ),
        (
            "graph.information.high_fidelity",
            "1",
            V,
            "information graph and lineage when producers' values are passed on as declared "
            "sources (runtime identity, source=, OTel panoptis.information.*): held-out "
            "information P/R 1.0/0.92, lineage F1 0.91 (stub systems)",
            # first scored run of the held-out code_review_pipeline, all thresholds met
            ("benchmarks/awbench/results/awbench-20260926T184322Z.json",),
        ),
        (
            "provenance.ambiguity",
            "1",
            E,
            "ambiguous-provenance records and per-run information-evidence summaries",
            ("tests/v3/test_api_parity.py",),
        ),
        (
            "provenance.lineage",
            "2",
            E,
            "lineage trees and dependents (candidates shown, not expanded)",
            (),
        ),
        ("compare.runs", "1", E, "earliest divergence, structural/resource/information diffs", ()),
        ("behaviour.motifs", "1", E, "7 defined motif detectors", ()),
        ("behaviour.genome", "1", E, "profiles, genomes, candidate distances", ()),
        ("behaviour.drift", "1", E, "permutation-test drift with FDR control", ()),
        (
            "causality.hypotheses",
            "1",
            E,
            "hypotheses with computed evidence classes; causes/effects",
            (),
        ),
        ("lab.replay", "1", E, "replay L0-L3 with reproduction confidence", ()),
        ("lab.branch", "1", E, "branching timelines with substitution", ()),
        (
            "lab.counterfactual",
            "1",
            E,
            "counterfactual framework (SIMULATED/MODEL_ESTIMATED/UNKNOWN)",
            (),
        ),
        ("state.latent", "1", E, "interpretable latent-state baseline", ()),
        (
            "forecasting.trajectory",
            "1",
            E,
            "kNN trajectory forecasts with calibration evaluation",
            (),
        ),
        ("query.engine", "1", E, "structured queries with deterministic NL routing", ()),
    ]
    for name, version, maturity, desc, evidence in items:
        register(Capability(name, version, maturity, desc, evidence))
