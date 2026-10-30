"""
RSN-007 — Auditor Calibration Dashboard.

Track false positive / false negative rate over time and surface decay.
One-click recalibrate adjusts the threshold to a target precision/recall.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class CalibrationSample:
    when: datetime
    score: float
    actual_outcome: bool  # True = was actually bad
    flagged: bool  # True = auditor said bad


@dataclass
class CalibrationStats:
    total: int
    tp: int
    fp: int
    tn: int
    fn: int

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def fpr(self) -> float:
        d = self.fp + self.tn
        return self.fp / d if d else 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "total": self.total,
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "precision": self.precision,
            "recall": self.recall,
            "false_positive_rate": self.fpr,
        }


class CalibrationTracker:
    """Sliding-window calibration history with re-tune capability."""

    def __init__(self, window: int = 500, threshold: float = 0.5):
        self.window = window
        self.threshold = threshold
        self._samples: deque[CalibrationSample] = deque(maxlen=window)

    def record(self, score: float, actual_outcome: bool) -> None:
        flagged = score < self.threshold
        self._samples.append(
            CalibrationSample(
                when=datetime.now(UTC),
                score=score,
                actual_outcome=actual_outcome,
                flagged=flagged,
            )
        )

    def stats(self) -> CalibrationStats:
        tp = fp = tn = fn = 0
        for s in self._samples:
            if s.flagged and s.actual_outcome:
                tp += 1
            elif s.flagged and not s.actual_outcome:
                fp += 1
            elif not s.flagged and not s.actual_outcome:
                tn += 1
            else:
                fn += 1
        return CalibrationStats(total=len(self._samples), tp=tp, fp=fp, tn=tn, fn=fn)

    def detect_decay(self, *, max_fpr: float = 0.2) -> bool:
        return self.stats().fpr > max_fpr

    def recalibrate(self, target_recall: float = 0.85) -> float:
        """
        One-click recalibrate — pick the lowest threshold that still recovers
        `target_recall` against historical positives.
        """
        positives = [s for s in self._samples if s.actual_outcome]
        if not positives:
            return self.threshold
        scores = sorted(s.score for s in positives)
        idx = int((1 - target_recall) * len(scores))
        idx = max(0, min(len(scores) - 1, idx))
        self.threshold = scores[idx]
        return self.threshold


__all__ = ["CalibrationTracker", "CalibrationStats", "CalibrationSample"]
