"""
PRT-002 — AgentWatch-Compatible Badge.

Certification checker — validates that a framework's emitted traces conform
to ReasoningTrace v1.0. Returns pass/fail with details so frameworks can
display the AgentWatch-compatible badge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from panoptis.protocol.schema_v1 import REASONING_TRACE_VERSION, validate_trace


@dataclass
class BadgeResult:
    passed: bool
    framework: str
    sample_size: int
    schema_errors: list[str] = field(default_factory=list)
    span_kind_coverage: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def badge_text(self) -> str:
        return "AgentWatch-compatible ✓" if self.passed else "Not yet certified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "framework": self.framework,
            "sample_size": self.sample_size,
            "badge_text": self.badge_text,
            "schema_errors": self.schema_errors,
            "span_kind_coverage": self.span_kind_coverage,
            "notes": self.notes,
            "version": REASONING_TRACE_VERSION,
        }


def check(framework: str, sample_traces: list[dict[str, Any]]) -> BadgeResult:
    """Validate a batch of sample traces from a framework."""
    if not sample_traces:
        return BadgeResult(
            passed=False,
            framework=framework,
            sample_size=0,
            notes=["no sample traces provided"],
        )

    schema_errors: list[str] = []
    kind_counter: dict[str, int] = {}
    for idx, trace in enumerate(sample_traces):
        ok, errs = validate_trace(trace)
        if not ok:
            for e in errs:
                schema_errors.append(f"trace[{idx}]: {e}")
        for span in trace.get("spans", []) or []:
            kind = span.get("kind", "unknown")
            kind_counter[kind] = kind_counter.get(kind, 0) + 1

    notes: list[str] = []
    required_kinds = {"reasoning", "tool_call"}
    missing_kinds = required_kinds - set(kind_counter.keys())
    if missing_kinds:
        notes.append(f"missing span kinds: {sorted(missing_kinds)}")

    passed = not schema_errors and not missing_kinds
    return BadgeResult(
        passed=passed,
        framework=framework,
        sample_size=len(sample_traces),
        schema_errors=schema_errors[:20],
        span_kind_coverage=kind_counter,
        notes=notes,
    )


__all__ = ["BadgeResult", "check"]
