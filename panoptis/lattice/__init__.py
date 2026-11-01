"""v2 invariant lattice: decide on actions by simulating their effects, not their syntax."""

from __future__ import annotations

from panoptis.lattice.attention_scatter import (
    AttentionScatterDetector,
    AttentionScatterReport,
    compute_scatter_score,
)
from panoptis.lattice.shadow_filesystem import (
    CRITICAL_SYSTEM_PATHS,
    FileAction,
    FileOperation,
    MutationResult,
    MutationType,
    ShadowFilesystem,
)

__all__ = [
    "AttentionScatterDetector",
    "AttentionScatterReport",
    "CRITICAL_SYSTEM_PATHS",
    "FileAction",
    "FileOperation",
    "MutationResult",
    "MutationType",
    "ShadowFilesystem",
    "compute_scatter_score",
]
