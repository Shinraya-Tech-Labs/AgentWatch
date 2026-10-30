import datetime

import pytest

from panoptis.adapters.base import after_provider_call, before_provider_call, set_semantic_cache
from panoptis.cost.semantic_cache import SemanticCache
from panoptis.memory.engine import EmbeddingProvider


@pytest.mark.asyncio
async def test_semantic_cache_exact_match(monkeypatch):
    async def mock_embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(EmbeddingProvider, "embed", mock_embed)

    cache = SemanticCache()
    prompt = "What is the capital of France?"
    response = "Paris"

    await cache.set(prompt, response)

    hit = await cache.get(prompt)
    assert hit == response


@pytest.mark.asyncio
async def test_semantic_cache_fuzzy_match(monkeypatch):
    async def mock_embed(self, texts):
        res = []
        for t in texts:
            if "python script" in t:
                res.append([1.0, 0.0])
            elif "python code" in t:
                res.append([0.96, 0.28])
            else:
                res.append([0.0, 1.0])
        return res

    monkeypatch.setattr(EmbeddingProvider, "embed", mock_embed)

    cache = SemanticCache(similarity_threshold=0.90)

    await cache.set("write a python script", "print('hello')")
    hit = await cache.get("write a python code")

    assert hit == "print('hello')"


@pytest.mark.asyncio
async def test_semantic_cache_miss(monkeypatch):
    async def mock_embed(self, texts):
        res = []
        for t in texts:
            if "hello" in t:
                res.append([1.0, 0.0])
            else:
                res.append([0.0, 1.0])
        return res

    monkeypatch.setattr(EmbeddingProvider, "embed", mock_embed)

    cache = SemanticCache(similarity_threshold=0.90)
    await cache.set("hello", "hi")
    hit = await cache.get("goodbye")

    assert hit is None


@pytest.mark.asyncio
async def test_semantic_cache_ttl(monkeypatch):
    async def mock_embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(EmbeddingProvider, "embed", mock_embed)

    cache = SemanticCache(ttl_days=1)
    await cache.set("test", "test response")

    # Force expiration by rewriting created_at
    cache._cache[0].created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)

    hit = await cache.get("test")
    assert hit is None


@pytest.mark.asyncio
async def test_adapter_base_hooks(monkeypatch):
    async def mock_embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(EmbeddingProvider, "embed", mock_embed)

    cache = SemanticCache()
    set_semantic_cache(cache)

    # Store response
    await after_provider_call("What is the largest ocean?", "Pacific Ocean")

    # Check cache hit
    hit = await before_provider_call("What is the largest ocean?")
    assert hit == "Pacific Ocean"

    # Clean up
    set_semantic_cache(None)


@pytest.mark.asyncio
async def test_cache_hit_skips_provider_call(monkeypatch):
    """
    Verify that a cached response is returned without incurring downstream LLM API latency or cost.
    """

    async def mock_embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(EmbeddingProvider, "embed", mock_embed)

    cache = SemanticCache()
    set_semantic_cache(cache)

    # Pre-warm cache
    await after_provider_call("What is the largest ocean?", "Pacific Ocean")

    provider_called = False

    # Simulate an adapter flow
    hit = await before_provider_call("What is the largest ocean?")
    if not hit:
        provider_called = True
        # Real provider call would go here

    assert hit == "Pacific Ocean"
    assert provider_called is False

    # Clean up
    set_semantic_cache(None)
