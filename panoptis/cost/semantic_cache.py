"""
Semantic Caching Engine for repeated LLM queries.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import logging
from typing import Any

from panoptis.memory.engine import EmbeddingProvider, _cosine_similarity
from panoptis.models.cache import CacheEntry

logger = logging.getLogger(__name__)


class SemanticCache:
    """Semantic cache utilizing cosine similarity bounds."""

    def __init__(
        self,
        similarity_threshold: float = 0.95,
        max_entries: int = 1000,
        embedding_provider: EmbeddingProvider | None = None,
        ttl_days: int | None = None,
    ):
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self.embedder = embedding_provider or EmbeddingProvider()
        self.ttl_days = ttl_days
        self._cache: list[CacheEntry] = []
        self._lock = asyncio.Lock()

    def _hash_query(self, query: str) -> str:
        return hashlib.sha256(query.encode("utf-8")).hexdigest()

    async def get(self, query: str, ttl_days_override: int | None = None) -> str | None:
        """Get a cached response if semantic similarity is above threshold and not expired."""
        effective_ttl = ttl_days_override if ttl_days_override is not None else self.ttl_days

        query_embeddings = await self.embedder.embed([query])
        query_vec = query_embeddings[0] if query_embeddings else None

        if not query_vec:
            return None

        # In-memory fallback
        if not self._cache:
            return None

        now = datetime.datetime.now(datetime.UTC)

        async with self._lock:
            best_match: CacheEntry | None = None
            highest_sim = -1.0

            # Filter out expired entries if ttl_days is set
            valid_entries = []
            for entry in self._cache:
                if effective_ttl is not None:
                    if (now - entry.created_at).total_seconds() > effective_ttl * 86400:
                        continue
                valid_entries.append(entry)

            # Optional: update cache to prune expired entries
            if len(valid_entries) != len(self._cache):
                self._cache = valid_entries

            for entry in valid_entries:
                sim = _cosine_similarity(query_vec, entry.embedding)
                if sim >= self.similarity_threshold and sim > highest_sim:
                    highest_sim = sim
                    best_match = entry

            if best_match:
                logger.info("Semantic cache hit with similarity %.4f", highest_sim)
                return best_match.response

            return None

    async def set(self, query: str, response: str, metadata: dict[str, Any] | None = None) -> None:
        """Store a query-response pair in the semantic cache."""
        query_embeddings = await self.embedder.embed([query])
        query_vec = query_embeddings[0] if query_embeddings else None

        if not query_vec:
            return

        async with self._lock:
            self._cache.append(
                CacheEntry(
                    query=query,
                    response=response,
                    embedding=query_vec,
                    metadata=metadata,
                )
            )
            if len(self._cache) > self.max_entries:
                self._cache.pop(0)
            logger.debug("Added entry to semantic cache. Size: %d", len(self._cache))
