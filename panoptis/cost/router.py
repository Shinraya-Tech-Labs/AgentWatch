"""
Legacy compatibility wrapper for ModelRouter.
Please use panoptis.infrastructure.router instead.
"""

from panoptis.infrastructure.router import CircuitState, ModelRouter, ProviderHealth

__all__ = ["ModelRouter", "ProviderHealth", "CircuitState"]
