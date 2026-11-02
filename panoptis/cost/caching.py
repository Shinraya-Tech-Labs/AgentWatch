"""Deprecated alias of :mod:`panoptis.cost.semantic_cache` (kept for old imports)."""

import warnings

from panoptis.cost.semantic_cache import SemanticCache

warnings.warn(
    "panoptis.cost.caching is deprecated; import from panoptis.cost.semantic_cache",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["SemanticCache"]
