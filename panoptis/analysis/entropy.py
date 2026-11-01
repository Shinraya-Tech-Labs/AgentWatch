"""Token entropy and generation stagnation monitor.

Two signals, both read from already-normalized model-invocation events of one run:

* ``ENTROPY_COLLAPSE``: the mean Shannon entropy of the model's next-token distribution over
  a sliding window of tokens falls below a threshold. Entropy comes from the provider's
  ``top_logprobs`` (kept by the llm_api normalizer as the ``logprobs`` attribute): for each
  generated token the top-k logprobs are exponentiated and renormalized to sum to 1, and
  H = -sum(p * log2 p) in bits. The window slides over the whole run's token stream, so it
  can span turns. A collapse is reported once, when a full window first drops below the
  threshold, and is re-armed when the window mean recovers.
* ``STAGNATION_LOOP``: the model produced the identical completion in ``min_repeats``
  consecutive turns. This compares completion artifact ids (content identity), so it needs
  no logprobs and works as the fallback when a provider does not send them.

Events without logprobs contribute no entropy samples and never raise anomalies of their
own. Truncating to the top-k means the entropy is a lower bound on the true entropy.
"""

from __future__ import annotations

import math
import uuid
from collections import deque
from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Any

from panoptis.analysis.base import AnalysisInput, Analyzer
from panoptis.analysis.maturity import Maturity
from panoptis.events.model import ComputationalEvent, EventKind
from panoptis.graph.build import event_order

ENTROPY_NS = uuid.UUID("0b7d3f52-6a1e-5c94-8d2b-4e9a17c3f6d8")


class AnomalyType(StrEnum):
    ENTROPY_COLLAPSE = "ENTROPY_COLLAPSE"
    STAGNATION_LOOP = "STAGNATION_LOOP"


def shannon_entropy(logprobs: Iterable[float]) -> float:
    """Entropy in bits of the distribution given by ``logprobs``, renormalized to sum to 1.

    Renormalizing matters because a top-k list does not cover the whole vocabulary.
    """
    weights = [math.exp(lp) for lp in logprobs]
    total = sum(weights)
    if total <= 0:
        return 0.0
    return max(0.0, -sum((w / total) * math.log2(w / total) for w in weights if w > 0))


def token_entropies(logprobs: Sequence[dict[str, Any]] | None) -> list[float]:
    """Entropy of each token that carries a ``top_logprobs`` list; others are skipped."""
    out: list[float] = []
    for tok in logprobs or []:
        top = [t["logprob"] for t in tok.get("top_logprobs") or [] if "logprob" in t]
        if top:
            out.append(shannon_entropy(top))
    return out


class TokenEntropyMonitor(Analyzer):
    name = "token_entropy"
    version = "1"
    maturity = Maturity.EXPERIMENTAL
    record_type = "token_entropy_anomaly"

    def __init__(self, window: int = 32, threshold: float = 0.05, min_repeats: int = 3) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        if threshold < 0:
            raise ValueError("threshold must be >= 0")
        if min_repeats < 2:
            raise ValueError("min_repeats must be >= 2")
        self.window = window
        self.threshold = threshold
        self.min_repeats = min_repeats

    def analyze(self, data: AnalysisInput) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for run_id in sorted(data.runs):
            calls = [
                e
                for e in event_order(data.run_events(run_id))
                if e.kind == EventKind.MODEL_INVOCATION
            ]
            out.extend(self._collapses(run_id, calls))
            out.extend(self._stagnation(run_id, calls))
        return out

    def _collapses(self, run_id: str, calls: Sequence[ComputationalEvent]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        buf: deque[float] = deque(maxlen=self.window)
        armed = True
        for turn, event in enumerate(calls):
            for i, h in enumerate(token_entropies(event.attributes.get("logprobs"))):
                buf.append(h)
                if len(buf) < self.window:
                    continue
                mean = sum(buf) / self.window
                if mean >= self.threshold:
                    armed = True
                elif armed:
                    armed = False
                    out.append(
                        self._anomaly(
                            AnomalyType.ENTROPY_COLLAPSE,
                            run_id,
                            [event.event_id],
                            turn,
                            f"mean entropy {mean:.4f} bits over the last {self.window} tokens "
                            f"is below {self.threshold} bits",
                            {
                                "window": self.window,
                                "threshold_bits": self.threshold,
                                "window_mean_bits": round(mean, 6),
                                "token_index": i,
                            },
                        )
                    )
        return out

    def _stagnation(self, run_id: str, calls: Sequence[ComputationalEvent]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        streak: list[tuple[int, ComputationalEvent]] = []
        last_id: str | None = None
        for turn, event in enumerate([*calls, None]):
            ids = [a.artifact_id for a in event.outputs if a.role == "completion"] if event else []
            cid = ids[0] if ids else None
            if event is not None and cid is not None and cid == last_id:
                streak.append((turn, event))
                continue
            if len(streak) >= self.min_repeats:
                out.append(self._stagnation_record(run_id, streak))
            streak = [(turn, event)] if event is not None and cid is not None else []
            last_id = cid
        return out

    def _stagnation_record(
        self, run_id: str, streak: Sequence[tuple[int, ComputationalEvent]]
    ) -> dict[str, Any]:
        return self._anomaly(
            AnomalyType.STAGNATION_LOOP,
            run_id,
            [e.event_id for _, e in streak],
            streak[0][0],
            f"identical completion in {len(streak)} consecutive model calls",
            {"repeats": len(streak), "min_repeats": self.min_repeats},
        )

    def _anomaly(
        self,
        kind: AnomalyType,
        run_id: str,
        events: list[str],
        turn: int,
        explanation: str,
        measures: dict[str, Any],
    ) -> dict[str, Any]:
        key = f"{kind}|{run_id}|{','.join(events)}"
        return self.record(
            str(uuid.uuid5(ENTROPY_NS, key)),
            run_id,
            {
                "anomaly_type": kind.value,
                "run_id": run_id,
                "events": events,
                "turn_index": turn,
                "explanation": explanation,
                "measures": measures,
            },
        )
