"""
AgentWatch Memory Engine
Layered persistent memory: episodic, semantic, and procedural.
Cross-session continuity with semantic retrieval and contradiction handling.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentwatch.memory.decay import ForgettingEngine

logger = logging.getLogger(__name__)


class MemoryType(str, Enum):
    EPISODIC = "episodic"  # What happened (events, observations, interactions)
    SEMANTIC = "semantic"  # What is known (facts, relationships, entities)
    PROCEDURAL = "procedural"  # How to do things (learned workflows, patterns)


class ImportanceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class MemoryEntry:
    entry_id: str
    agent_id: str
    memory_type: MemoryType
    content: str
    summary: str | None = None
    importance: ImportanceLevel = ImportanceLevel.MEDIUM
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_accessed: datetime = field(default_factory=lambda: datetime.now(UTC))
    access_count: int = 0
    session_id: str | None = None
    task_id: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    contradiction_ids: list[str] = field(default_factory=list)
    superseded_by: str | None = None
    decay_factor: float = 1.0

    @property
    def is_active(self) -> bool:
        return self.superseded_by is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "agent_id": self.agent_id,
            "memory_type": self.memory_type.value,
            "content": self.content,
            "summary": self.summary,
            "importance": self.importance.value,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "access_count": self.access_count,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "tags": self.tags,
            "metadata": self.metadata,
            "contradiction_ids": self.contradiction_ids,
            "superseded_by": self.superseded_by,
            "decay_factor": self.decay_factor,
        }


@dataclass
class MemorySearchResult:
    entry: MemoryEntry
    similarity_score: float
    relevance_explanation: str | None = None


@dataclass
class ContradictionReport:
    entry_a_id: str
    entry_b_id: str
    reason: str
    confidence: float
    suggested_resolution: str


# ─────────────────────────────────────────────
# Embedding interface
# ─────────────────────────────────────────────


class EmbeddingProvider:
    """
    Pluggable embedding provider interface.
    Default implementation uses sentence-transformers locally.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model = None
        self._lock = asyncio.Lock()
        self._disabled = False

    async def _load(self) -> None:
        async with self._lock:
            if self._model is None and not self._disabled:
                try:
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(self._model_name)
                    logger.info("Loaded embedding model: %s", self._model_name)
                except ImportError:
                    logger.warning(
                        "sentence-transformers not installed. "
                        "Memory retrieval will use keyword fallback."
                    )
                    self._disabled = True
                except Exception as exc:
                    logger.warning(
                        "Embedding model unavailable (%s). Memory retrieval will use keyword fallback.",
                        exc,
                    )
                    self._disabled = True

    async def embed(self, texts: list[str]) -> list[list[float] | None]:
        await self._load()
        if self._model is None:
            return [None] * len(texts)

        def _encode() -> list[list[float]]:
            return self._model.encode(texts, normalize_embeddings=True).tolist()

        embeddings = await asyncio.get_event_loop().run_in_executor(None, _encode)
        return embeddings


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x**2 for x in a) ** 0.5
    norm_b = sum(x**2 for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _keyword_similarity(query: str, content: str) -> float:
    """Fallback keyword overlap similarity."""
    q_tokens = set(query.lower().split())
    c_tokens = set(content.lower().split())
    if not q_tokens:
        return 0.0
    return len(q_tokens & c_tokens) / len(q_tokens)


# ─────────────────────────────────────────────
# In-memory store (swap for PostgreSQL/pgvector)
# ─────────────────────────────────────────────


class MemoryStore:
    """
    In-process memory store.
    Production: replace with PostgreSQL + pgvector backend.
    """

    def __init__(self) -> None:
        self._entries: dict[str, MemoryEntry] = {}
        self._agent_index: dict[str, list[str]] = {}  # agent_id -> [entry_ids]

    def add(self, entry: MemoryEntry) -> None:
        self._entries[entry.entry_id] = entry
        if entry.agent_id not in self._agent_index:
            self._agent_index[entry.agent_id] = []
        self._agent_index[entry.agent_id].append(entry.entry_id)

    def get(self, entry_id: str) -> MemoryEntry | None:
        return self._entries.get(entry_id)

    def get_for_agent(
        self,
        agent_id: str,
        memory_type: MemoryType | None = None,
        active_only: bool = True,
    ) -> list[MemoryEntry]:
        ids = self._agent_index.get(agent_id, [])
        entries = [self._entries[i] for i in ids if i in self._entries]
        if active_only:
            entries = [e for e in entries if e.is_active]
        if memory_type:
            entries = [e for e in entries if e.memory_type == memory_type]
        return entries

    def update(self, entry: MemoryEntry) -> None:
        self._entries[entry.entry_id] = entry

    def remove(self, entry_id: str) -> bool:
        """Delete an entry and drop it from the agent index. Returns success."""
        entry = self._entries.pop(entry_id, None)
        if entry is None:
            return False
        ids = self._agent_index.get(entry.agent_id)
        if ids and entry_id in ids:
            ids.remove(entry_id)
        return True

    def all_entries(self) -> list[MemoryEntry]:
        return list(self._entries.values())

    def count(self) -> int:
        return len(self._entries)


# ─────────────────────────────────────────────
# Memory Engine
# ─────────────────────────────────────────────


class MemoryEngine:
    """
    Layered memory engine with episodic, semantic, and procedural stores.

    Key capabilities:
    - Semantic retrieval via embeddings
    - Cross-session persistence
    - Contradiction detection
    - Temporal decay for episodic memories
    - Importance-weighted retrieval
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        max_entries_per_agent: int = 10_000,
        decay_manager: ForgettingEngine | None = None,
    ):
        # Imported lazily: ForgettingEngine imports from this module.
        from agentwatch.memory.decay import ForgettingEngine

        self._store = MemoryStore()
        self._embedder = embedding_provider or EmbeddingProvider()
        self._max_entries = max_entries_per_agent
        self._contradiction_reports: list[ContradictionReport] = []
        self._decay = decay_manager or ForgettingEngine()

    async def store(
        self,
        agent_id: str,
        content: str,
        memory_type: MemoryType = MemoryType.EPISODIC,
        importance: ImportanceLevel = ImportanceLevel.MEDIUM,
        session_id: str | None = None,
        task_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        check_contradictions: bool = True,
    ) -> MemoryEntry:
        """Store a new memory entry with optional embedding and contradiction check."""
        entry = MemoryEntry(
            entry_id=str(uuid.uuid4()),
            agent_id=agent_id,
            memory_type=memory_type,
            content=content,
            importance=importance,
            session_id=session_id,
            task_id=task_id,
            tags=tags or [],
            metadata=metadata or {},
        )

        embeddings = await self._embedder.embed([content])
        if embeddings and embeddings[0]:
            entry.embedding = embeddings[0]

        if check_contradictions and memory_type == MemoryType.SEMANTIC:
            contradictions = await self._check_contradictions(entry, agent_id)
            if contradictions:
                entry.contradiction_ids = [c.entry_a_id for c in contradictions]
                self._contradiction_reports.extend(contradictions)
                logger.warning(
                    "Memory contradiction detected for agent %s: %d conflict(s)",
                    agent_id,
                    len(contradictions),
                )

        self._store.add(entry)
        return entry

    async def retrieve(
        self,
        agent_id: str,
        query: str,
        memory_types: list[MemoryType] | None = None,
        top_k: int = 10,
        min_similarity: float = 0.3,
        recency_boost: bool = True,
    ) -> list[MemorySearchResult]:
        """Retrieve relevant memories using semantic search."""
        candidates: list[MemoryEntry] = []
        if memory_types:
            for mt in memory_types:
                candidates.extend(self._store.get_for_agent(agent_id, memory_type=mt))
        else:
            candidates = self._store.get_for_agent(agent_id)

        if not candidates:
            return []

        query_embeddings = await self._embedder.embed([query])
        query_vec = query_embeddings[0] if query_embeddings else None

        results: list[MemorySearchResult] = []
        now = datetime.now(UTC)

        for entry in candidates:
            if query_vec and entry.embedding:
                similarity = _cosine_similarity(query_vec, entry.embedding)
            else:
                similarity = _keyword_similarity(query, entry.content)

            if similarity < min_similarity:
                continue

            score = similarity
            if recency_boost and entry.memory_type == MemoryType.EPISODIC:
                # Deprioritize stale episodic memories using the importance-
                # weighted forgetting curve. Read strength before the rehearsal
                # bump below so older memories score lower; decay_factor is
                # refreshed afterwards to reflect the post-access state.
                strength = self._decay.entry_strength(entry, now)
                score = score * 0.7 + strength * 0.3

            importance_boost = {
                ImportanceLevel.LOW: 0.9,
                ImportanceLevel.MEDIUM: 1.0,
                ImportanceLevel.HIGH: 1.1,
                ImportanceLevel.CRITICAL: 1.2,
            }[entry.importance]
            score *= importance_boost

            results.append(
                MemorySearchResult(
                    entry=entry,
                    similarity_score=min(1.0, score),
                )
            )

            entry.last_accessed = now
            entry.access_count += 1
            self._decay.refresh_entry(entry, now)
            self._store.update(entry)

        results.sort(key=lambda r: r.similarity_score, reverse=True)
        return results[:top_k]

    async def get_context_window(
        self,
        agent_id: str,
        query: str,
        max_tokens: int = 2000,
        session_id: str | None = None,
    ) -> str:
        """
        Build a formatted memory context string for injection into prompts.
        Returns the most relevant memories formatted as context.
        """
        results = await self.retrieve(agent_id, query, top_k=20)

        context_parts: list[str] = []
        estimated_tokens = 0.0
        tokens_per_char = 0.25

        ordered = sorted(
            results,
            key=lambda r: (
                {"semantic": 0, "procedural": 1, "episodic": 2}[r.entry.memory_type.value],
                -r.similarity_score,
            ),
        )

        for result in ordered:
            entry = result.entry
            chunk = f"[{entry.memory_type.value.upper()}] {entry.content}"
            chunk_tokens = len(chunk) * tokens_per_char

            if estimated_tokens + chunk_tokens > max_tokens:
                break

            context_parts.append(chunk)
            estimated_tokens += chunk_tokens

        if not context_parts:
            return ""

        return "\n\n---\nRelevant memory context:\n" + "\n".join(context_parts)

    async def _check_contradictions(
        self,
        new_entry: MemoryEntry,
        agent_id: str,
    ) -> list[ContradictionReport]:
        reports: list[ContradictionReport] = []
        existing = self._store.get_for_agent(agent_id, memory_type=MemoryType.SEMANTIC)

        if not existing:
            return []

        if new_entry.embedding is None:
            embeddings = await self._embedder.embed([new_entry.content])
            if embeddings and embeddings[0]:
                new_entry.embedding = embeddings[0]

        negation_pairs = [
            ("is", "is not"),
            ("can", "cannot"),
            ("will", "will not"),
            ("does", "does not"),
            ("has", "has not"),
            ("true", "false"),
        ]

        for existing_entry in existing[:100]:
            if not existing_entry.is_active:
                continue

            if new_entry.embedding and existing_entry.embedding:
                sim = _cosine_similarity(new_entry.embedding, existing_entry.embedding)
                if sim > 0.85:
                    n_lower = new_entry.content.lower()
                    e_lower = existing_entry.content.lower()
                    for pos, neg in negation_pairs:
                        if pos in n_lower and neg in e_lower:
                            reports.append(
                                ContradictionReport(
                                    entry_a_id=existing_entry.entry_id,
                                    entry_b_id=new_entry.entry_id,
                                    reason=f"Negation conflict: '{pos}' vs '{neg}'",
                                    confidence=sim,
                                    suggested_resolution=(
                                        "Keep the more recent entry. Review both manually."
                                    ),
                                )
                            )
                            break
                        if neg in n_lower and pos in e_lower:
                            reports.append(
                                ContradictionReport(
                                    entry_a_id=existing_entry.entry_id,
                                    entry_b_id=new_entry.entry_id,
                                    reason=f"Negation conflict: '{neg}' vs '{pos}'",
                                    confidence=sim,
                                    suggested_resolution=(
                                        "Keep the more recent entry. Review both manually."
                                    ),
                                )
                            )
                            break

        return reports

    def get_contradictions(self, agent_id: str | None = None) -> list[ContradictionReport]:
        return list(self._contradiction_reports)

    def prune_decayed(
        self,
        agent_id: str | None = None,
        *,
        now: datetime | None = None,
    ) -> list[str]:
        """Evict memories that have decayed below the strength threshold.

        Background cleanup pass for the forgetting curve. Eligibility is by
        decayed strength, not importance level directly — but importance sets
        the half-life, so higher-importance entries decay slower and survive
        longer. CRITICAL memories (and, by default, non-episodic knowledge) are
        never evicted. Pass ``agent_id`` to scope the sweep to a single agent,
        or omit it to sweep all agents. Returns the entry IDs that were removed.
        """
        now = now or datetime.now(UTC)
        if agent_id is not None:
            entries = self._store.get_for_agent(agent_id, active_only=False)
        else:
            entries = self._store.all_entries()

        removed: list[str] = []
        for entry in self._decay.select_prunable(entries, now):
            if self._store.remove(entry.entry_id):
                removed.append(entry.entry_id)
        if removed:
            logger.info("Pruned %d decayed memory entries", len(removed))
        return removed

    def stats(self, agent_id: str | None = None) -> dict[str, Any]:
        if agent_id:
            entries = self._store.get_for_agent(agent_id)
            by_type = {}
            for mt in MemoryType:
                by_type[mt.value] = sum(1 for e in entries if e.memory_type == mt)
            return {
                "agent_id": agent_id,
                "total_entries": len(entries),
                "by_type": by_type,
                "contradictions": len(self._contradiction_reports),
            }
        return {
            "total_entries": self._store.count(),
            "total_contradictions": len(self._contradiction_reports),
        }

    def create_snapshot(
        self,
        turn_id: int,
        scratchpad: str = "",
        agent_id: str | None = None,
        extra_state: dict[str, Any] | None = None,
    ) -> MemorySnapshot:
        """Capture an immutable, cryptographically hashed memory snapshot."""
        state: dict[str, Any] = {}
        if agent_id:
            entries = self._store.get_for_agent(agent_id)
            state["entries"] = [e.content for e in entries]
        else:
            state["entry_count"] = self._store.count()
        if extra_state:
            state.update(extra_state)

        timestamp_iso = datetime.now(UTC).isoformat()
        return MemorySnapshot(
            turn_id=turn_id,
            timestamp=timestamp_iso,
            state=state,
            scratchpad=scratchpad,
        )

    def record_mutation(self, before: MemorySnapshot, after: MemorySnapshot) -> MemoryDelta:
        """Compute delta between turns and return structured mutation record."""
        return compute_memory_delta(before, after)


@dataclass(frozen=True)
class MemorySnapshot:
    """Immutable working-memory state snapshot with cryptographic digest."""

    turn_id: int
    timestamp: str
    state: dict[str, Any]
    scratchpad: str = ""
    sha256_hash: str = field(init=False)

    def __post_init__(self) -> None:
        payload = {
            "turn_id": self.turn_id,
            "timestamp": self.timestamp,
            "state": self.state,
            "scratchpad": self.scratchpad,
        }
        canonical_bytes = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        object.__setattr__(self, "sha256_hash", hashlib.sha256(canonical_bytes).hexdigest())


@dataclass(frozen=True)
class MemoryDelta:
    """Structured mutation diff between two working-memory snapshots."""

    turn_id_before: int
    turn_id_after: int
    before_hash: str
    after_hash: str
    added_keys: list[str]
    removed_keys: list[str]
    modified_values: dict[str, tuple[Any, Any]]
    scratchpad_diff: list[str]
    has_mutations: bool


def compute_memory_delta(before: MemorySnapshot, after: MemorySnapshot) -> MemoryDelta:
    """Compute structured state diff and line-level scratchpad unified diff."""
    if after.turn_id < before.turn_id:
        raise ValueError(
            f"Cannot diff backwards in time: before turn {before.turn_id} > after turn {after.turn_id}"
        )

    before_keys = set(before.state.keys())
    after_keys = set(after.state.keys())

    added_keys = sorted(after_keys - before_keys)
    removed_keys = sorted(before_keys - after_keys)

    modified_values: dict[str, tuple[Any, Any]] = {}
    for k in sorted(before_keys & after_keys):
        if before.state[k] != after.state[k]:
            modified_values[k] = (before.state[k], after.state[k])

    before_lines = before.scratchpad.splitlines(keepends=True)
    after_lines = after.scratchpad.splitlines(keepends=True)
    scratchpad_diff = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"turn_{before.turn_id}",
            tofile=f"turn_{after.turn_id}",
        )
    )

    has_mutations = bool(added_keys or removed_keys or modified_values or scratchpad_diff)

    return MemoryDelta(
        turn_id_before=before.turn_id,
        turn_id_after=after.turn_id,
        before_hash=before.sha256_hash,
        after_hash=after.sha256_hash,
        added_keys=added_keys,
        removed_keys=removed_keys,
        modified_values=modified_values,
        scratchpad_diff=scratchpad_diff,
        has_mutations=has_mutations,
    )
