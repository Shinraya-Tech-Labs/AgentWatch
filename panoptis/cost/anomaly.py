"""
CST-005 — Cost Anomaly Detector.

Alert when a session costs 3× more than the rolling baseline.
Early warning for runaway agents.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class CostAnomaly:
    session_id: str
    observed_usd: float
    baseline_usd: float
    multiplier: float
    severity: str  # warn | high | critical
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class CostAnomalyDetector:
    """
    Maintain a rolling baseline of session cost and flag outliers.

    Severity bands:
      warn      ≥ 2×   baseline
      high      ≥ 3×   baseline
      critical  ≥ 5×   baseline
    """

    def __init__(self, window: int = 100, min_samples: int = 5):
        self.window = window
        self.min_samples = min_samples
        self._samples: deque[float] = deque(maxlen=window)

    @property
    def baseline(self) -> float:
        if not self._samples:
            return 0.0
        return statistics.median(self._samples)

    def record(self, session_id: str, total_usd: float) -> CostAnomaly | None:
        anomaly: CostAnomaly | None = None
        if len(self._samples) >= self.min_samples:
            base = self.baseline
            if base > 0:
                mult = total_usd / base
                severity = (
                    "critical"
                    if mult >= 5.0
                    else "high"
                    if mult >= 3.0
                    else "warn"
                    if mult >= 2.0
                    else None
                )
                if severity is not None:
                    anomaly = CostAnomaly(
                        session_id=session_id,
                        observed_usd=total_usd,
                        baseline_usd=base,
                        multiplier=mult,
                        severity=severity,
                    )
        self._samples.append(total_usd)
        return anomaly


__all__ = ["CostAnomaly", "CostAnomalyDetector"]
