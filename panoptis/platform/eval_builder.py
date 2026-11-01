"""
PLT-006 — Evaluation Dataset Builder.

Convert production traces to eval datasets. One-click: flag session as
golden example or failure case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class EvalLabel(str, Enum):
    GOLDEN = "golden"
    FAILURE = "failure"
    AMBIGUOUS = "ambiguous"


@dataclass
class EvalExample:
    session_id: str
    goal: str
    expected_outcome: str
    label: EvalLabel
    events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    added_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "expected_outcome": self.expected_outcome,
            "label": self.label.value,
            "events": self.events,
            "metadata": self.metadata,
            "added_at": self.added_at.isoformat(),
        }


class EvalDataset:
    """Build, label, and export an eval dataset from production traces."""

    def __init__(self, name: str):
        self.name = name
        self._examples: list[EvalExample] = []

    def add(
        self,
        session_id: str,
        goal: str,
        expected_outcome: str,
        label: EvalLabel,
        events: list[dict[str, Any]] | None = None,
        **meta: Any,
    ) -> EvalExample:
        example = EvalExample(
            session_id=session_id,
            goal=goal,
            expected_outcome=expected_outcome,
            label=label,
            events=events or [],
            metadata=meta,
        )
        self._examples.append(example)
        return example

    def filter(self, label: EvalLabel) -> list[EvalExample]:
        return [e for e in self._examples if e.label == label]

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e.to_dict(), default=str) for e in self._examples)

    def __len__(self) -> int:
        return len(self._examples)

    def stats(self) -> dict[str, int]:
        return {
            "total": len(self._examples),
            "golden": len(self.filter(EvalLabel.GOLDEN)),
            "failure": len(self.filter(EvalLabel.FAILURE)),
            "ambiguous": len(self.filter(EvalLabel.AMBIGUOUS)),
        }


__all__ = ["EvalLabel", "EvalExample", "EvalDataset"]
