"""
Base adapter utilities, including optional semantic caching hooks.
"""

import logging
from typing import Any

from panoptis.cost.semantic_cache import SemanticCache

logger = logging.getLogger(__name__)

# Global cache instance, disabled by default
_global_semantic_cache: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache | None:
    return _global_semantic_cache


def set_semantic_cache(cache: SemanticCache | None):
    """Enable or disable global semantic caching."""
    global _global_semantic_cache
    _global_semantic_cache = cache


async def before_provider_call(prompt: str, cache: SemanticCache | None = None) -> str | None:
    """
    Check the semantic cache before making a provider call.
    Returns the cached response if a semantic hit is found, else None.
    """
    cache_instance = cache or _global_semantic_cache
    if not cache_instance:
        return None

    try:
        hit = await cache_instance.get(prompt)
        if hit:
            logger.info("Semantic cache hit for prompt.")
            return hit
    except Exception as e:
        logger.warning(f"Error checking semantic cache: {e}")

    return None


async def after_provider_call(
    prompt: str,
    response: str,
    metadata: dict[str, Any] | None = None,
    cache: SemanticCache | None = None,
) -> None:
    """
    Store the response in the semantic cache after a provider call.
    """
    cache_instance = cache or _global_semantic_cache
    if not cache_instance:
        return

    try:
        await cache_instance.set(prompt, response, metadata)
    except Exception as e:
        logger.warning(f"Error storing in semantic cache: {e}")
