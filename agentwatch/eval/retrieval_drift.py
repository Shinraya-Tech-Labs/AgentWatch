"""Vector RAG embedding similarity drift & precision collapse monitor."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

ALERT_RETRIEVAL_PRECISION_DRIFT = "RETRIEVAL_PRECISION_DRIFT"


@dataclass(frozen=True)
class DriftReport:
    """Statistical summary emitted on ingestion of retriever similarity scores."""

    query_count: int
    query_mean: float
    ema: float
    baseline_mean: float | None
    baseline_std: float | None
    z_score: float | None
    drift_detected: bool
    status: str
    alert_code: str | None = None


class WelfordAccumulator:
    """Streaming online mean and variance calculator using Welford's algorithm."""

    def __init__(self) -> None:
        self.count: int = 0
        self.mean: float = 0.0
        self._m2: float = 0.0

    def update(self, val: float) -> None:
        self.count += 1
        delta = val - self.mean
        self.mean += delta / self.count
        delta2 = val - self.mean
        self._m2 += delta * delta2

    @property
    def variance(self) -> float:
        return self._m2 / (self.count - 1) if self.count > 1 else 0.0

    @property
    def stdev(self) -> float:
        return math.sqrt(self.variance)


class RetrievalDriftDetector:
    """Monitors vector RAG top-K similarity scores to detect silent precision collapse."""

    def __init__(
        self,
        baseline_window: int = 50,
        sigma_threshold: float = 2.5,
        alpha: float = 0.1,
        is_distance: bool = False,
    ) -> None:
        self.baseline_window = baseline_window
        self.sigma_threshold = sigma_threshold
        self.alpha = alpha
        self.is_distance = is_distance

        self._welford = WelfordAccumulator()
        self._query_count: int = 0
        self._ema: float | None = None
        self._baseline_mean: float | None = None
        self._baseline_std: float | None = None
        self._is_drifted: bool = False

    @property
    def baseline_established(self) -> bool:
        return self._query_count >= self.baseline_window

    @property
    def is_drifted(self) -> bool:
        return self._is_drifted

    def _extract_scores(self, payload: Any) -> list[float]:
        if isinstance(payload, (int, float)):
            return [float(payload)]
        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
            return [float(x) for x in payload if isinstance(x, (int, float))]
        if isinstance(payload, dict):
            for k in ("similarity_scores", "scores", "similarities", "distances", "top_k_scores"):
                if k in payload and isinstance(payload[k], Sequence):
                    return [float(x) for x in payload[k] if isinstance(x, (int, float))]
            if "attributes" in payload and isinstance(payload["attributes"], dict):
                return self._extract_scores(payload["attributes"])
        if hasattr(payload, "attributes") and isinstance(payload.attributes, dict):
            return self._extract_scores(payload.attributes)
        return []

    def ingest(self, event_or_scores: Any) -> DriftReport:
        scores = self._extract_scores(event_or_scores)
        if not scores:
            raise ValueError(f"No valid numeric similarity scores found in: {event_or_scores}")

        query_mean = sum(scores) / len(scores)
        self._query_count += 1

        if self._ema is None:
            self._ema = query_mean
        else:
            self._ema = self.alpha * query_mean + (1.0 - self.alpha) * self._ema

        if self._query_count <= self.baseline_window:
            self._welford.update(query_mean)
            if self._query_count == self.baseline_window:
                self._baseline_mean = self._welford.mean
                self._baseline_std = self._welford.stdev

            return DriftReport(
                query_count=self._query_count,
                query_mean=query_mean,
                ema=self._ema,
                baseline_mean=self._baseline_mean,
                baseline_std=self._baseline_std,
                z_score=None,
                drift_detected=False,
                status="BASELINE_ESTABLISHING",
                alert_code=None,
            )

        baseline_mean = self._baseline_mean or 0.0
        baseline_std = self._baseline_std or 0.0

        if baseline_std > 1e-9:
            if self.is_distance:
                # For distance, drift occurs when distance rises abnormally
                z_score = (query_mean - baseline_mean) / baseline_std
            else:
                # For similarity, drift occurs when similarity drops abnormally
                z_score = (baseline_mean - query_mean) / baseline_std
        else:
            is_worse = (
                query_mean > baseline_mean if self.is_distance else query_mean < baseline_mean
            )
            z_score = float("inf") if is_worse else 0.0

        drift_detected = z_score > self.sigma_threshold
        if drift_detected:
            self._is_drifted = True
            status = ALERT_RETRIEVAL_PRECISION_DRIFT
            alert_code = ALERT_RETRIEVAL_PRECISION_DRIFT
        else:
            status = "STABLE"
            alert_code = None

        return DriftReport(
            query_count=self._query_count,
            query_mean=query_mean,
            ema=self._ema,
            baseline_mean=baseline_mean,
            baseline_std=baseline_std,
            z_score=z_score,
            drift_detected=drift_detected,
            status=status,
            alert_code=alert_code,
        )

    def reset(self) -> None:
        self._welford = WelfordAccumulator()
        self._query_count = 0
        self._ema = None
        self._baseline_mean = None
        self._baseline_std = None
        self._is_drifted = False
