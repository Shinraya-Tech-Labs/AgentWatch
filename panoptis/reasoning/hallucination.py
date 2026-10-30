"""
RSN-002 — Hallucination Risk Classifier.

Per-step risk: low / medium / high.
Heuristically grounds the agent's stated facts against:
  - tool inputs that referenced things never seen in prior tool outputs
  - cited file paths that don't appear in the trace
  - identifiers/references with no upstream binding
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from panoptis.core.schema import AgentEvent, EventType


class HallucinationRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class HallucinationFlag:
    risk: HallucinationRisk
    score: float
    triggers: list[str] = field(default_factory=list)
    pre_execution: bool = False


# Reasonable identifier-like pattern (paths, varnames, keys)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_./\-]{2,}")


def _identifiers(text: str | None) -> set[str]:
    if not text:
        return set()
    return set(_IDENT.findall(text))


class HallucinationClassifier:
    """
    Tracks "grounded" identifiers across a session and flags new ones that
    appear in tool arguments without ever being seen in prior outputs.
    """

    def __init__(self) -> None:
        self._grounded: set[str] = set()
        self._session_id: str | None = None

    def reset(self, session_id: str | None = None) -> None:
        self._grounded.clear()
        self._session_id = session_id

    def observe(self, event: AgentEvent) -> None:
        """Update grounded identifiers from tool outputs and planner data."""
        if event.tool_result and event.tool_result.output is not None:
            self._grounded.update(_identifiers(str(event.tool_result.output)))
        if event.planner_output_preview:
            self._grounded.update(_identifiers(event.planner_output_preview))
        if event.prompt_preview:
            self._grounded.update(_identifiers(event.prompt_preview))

    def classify(self, event: AgentEvent) -> HallucinationFlag:
        triggers: list[str] = []
        score = 0.0

        # Pre-execution flag on tool-call arguments
        if event.event_type == EventType.TOOL_CALL and event.tool_call:
            arg_blob = repr(event.tool_call.arguments) + (event.tool_call.raw_command or "")
            arg_idents = _identifiers(arg_blob)
            novel = [i for i in arg_idents if i not in self._grounded and len(i) >= 4]

            if novel:
                # Filter obvious built-ins / common path prefixes
                novel = [
                    n
                    for n in novel
                    if n.lower()
                    not in {
                        "true",
                        "false",
                        "none",
                        "null",
                        "command",
                        "input",
                        "args",
                    }
                ]

            if len(novel) >= 3:
                triggers.append(f"novel_identifiers={len(novel)}")
                score += 0.6

            # Invented credential-like patterns
            if re.search(r"(fake|placeholder|invented|TODO)", arg_blob, re.I):
                triggers.append("placeholder_text")
                score += 0.4

            risk = (
                HallucinationRisk.HIGH
                if score >= 0.6
                else HallucinationRisk.MEDIUM
                if score >= 0.3
                else HallucinationRisk.LOW
            )
            return HallucinationFlag(
                risk=risk,
                score=min(1.0, score),
                triggers=triggers,
                pre_execution=True,
            )

        return HallucinationFlag(
            risk=HallucinationRisk.LOW, score=0.0, triggers=[], pre_execution=False
        )


__all__ = ["HallucinationRisk", "HallucinationFlag", "HallucinationClassifier"]
