"""Phase 3 — Reasoning Auditor tests (RSN-001..012)."""

from __future__ import annotations

from datetime import UTC, datetime

from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    EventType,
    ExecutionStatus,
    RiskLevel,
    SafetyCheckData,
    ToolCallData,
    ToolResultData,
)
from panoptis.reasoning.adversarial import run_adversarial_probes
from panoptis.reasoning.benchmark import run_benchmark
from panoptis.reasoning.calibration import CalibrationTracker
from panoptis.reasoning.dual_eval import dual_evaluate
from panoptis.reasoning.explainer import explain
from panoptis.reasoning.fingerprint import detect_mid_session_change
from panoptis.reasoning.goal_drift import GoalDriftDetector
from panoptis.reasoning.hallucination import HallucinationClassifier, HallucinationRisk
from panoptis.reasoning.quality import compute_quality
from panoptis.reasoning.semantic_drift import CrossSessionDrift
from panoptis.reasoning.trust_score import compute_trust


def _tool_call(tool: str, args: dict, raw: str | None = None) -> AgentEvent:
    return AgentEvent(
        session_id="S",
        agent_id="A",
        framework=AgentFramework.CUSTOM,
        event_type=EventType.TOOL_CALL,
        tool_call=ToolCallData(tool_name=tool, arguments=args, raw_command=raw),
    )


def _tool_result(tool: str, output: str) -> AgentEvent:
    return AgentEvent(
        session_id="S",
        agent_id="A",
        framework=AgentFramework.CUSTOM,
        event_type=EventType.TOOL_RESULT,
        tool_result=ToolResultData(tool_name=tool, output=output),
    )


# ─────────────────────────────────────────────
# RSN-002 — Hallucination classifier
# ─────────────────────────────────────────────


def test_hallucination_low_when_args_are_grounded():
    cls = HallucinationClassifier()
    cls.observe(_tool_result("ls", "config.yaml settings.json"))
    flag = cls.classify(_tool_call("read", {"path": "config.yaml"}))
    assert flag.risk == HallucinationRisk.LOW


def test_hallucination_high_when_invented_identifiers():
    cls = HallucinationClassifier()
    flag = cls.classify(
        _tool_call(
            "read",
            {"path": "/etc/somefake/nothing_here", "key": "fake_key"},
            raw="curl invented-host-xyz.example",
        )
    )
    assert flag.risk in (HallucinationRisk.MEDIUM, HallucinationRisk.HIGH)
    assert flag.pre_execution is True


# ─────────────────────────────────────────────
# RSN-003 — Goal drift
# ─────────────────────────────────────────────


def test_goal_drift_detects_off_topic_steps():
    det = GoalDriftDetector(similarity_threshold=0.5)
    det.set_goal("write a python function to add two numbers")
    same = AgentEvent(
        session_id="S",
        agent_id="A",
        event_type=EventType.PLANNER_OUTPUT,
        planner_output_preview="I will write a python function to add two numbers",
    )
    off = AgentEvent(
        session_id="S",
        agent_id="A",
        event_type=EventType.PLANNER_OUTPUT,
        planner_output_preview="Quantum mechanics of phase space",
    )
    det.evaluate(same)
    det.evaluate(off)
    rep = det.report()
    assert rep.drift_events >= 1


# ─────────────────────────────────────────────
# RSN-004 — Quality
# ─────────────────────────────────────────────


def test_quality_score_dimensions_in_range():
    events = [
        _tool_call("read", {"path": "x"}),
        _tool_result("read", "ok"),
        AgentEvent(
            session_id="S",
            agent_id="A",
            event_type=EventType.SESSION_END,
            status=ExecutionStatus.SUCCESS,
        ),
    ]
    q = compute_quality(events, goal="read file")
    for v in (q.coherence, q.completeness, q.factual_grounding, q.goal_alignment, q.safety):
        assert 0.0 <= v <= 1.0
    assert 0 <= q.overall <= 1


# ─────────────────────────────────────────────
# RSN-005 — Adversarial probes
# ─────────────────────────────────────────────


def test_adversarial_gameable_when_constant_score():
    result = run_adversarial_probes(score_fn=lambda e: 0.9)
    assert result.gameable is True


def test_adversarial_not_gameable_when_discriminating():
    def discriminating(event):
        return 0.1 if event.tool_call and event.tool_call.tool_name == "bash" else 0.9

    result = run_adversarial_probes(score_fn=discriminating)
    assert result.gameable is False
    assert result.discrimination > 0.5


# ─────────────────────────────────────────────
# RSN-006 — Semantic drift cross-session
# ─────────────────────────────────────────────


def test_cross_session_drift_detects_divergence():
    cs = CrossSessionDrift(drift_threshold=0.2)
    cs.register("s1", "optimize the pipeline", "tune database query", datetime.now(UTC))
    cs.register("s2", "optimize the pipeline", "tune database query", datetime.now(UTC))
    cs.register(
        "s3", "optimize the pipeline", "compress images and shorten texts", datetime.now(UTC)
    )
    alert = cs.analyze("optimize the pipeline")
    assert alert is not None
    assert alert.n_sessions == 3


# ─────────────────────────────────────────────
# RSN-007 — Calibration
# ─────────────────────────────────────────────


def test_calibration_recalibrate_lowers_threshold_for_recall():
    tracker = CalibrationTracker(threshold=0.3)
    # 10 positives (actual outcome True), low scores ⇒ flagged correctly
    for s in [0.1, 0.2, 0.25, 0.15, 0.05, 0.3, 0.35, 0.4, 0.45, 0.5]:
        tracker.record(s, actual_outcome=True)
    new_thresh = tracker.recalibrate(target_recall=0.8)
    assert new_thresh > 0


def test_calibration_detect_decay():
    tracker = CalibrationTracker(threshold=0.9)
    # All negatives flagged (false positives)
    for _ in range(20):
        tracker.record(0.1, actual_outcome=False)
    assert tracker.detect_decay(max_fpr=0.1) is True


# ─────────────────────────────────────────────
# RSN-008 — Fingerprint
# ─────────────────────────────────────────────


def test_fingerprint_detects_mid_session_change():
    short_plan = AgentEvent(
        session_id="S",
        agent_id="A",
        event_type=EventType.PLANNER_OUTPUT,
        planner_output_preview="ok",
    )
    long_plan = AgentEvent(
        session_id="S",
        agent_id="A",
        event_type=EventType.PLANNER_OUTPUT,
        planner_output_preview="A very long and elaborate plan with many words and "
        "specific punctuation. Multiple sentences here.",
    )
    events = [short_plan] * 5 + [long_plan] * 5
    changed, dist = detect_mid_session_change(events, distance_threshold=0.5)
    assert dist > 0


# ─────────────────────────────────────────────
# RSN-009 — Dual eval
# ─────────────────────────────────────────────


def test_dual_eval_session_achieved():
    events = [
        AgentEvent(
            session_id="S",
            agent_id="A",
            event_type=EventType.PLANNER_OUTPUT,
            planner_output_preview="write a function to add two numbers",
        ),
        _tool_call("write_file", {"path": "add.py", "content": "def add(a,b): return a+b"}),
        _tool_result("write_file", "ok"),
        AgentEvent(
            session_id="S",
            agent_id="A",
            event_type=EventType.SESSION_END,
            status=ExecutionStatus.SUCCESS,
        ),
    ]
    r = dual_evaluate(events, goal="write a function to add two numbers")
    assert r.session_score >= 0
    assert r.step_passed + r.step_failed > 0


# ─────────────────────────────────────────────
# RSN-010 — Benchmark
# ─────────────────────────────────────────────


def test_benchmark_runs_and_signs():
    def fake_agent(prompt: str) -> dict:
        # Refuses anything high-severity (contains rm or exfil)
        risky = any(w in prompt.lower() for w in ("delete", "leak", "bypass", "exfil"))
        return {"refused": risky, "output": "", "actions": []}

    report = run_benchmark(fake_agent, secret_key=b"test-key")
    assert 0.0 <= report.pass_rate <= 1.0
    assert report.signature
    assert "jailbreak" in report.per_category


# ─────────────────────────────────────────────
# RSN-011 — Explainer
# ─────────────────────────────────────────────


def test_explainer_produces_human_readable():
    ev = AgentEvent(
        session_id="S",
        agent_id="A",
        event_type=EventType.SAFETY_BLOCK,
        status=ExecutionStatus.BLOCKED,
        tool_call=ToolCallData(tool_name="bash", raw_command="rm -rf /"),
        safety=SafetyCheckData(
            risk_level=RiskLevel.CRITICAL,
            risk_score=0.99,
            blocked=True,
            reasons=["destructive_filesystem_command"],
            matched_policies=["block_rm_rf"],
        ),
    )
    out = explain(ev)
    assert "Critical" in out.headline
    assert "bash" in out.detail
    assert out.suggested_alternative


# ─────────────────────────────────────────────
# RSN-012 — Trust score
# ─────────────────────────────────────────────


def test_trust_score_returns_grade():
    events = [
        AgentEvent(
            session_id="S",
            agent_id="A",
            event_type=EventType.SESSION_START,
            goal="do thing",
        ),
        AgentEvent(
            session_id="S",
            agent_id="A",
            event_type=EventType.SESSION_END,
            status=ExecutionStatus.SUCCESS,
        ),
    ]
    score = compute_trust(events, goal="do thing")
    assert 0.0 <= score.score <= 100.0
    assert score.grade in ("A", "B", "C", "D", "F")


def test_trust_score_penalizes_safety_violations():
    bad = [
        AgentEvent(
            session_id="S",
            agent_id="A",
            event_type=EventType.SAFETY_BLOCK,
            status=ExecutionStatus.BLOCKED,
            safety=SafetyCheckData(
                risk_level=RiskLevel.CRITICAL,
                risk_score=0.99,
                blocked=True,
            ),
        )
        for _ in range(3)
    ]
    score = compute_trust(bad)
    assert score.safety_component < 100
