"""Issue 4 — Auditor Transparency: StepAudit transparency fields (#576)."""

from __future__ import annotations

import asyncio

from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    EventType,
    ExecutionStatus,
    ToolCallData,
)
from panoptis.reasoning.auditor import ReasoningAuditor, StepAudit


def _tool_call(tool: str, args: dict, raw: str | None = None) -> AgentEvent:
    return AgentEvent(
        session_id="S",
        agent_id="A",
        framework=AgentFramework.CUSTOM,
        event_type=EventType.TOOL_CALL,
        tool_call=ToolCallData(tool_name=tool, arguments=args, raw_command=raw),
    )


def test_step_audit_has_transparency_fields_with_defaults() -> None:
    audit = StepAudit(step_index=0, event_id="e", score=0.8, verdict="sound", rationale="ok")
    assert audit.model_used == "heuristic"
    assert audit.risk_signals == []
    assert audit.confidence_breakdown == {}
    assert audit.latency_ms == 0.0


def test_heuristic_audit_populates_transparency_fields() -> None:
    auditor = ReasoningAuditor()
    event = _tool_call("shell", {"command": "ls -la"}, raw="ls -la")
    audit = asyncio.run(auditor.audit_step(0, event))

    assert audit.model_used == "heuristic"
    # The breakdown exposes the three interpretable dimensions, each in [0, 1].
    assert set(audit.confidence_breakdown) == {"relevance", "safety", "coherence"}
    assert all(0.0 <= value <= 1.0 for value in audit.confidence_breakdown.values())
    # Latency is measured (non-negative).
    assert audit.latency_ms >= 0.0


def test_risk_signals_flag_destructive_command_and_wildcard() -> None:
    auditor = ReasoningAuditor()
    event = _tool_call("shell", {"command": "rm -rf /var/*"}, raw="rm -rf /var/*")
    audit = asyncio.run(auditor.audit_step(0, event))

    assert "destructive_command" in audit.risk_signals
    assert "broad_wildcard" in audit.risk_signals


def test_risk_signals_flag_blocked_action() -> None:
    auditor = ReasoningAuditor()
    event = AgentEvent(
        session_id="S",
        agent_id="A",
        framework=AgentFramework.CUSTOM,
        event_type=EventType.TOOL_CALL,
        tool_call=ToolCallData(
            tool_name="shell", arguments={"command": "echo hi"}, raw_command="echo hi"
        ),
        status=ExecutionStatus.BLOCKED,
    )
    audit = asyncio.run(auditor.audit_step(0, event))
    assert "blocked_action" in audit.risk_signals


def test_to_dict_exposes_transparency_fields() -> None:
    auditor = ReasoningAuditor()
    event = _tool_call("shell", {"command": "sudo rm -rf /tmp/*"}, raw="sudo rm -rf /tmp/*")
    summary = asyncio.run(auditor.audit_session([event]))

    step = summary.to_dict()["audits"][0]  # type: ignore[index]
    assert "model_used" in step
    assert "risk_signals" in step
    assert "confidence_breakdown" in step
    assert "latency_ms" in step
    assert "destructive_command" in step["risk_signals"]
    assert "privilege_escalation" in step["risk_signals"]
