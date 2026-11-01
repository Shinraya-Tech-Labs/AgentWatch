"""Built-in analyzers run by the engine after graph construction."""

from __future__ import annotations

from panoptis.analysis.base import Analyzer


def builtin_analyzers() -> list[Analyzer]:
    from panoptis.analysis.entropy import TokenEntropyMonitor
    from panoptis.behaviour.motifs import MotifAnalyzer
    from panoptis.behaviour.profile import ProfileAnalyzer
    from panoptis.provenance.ambiguity import ProvenanceEvidenceAnalyzer

    return [
        MotifAnalyzer(),
        ProfileAnalyzer(),
        ProvenanceEvidenceAnalyzer(),
        TokenEntropyMonitor(),
    ]
