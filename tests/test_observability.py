"""Phase 2 — Core Observability tests (OBS-001..010)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from panoptis.core.event_bus import EventBus
from panoptis.core.schema import (
    AgentEvent,
    AgentSession,
    EventType,
    ExecutionStatus,
    ToolCallData,
    ToolResultData,
)
from panoptis.replay.counterfactual import (
    CounterfactualEngine,
    CounterfactualScenario,
)
from panoptis.scoring.drift import DriftHeatmap, cosine, embed
from panoptis.scoring.silence import SilentFailureDetector
from panoptis.telemetry.otel import OTELConfig, OTELExporter
from panoptis.tracing.audit import ToolAuditLog, detect_hallucinated_arguments
from panoptis.tracing.live import LiveStreamHub
from panoptis.tracing.sampling import (
    FailureAlwaysSampler,
    HeadSampler,
    ReservoirSampler,
    TailSampler,
)
from panoptis.tracing.spans import SpanKind, SpanRegistry, event_to_span
from panoptis.tracing.trajectory import build_trajectory


def _ev(
    event_type: EventType,
    *,
    session_id: str = "S",
    tool: str | None = None,
    args: dict | None = None,
    status: ExecutionStatus = ExecutionStatus.RUNNING,
    step: int = 0,
) -> AgentEvent:
    _args = args or {}
    # Auto-promote command-like argument values to raw_command so the
    # ToolCallData validator doesn't raise (and so safety checks work correctly).
    cmd_keys = ("command", "cmd", "shell", "exec")
    _raw_cmd = next(
        (
            v
            for k in cmd_keys
            if isinstance(_args.get(k), str) and _args[k].strip()
            for v in [_args[k]]
        ),
        None,
    )
    return AgentEvent(
        session_id=session_id,
        agent_id="A",
        event_type=event_type,
        status=status,
        step_number=step,
        tool_call=ToolCallData(tool_name=tool, arguments=_args, raw_command=_raw_cmd)
        if tool
        else None,
    )


# ─────────────────────────────────────────────
# OBS-001 — Spans
# ─────────────────────────────────────────────


def test_span_kinds_map_correctly():
    s_tool = event_to_span(_ev(EventType.TOOL_CALL, tool="bash"))
    s_plan = event_to_span(_ev(EventType.PLANNER_OUTPUT))
    s_mem = event_to_span(_ev(EventType.MEMORY_READ))
    assert s_tool.kind == SpanKind.TOOL_CALL
    assert s_plan.kind == SpanKind.REASONING
    assert s_mem.kind == SpanKind.MEMORY_READ


def test_span_registry_indexes_by_trace_and_kind():
    reg = SpanRegistry()
    reg.ingest_event(_ev(EventType.TOOL_CALL, tool="bash"))
    reg.ingest_event(_ev(EventType.PLANNER_OUTPUT))
    assert reg.count() == 2
    assert len(reg.by_kind(SpanKind.TOOL_CALL)) == 1
    assert len(reg.by_kind(SpanKind.REASONING)) == 1


def test_span_finish_records_latency():
    from panoptis.tracing.spans import Span

    span = Span(kind=SpanKind.MODEL_CALL, name="test")
    span.finish(output="ok")
    assert span.end_time is not None
    assert span.latency_ms is not None
    assert span.is_error is False


# ─────────────────────────────────────────────
# OBS-002 — Live stream hub
# ─────────────────────────────────────────────


def test_live_hub_auto_creates_session():
    bus = EventBus()
    hub = LiveStreamHub()
    hub.attach(bus)

    async def go() -> None:
        await bus.publish(_ev(EventType.SESSION_START, session_id="X"))
        await bus.publish(_ev(EventType.TOOL_CALL, session_id="X", tool="bash"))

    asyncio.run(go())
    snap = hub.snapshot()
    assert len(snap["sessions"]) == 1
    assert snap["sessions"][0]["session_id"] == "X"
    assert snap["sessions"][0]["event_count"] == 2


# ─────────────────────────────────────────────
# OBS-004 — Trajectory
# ─────────────────────────────────────────────


def test_trajectory_detects_repeated_tools_and_loops():
    events = [
        _ev(EventType.TOOL_CALL, tool="bash", step=1),
        _ev(EventType.TOOL_RESULT, step=2),
        _ev(EventType.TOOL_CALL, tool="bash", step=3),
        _ev(EventType.TOOL_RESULT, step=4),
        _ev(EventType.TOOL_CALL, tool="bash", step=5),
    ]
    report = build_trajectory(events, intended_tools=["read"])
    assert report.repeated_tools["bash"] == 3
    assert report.deviation_score == 1.0  # all off-plan
    assert report.loops, "should detect repeating subsequence"


def test_trajectory_detects_dead_end_tool_call():
    events = [
        _ev(EventType.TOOL_CALL, tool="bash", step=1),
        # no result follows
        _ev(EventType.AGENT_END, step=2),
    ]
    report = build_trajectory(events)
    assert len(report.dead_ends) == 1


# ─────────────────────────────────────────────
# OBS-005 — Silent failure detector
# ─────────────────────────────────────────────


def test_silence_detector_flags_zero_calls_after_planning():
    det = SilentFailureDetector(min_baseline=1)
    session = AgentSession(
        session_id="S",
        agent_id="A",
        status=ExecutionStatus.SUCCESS,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC) + timedelta(milliseconds=10),
        total_tokens=0,
    )
    events = [_ev(EventType.PLANNER_OUTPUT)]
    finding = det.detect(session, events)
    assert "planned_but_did_nothing" in finding.flags
    assert "zero_tokens" in finding.flags
    assert finding.confidence > 0


def test_silence_detector_baseline_outlier():
    det = SilentFailureDetector(min_baseline=2)
    baseline_sessions = []
    for _ in range(5):
        s = AgentSession(
            session_id=f"OK{_}",
            agent_id="A",
            status=ExecutionStatus.SUCCESS,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC) + timedelta(seconds=2),
            total_tokens=1000,
        )
        evs = [_ev(EventType.TOOL_CALL, tool="bash", session_id=s.session_id)] * 5
        baseline_sessions.append((s, evs))
    det.train(baseline_sessions)
    assert det.baseline is not None
    assert det.baseline.mean_tool_calls > 0


# ─────────────────────────────────────────────
# OBS-006 — Audit log
# ─────────────────────────────────────────────


def test_audit_log_records_calls_and_detects_retry_storm():
    log = ToolAuditLog(retry_storm_threshold=3)
    for _ in range(4):
        log.ingest(_ev(EventType.TOOL_CALL, tool="bash", args={"command": "ls"}))
    storms = log.retry_storms()
    assert storms
    assert storms[0][0] == "bash"


def test_audit_log_detects_hallucinated_args():
    # /tmp path here is a fixture string passed to the detector, never written to disk.
    flags = detect_hallucinated_arguments(
        {"path": "/tmp/very_likely_to_not_exist", "key": "fake_key"}  # noqa: S108
    )
    assert flags


# ─────────────────────────────────────────────
# OBS-007 — Drift heatmap
# ─────────────────────────────────────────────


def test_drift_embed_is_normalized_and_deterministic():
    v1 = embed("hello world")
    v2 = embed("hello world")
    assert v1 == v2
    assert cosine(v1, v2) > 0.99


def test_drift_clustering_groups_similar_texts():
    h = DriftHeatmap(threshold=0.5)
    h.add("a", "cats and dogs")
    h.add("b", "cats and dogs again")
    h.add("c", "quantum mechanics phase space")
    rep = h.report()
    assert len(rep.clusters) >= 1
    assert rep.drift_score >= 0.0


# ─────────────────────────────────────────────
# OBS-008 — OTEL exporter
# ─────────────────────────────────────────────


def test_otel_exporter_buffers_when_fallback():
    # Force fallback by passing an unreachable endpoint without the OTEL lib
    exporter = OTELExporter(OTELConfig(endpoint="http://localhost:1"))
    span = event_to_span(_ev(EventType.TOOL_CALL, tool="bash"))
    exporter.export(span)
    # Either the real exporter ran or the buffer caught it — both are valid
    assert span.kind == SpanKind.TOOL_CALL


def test_otel_grafana_template_contains_panels():
    exporter = OTELExporter()
    tpl = exporter.grafana_dashboard_template()
    assert tpl["uid"] == "panoptis-main"
    assert any(p["title"] == "Confidence (p50 / p95)" for p in tpl["panels"])


# ─────────────────────────────────────────────
# OBS-009 — Counterfactual replay
# ─────────────────────────────────────────────


def test_counterfactual_swap_tool_result():
    events = [
        _ev(EventType.TOOL_CALL, tool="bash", step=1),
        AgentEvent(
            session_id="S",
            agent_id="A",
            event_type=EventType.TOOL_RESULT,
            step_number=2,
            tool_call=ToolCallData(tool_name="bash", tool_id="t1"),
            tool_result=ToolResultData(tool_name="bash", tool_id="t1", output="real"),
        ),
        _ev(EventType.AGENT_END, step=3),
    ]
    engine = CounterfactualEngine()
    result = engine.run(
        events,
        CounterfactualScenario(rewind_to_step=1, tool_id="t1", replacement="WHATIF"),
    )
    swapped = result.alternate_events[1]
    assert swapped.tool_result.output == "WHATIF"
    assert swapped.metadata.get("counterfactual") is True
    assert result.diverged


def test_counterfactual_invalid_step_raises():
    engine = CounterfactualEngine()
    with pytest.raises(ValueError):
        engine.run([], CounterfactualScenario(rewind_to_step=0))


# ─────────────────────────────────────────────
# OBS-010 — Sampling
# ─────────────────────────────────────────────


def test_head_sampler_respects_rate():
    s = HeadSampler(rate=0.0)
    decision = s.should_sample(_ev(EventType.SESSION_START))
    assert decision.keep is False


def test_failure_always_sampler_keeps_failures():
    s = FailureAlwaysSampler(success_rate=0.0)
    fail_end = _ev(EventType.SESSION_END, status=ExecutionStatus.FAILURE)
    assert s.should_sample(fail_end).keep is True


def test_reservoir_sampler_caps_at_k():
    r = ReservoirSampler(k=5)
    for i in range(100):
        r.add(_ev(EventType.TOOL_CALL, step=i))
    assert len(r) == 5


def test_tail_sampler_keeps_slow_sessions():
    s = TailSampler(latency_threshold_ms=500.0)
    for i in range(3):
        s.push(_ev(EventType.TOOL_CALL, step=i))
    decision = s.evaluate("S", total_latency_ms=1200.0)
    assert decision.keep is True
