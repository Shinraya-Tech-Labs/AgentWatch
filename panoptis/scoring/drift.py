"""
OBS-007 — Embedding Drift Heatmap.

Tracks semantic shift of outputs over time and clusters them by similarity.
When `sentence-transformers` is not installed, falls back to a deterministic
hashed-token vector so the rest of the system keeps working.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

try:
    import numpy as np  # type: ignore[import-not-found]

    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False
    np = None  # type: ignore[assignment]


@dataclass
class DriftPoint:
    timestamp: datetime
    label: str
    vector: list[float]
    cluster: int = -1


def _hashed_vector(text: str, dim: int = 128) -> list[float]:
    """Deterministic, fast, dependency-free embedding for fallback."""
    vec = [0.0] * dim
    if not text:
        return vec
    tokens = text.lower().split()
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if h[4] & 1 else -1.0
        vec[idx] += sign
    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def embed(text: str, dim: int = 128) -> list[float]:
    """Public embedding API — uses sentence-transformers if available."""
    try:
        model = _get_st_model()
        if model is not None:
            arr = model.encode([text], normalize_embeddings=True)
            return list(arr[0])
    except Exception as exc:  # noqa: BLE001
        # Fall back to deterministic hashed vector when ST is unavailable.
        # Logged at debug to keep the hot path quiet.
        import logging

        logging.getLogger(__name__).debug("ST embed failed, using fallback: %s", exc)
    return _hashed_vector(text, dim=dim)


_ST_UNAVAILABLE = object()
_st_model: Any = None


def _get_st_model() -> Any:
    global _st_model
    if _st_model is _ST_UNAVAILABLE:
        return None
    if _st_model is not None:
        return _st_model
    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        _st_model = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:  # noqa: BLE001
        _st_model = _ST_UNAVAILABLE
        return None
    return _st_model


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@dataclass
class DriftReport:
    points: list[DriftPoint]
    clusters: dict[int, list[int]]  # cluster_id → list of point indices
    drift_score: float  # mean inter-cluster distance


class DriftHeatmap:
    """Online drift tracker — accumulates points and clusters them."""

    def __init__(self, dim: int = 128, threshold: float = 0.6):
        self.dim = dim
        self.threshold = threshold
        self._points: list[DriftPoint] = []

    def add(self, label: str, text: str, timestamp: datetime | None = None) -> DriftPoint:
        vec = embed(text, dim=self.dim) if not _HAS_NUMPY else embed(text, dim=self.dim)
        pt = DriftPoint(
            timestamp=timestamp or datetime.now(UTC),  # noqa: DTZ003 — analytic only
            label=label,
            vector=vec,
        )
        self._points.append(pt)
        return pt

    def cluster_greedy(self) -> dict[int, list[int]]:
        """Greedy single-pass cosine clustering."""
        clusters: dict[int, list[int]] = {}
        centroids: list[list[float]] = []
        for i, pt in enumerate(self._points):
            assigned = False
            for cid, centroid in enumerate(centroids):
                if cosine(pt.vector, centroid) >= self.threshold:
                    clusters[cid].append(i)
                    pt.cluster = cid
                    assigned = True
                    break
            if not assigned:
                cid = len(centroids)
                centroids.append(list(pt.vector))
                clusters[cid] = [i]
                pt.cluster = cid
        return clusters

    def report(self) -> DriftReport:
        clusters = self.cluster_greedy()
        # Drift = mean distance between consecutive clusters
        if len(self._points) < 2:
            return DriftReport(points=list(self._points), clusters=clusters, drift_score=0.0)
        distances = [
            1.0 - cosine(self._points[i].vector, self._points[i - 1].vector)
            for i in range(1, len(self._points))
        ]
        drift_score = sum(distances) / len(distances)
        return DriftReport(
            points=list(self._points),
            clusters=clusters,
            drift_score=drift_score,
        )

    @property
    def points(self) -> list[DriftPoint]:
        return list(self._points)


__all__ = [
    "DriftPoint",
    "DriftReport",
    "DriftHeatmap",
    "embed",
    "cosine",
]
