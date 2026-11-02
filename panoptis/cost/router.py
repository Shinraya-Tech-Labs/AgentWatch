"""
Legacy compatibility wrapper for ModelRouter.
Please use panoptis.infrastructure.router instead.
"""

import warnings

from panoptis.infrastructure.router import CircuitState, ModelRouter, ProviderHealth

warnings.warn(
    "panoptis.cost.router is deprecated; import from panoptis.infrastructure.router",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["ModelRouter", "ProviderHealth", "CircuitState"]
