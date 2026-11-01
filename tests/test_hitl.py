"""Tests for the human-in-the-loop ambient integration (Issue #482)."""

from __future__ import annotations

import pytest

from panoptis.core.schema import (
    AgentEvent,
    EventType,
    RiskLevel,
    SafetyCheckData,
)
from panoptis.hitl import (
    DecisionOutcome,
    FeedbackLearner,
    HITLOrchestrator,
    HITLThresholds,
    InteractionPattern,
    RequestState,
)
from panoptis.hitl.decisions import HumanDecision
from panoptis.hitl.feedback import ThresholdSuggestion
from panoptis.hitl.orchestrator import HITLError

# --------------------------------------------------------------------------- helpers


def make_event(
    *,
    session_id: str = "sess-1",
    risk: RiskLevel | None = RiskLevel.HIGH,
    event_type: EventType = EventType.TOOL_CALL,
) -> AgentEvent:
    safety = None
    if risk is not None:
        safety = SafetyCheckData(risk_level=risk, risk_score=0.5)
    return AgentEvent(
        session_id=session_id,
        agent_id="agent-1",
        event_type=event_type,
        safety=safety,
    )


class FakeAlertingEngine:
    def __init__(self) -> None:
        self.alerted: list = []

    async def alert_event(self, event) -> dict:
        self.alerted.append(event)
        return {"slack": True, "pagerduty": False}


# --------------------------------------------------------------------------- thresholds


class TestThresholds:
    def test_default_policy_resolves_all_levels(self):
        t = HITLThresholds()
        assert t.pattern_for(RiskLevel.SAFE) is None
        assert t.pattern_for(RiskLevel.LOW) is InteractionPattern.NOTIFY
        assert t.pattern_for(RiskLevel.HIGH) is InteractionPattern.REVIEW
        assert t.pattern_for(RiskLevel.CRITICAL) is InteractionPattern.REVIEW

    def test_requires_human_only_for_question_and_review(self):
        t = HITLThresholds()
        assert t.requires_human(RiskLevel.HIGH) is True  # REVIEW
        assert t.requires_human(RiskLevel.LOW) is False  # NOTIFY
        assert t.requires_human(RiskLevel.SAFE) is False  # None

    def test_invalid_timeout_raises(self):
        with pytest.raises(ValueError):
            HITLThresholds(approval_timeout_seconds=0)

    def test_partial_policy_backfilled(self):
        t = HITLThresholds(policy={RiskLevel.HIGH: InteractionPattern.QUESTION})
        # Missing levels are backfilled from the default.
        assert t.pattern_for(RiskLevel.HIGH) is InteractionPattern.QUESTION
        assert t.pattern_for(RiskLevel.LOW) is InteractionPattern.NOTIFY

    def test_set_pattern(self):
        t = HITLThresholds()
        t.set_pattern(RiskLevel.MEDIUM, InteractionPattern.REVIEW)
        assert t.pattern_for(RiskLevel.MEDIUM) is InteractionPattern.REVIEW


# --------------------------------------------------------------------------- submit / triage


class TestSubmit:
    async def test_safe_event_auto_approved(self):
        o = HITLOrchestrator()
        req = await o.submit(make_event(risk=RiskLevel.SAFE))
        assert req.state is RequestState.AUTO_APPROVED
        assert req.is_actionable is True
        assert req.is_resolved is True

    async def test_event_without_safety_treated_as_safe(self):
        o = HITLOrchestrator()
        req = await o.submit(make_event(risk=None))
        assert req.state is RequestState.AUTO_APPROVED

    async def test_notify_event_auto_approves_after_delivery(self):
        alerting = FakeAlertingEngine()
        o = HITLOrchestrator(alerting_engine=alerting)
        event = make_event(risk=RiskLevel.LOW)
        req = await o.submit(event, payload={"_event": event})
        assert req.pattern is InteractionPattern.NOTIFY
        assert req.state is RequestState.AUTO_APPROVED  # notify doesn't block
        assert len(alerting.alerted) == 1  # it was delivered

    async def test_review_event_stays_pending(self):
        o = HITLOrchestrator()
        req = await o.submit(make_event(risk=RiskLevel.HIGH))
        assert req.pattern is InteractionPattern.REVIEW
        assert req.state is RequestState.PENDING
        assert req.is_resolved is False
        assert req.is_actionable is False

    async def test_explicit_question_forces_question_pattern(self):
        o = HITLOrchestrator()
        # Even a LOW-risk event becomes a QUESTION when a question is supplied.
        req = await o.submit(make_event(risk=RiskLevel.LOW), question="Which environment?")
        assert req.pattern is InteractionPattern.QUESTION
        assert req.state is RequestState.PENDING
        assert req.question == "Which environment?"

    async def test_custom_summary_used(self):
        o = HITLOrchestrator()
        req = await o.submit(make_event(risk=RiskLevel.HIGH), summary="Delete prod table")
        assert req.summary == "Delete prod table"


# --------------------------------------------------------------------------- decisions


class TestDecisions:
    async def _pending_review(self, orch: HITLOrchestrator):
        return await orch.submit(make_event(risk=RiskLevel.HIGH))

    async def test_approve(self):
        o = HITLOrchestrator()
        req = await self._pending_review(o)
        out = o.approve(req.request_id, "alice", comment="looks fine")
        assert out.state is RequestState.APPROVED
        assert out.is_actionable is True
        assert o.audit_trail()[-1].decided_by == "alice"
        assert o.audit_trail()[-1].outcome is DecisionOutcome.APPROVE

    async def test_reject(self):
        o = HITLOrchestrator()
        req = await self._pending_review(o)
        out = o.reject(req.request_id, "bob")
        assert out.state is RequestState.REJECTED
        assert out.is_actionable is False

    async def test_edit_replaces_payload(self):
        o = HITLOrchestrator()
        req = await o.submit(make_event(risk=RiskLevel.HIGH), payload={"cmd": "rm -rf /"})
        out = o.edit(req.request_id, "carol", {"cmd": "rm -rf /tmp/scratch"})
        assert out.state is RequestState.EDITED
        assert out.payload == {"cmd": "rm -rf /tmp/scratch"}
        assert out.is_actionable is True
        assert o.audit_trail()[-1].edited_payload == {"cmd": "rm -rf /tmp/scratch"}

    async def test_answer_a_question(self):
        o = HITLOrchestrator()
        req = await o.submit(make_event(risk=RiskLevel.MEDIUM), question="Which region?")
        out = o.answer(req.request_id, "dave", "us-east-1")
        assert out.state is RequestState.ANSWERED
        assert out.answer == "us-east-1"

    async def test_expire_pending(self):
        o = HITLOrchestrator()
        req = await self._pending_review(o)
        out = o.expire(req.request_id)
        assert out.state is RequestState.EXPIRED
        assert out.is_actionable is False

    async def test_cannot_decide_unknown_request(self):
        o = HITLOrchestrator()
        with pytest.raises(HITLError):
            o.approve("nope", "alice")

    async def test_cannot_decide_resolved_request(self):
        o = HITLOrchestrator()
        req = await self._pending_review(o)
        o.approve(req.request_id, "alice")
        with pytest.raises(HITLError):
            o.approve(req.request_id, "alice")

    async def test_wrong_pattern_decision_rejected(self):
        o = HITLOrchestrator()
        # A REVIEW request cannot be resolved via answer() (that's for QUESTION).
        req = await self._pending_review(o)
        with pytest.raises(HITLError):
            o.answer(req.request_id, "alice", "hi")

    async def test_cannot_expire_resolved(self):
        o = HITLOrchestrator()
        req = await self._pending_review(o)
        o.approve(req.request_id, "alice")
        with pytest.raises(HITLError):
            o.expire(req.request_id)

    async def test_expire_unknown_raises(self):
        o = HITLOrchestrator()
        with pytest.raises(HITLError):
            o.expire("nope")


# --------------------------------------------------------------------------- queries / audit


class TestQueriesAndAudit:
    async def test_pending_list(self):
        o = HITLOrchestrator()
        await o.submit(make_event(risk=RiskLevel.HIGH))
        await o.submit(make_event(risk=RiskLevel.CRITICAL))
        await o.submit(make_event(risk=RiskLevel.SAFE))  # auto-approved, not pending
        assert len(o.pending()) == 2

    async def test_get_request(self):
        o = HITLOrchestrator()
        req = await o.submit(make_event(risk=RiskLevel.HIGH))
        assert o.get(req.request_id) is req
        assert o.get("missing") is None

    async def test_audit_trail_records_every_decision(self):
        o = HITLOrchestrator()
        r1 = await o.submit(make_event(session_id="s1", risk=RiskLevel.HIGH))
        r2 = await o.submit(make_event(session_id="s2", risk=RiskLevel.HIGH))
        o.approve(r1.request_id, "alice")
        o.reject(r2.request_id, "bob")
        assert len(o.audit_trail()) == 2
        assert len(o.audit_for_session("s1")) == 1
        assert o.audit_for_session("s1")[0].outcome is DecisionOutcome.APPROVE

    async def test_auto_approval_is_audited(self):
        o = HITLOrchestrator()
        await o.submit(make_event(risk=RiskLevel.SAFE))
        trail = o.audit_trail()
        assert len(trail) == 1
        assert trail[0].outcome is DecisionOutcome.AUTO_APPROVE

    def test_human_decision_to_dict_roundtrips(self):
        d = HumanDecision.now("req-1", DecisionOutcome.APPROVE, "alice", "s1", comment="ok")
        payload = d.to_dict()
        assert payload["request_id"] == "req-1"
        assert payload["outcome"] == "approve"
        assert payload["decided_by"] == "alice"

    async def test_audit_trail_supports_len(self):
        from panoptis.hitl.decisions import _AuditTrail

        trail = _AuditTrail()
        assert len(trail) == 0
        trail.record(HumanDecision.now("r", DecisionOutcome.APPROVE, "a", "s1"))
        assert len(trail) == 1

    async def test_request_to_dict_shape(self):
        o = HITLOrchestrator()
        req = await o.submit(make_event(risk=RiskLevel.HIGH), summary="x")
        d = req.to_dict()
        assert d["risk_level"] == "high"
        assert d["pattern"] == "review"
        assert d["state"] == "pending"


# --------------------------------------------------------------------------- delivery


class TestDelivery:
    async def test_delivery_failure_does_not_break_submit(self):
        class BrokenAlerting:
            async def alert_event(self, event):
                raise RuntimeError("webhook down")

        o = HITLOrchestrator(alerting_engine=BrokenAlerting())
        event = make_event(risk=RiskLevel.HIGH)
        # Should still create the pending request despite delivery raising.
        req = await o.submit(event, payload={"_event": event})
        assert req.state is RequestState.PENDING

    async def test_no_alerting_engine_is_fine(self):
        o = HITLOrchestrator()
        req = await o.submit(make_event(risk=RiskLevel.HIGH))
        assert req.state is RequestState.PENDING


# --------------------------------------------------------------------------- feedback learning


class TestFeedbackLearner:
    def test_invalid_config_raises(self):
        with pytest.raises(ValueError):
            FeedbackLearner(relax_above=0.4, tighten_below=0.5)  # tighten >= relax
        with pytest.raises(ValueError):
            FeedbackLearner(min_samples=0)

    def test_records_approval_rate(self):
        fl = FeedbackLearner(min_samples=2)
        fl.record(RiskLevel.HIGH, DecisionOutcome.APPROVE)
        fl.record(RiskLevel.HIGH, DecisionOutcome.APPROVE)
        fl.record(RiskLevel.HIGH, DecisionOutcome.REJECT)
        assert fl.approval_rate(RiskLevel.HIGH) == pytest.approx(2 / 3)

    def test_edit_counts_as_approval(self):
        fl = FeedbackLearner()
        fl.record(RiskLevel.HIGH, DecisionOutcome.EDIT)
        assert fl.approval_rate(RiskLevel.HIGH) == 1.0

    def test_no_suggestion_below_min_samples(self):
        fl = FeedbackLearner(min_samples=10)
        t = HITLThresholds()
        for _ in range(5):
            fl.record(RiskLevel.HIGH, DecisionOutcome.APPROVE)
        assert fl.suggest(t) == []

    def test_relax_suggestion_on_high_approval(self):
        fl = FeedbackLearner(min_samples=5, relax_above=0.9)
        t = HITLThresholds()  # HIGH -> REVIEW
        for _ in range(10):
            fl.record(RiskLevel.HIGH, DecisionOutcome.APPROVE)
        suggestions = fl.suggest(t)
        assert len(suggestions) == 1
        assert suggestions[0].risk_level is RiskLevel.HIGH
        assert suggestions[0].current is InteractionPattern.REVIEW
        assert suggestions[0].suggested is InteractionPattern.QUESTION  # relaxed one step

    def test_tighten_suggestion_on_low_approval(self):
        fl = FeedbackLearner(min_samples=5, tighten_below=0.5)
        t = HITLThresholds(policy={RiskLevel.MEDIUM: InteractionPattern.NOTIFY})
        for _ in range(8):
            fl.record(RiskLevel.MEDIUM, DecisionOutcome.REJECT)
        suggestions = fl.suggest(t)
        assert suggestions[0].risk_level is RiskLevel.MEDIUM
        assert suggestions[0].suggested is InteractionPattern.QUESTION  # tightened one step

    def test_critical_never_relaxed_below_review(self):
        fl = FeedbackLearner(min_samples=5, relax_above=0.9)
        t = HITLThresholds()  # CRITICAL -> REVIEW
        for _ in range(10):
            fl.record(RiskLevel.CRITICAL, DecisionOutcome.APPROVE)
        # No suggestion: CRITICAL stays at REVIEW regardless of approval rate.
        assert fl.suggest(t) == []

    def test_apply_mutates_thresholds(self):
        fl = FeedbackLearner(min_samples=5, relax_above=0.9)
        t = HITLThresholds()
        for _ in range(10):
            fl.record(RiskLevel.HIGH, DecisionOutcome.APPROVE)
        applied = fl.apply(t)
        assert len(applied) == 1
        assert t.pattern_for(RiskLevel.HIGH) is InteractionPattern.QUESTION

    def test_suggestion_to_dict(self):
        s = ThresholdSuggestion(
            risk_level=RiskLevel.HIGH,
            current=InteractionPattern.REVIEW,
            suggested=InteractionPattern.QUESTION,
            reason="test",
        )
        d = s.to_dict()
        assert d["risk_level"] == "high"
        assert d["current"] == "review"
        assert d["suggested"] == "question"

    def test_mid_approval_no_suggestion(self):
        # An approval rate between tighten_below and relax_above yields nothing.
        fl = FeedbackLearner(min_samples=4, relax_above=0.9, tighten_below=0.3)
        t = HITLThresholds()
        fl.record(RiskLevel.HIGH, DecisionOutcome.APPROVE)
        fl.record(RiskLevel.HIGH, DecisionOutcome.APPROVE)
        fl.record(RiskLevel.HIGH, DecisionOutcome.REJECT)
        fl.record(RiskLevel.HIGH, DecisionOutcome.REJECT)  # 50% approval
        assert fl.suggest(t) == []

    def test_relax_at_floor_stays_none(self):
        # A level already at the most-relaxed pattern (None) cannot relax further,
        # so no suggestion is produced even at a very high approval rate.
        fl = FeedbackLearner(min_samples=2, relax_above=0.9)
        t = HITLThresholds(policy={RiskLevel.LOW: None})
        for _ in range(5):
            fl.record(RiskLevel.LOW, DecisionOutcome.APPROVE)
        assert fl.suggest(t) == []

    def test_tighten_at_ceiling_stays_review(self):
        # A level already at REVIEW cannot tighten further.
        fl = FeedbackLearner(min_samples=2, tighten_below=0.5)
        t = HITLThresholds()  # HIGH -> REVIEW
        for _ in range(5):
            fl.record(RiskLevel.HIGH, DecisionOutcome.REJECT)
        assert fl.suggest(t) == []


# --------------------------------------------------------------------------- orchestrator + feedback


class TestOrchestratorFeedbackIntegration:
    async def test_human_decisions_feed_the_learner(self):
        fl = FeedbackLearner(min_samples=3)
        o = HITLOrchestrator(feedback_learner=fl)
        for _ in range(3):
            req = await o.submit(make_event(risk=RiskLevel.HIGH))
            o.approve(req.request_id, "alice")
        assert fl.approval_rate(RiskLevel.HIGH) == 1.0

    async def test_auto_approval_does_not_feed_learner(self):
        fl = FeedbackLearner()
        o = HITLOrchestrator(feedback_learner=fl)
        # SAFE events auto-approve and must NOT count as human approvals.
        await o.submit(make_event(risk=RiskLevel.SAFE))
        assert fl.approval_rate(RiskLevel.SAFE) == 0.0

    async def test_full_lifecycle_review_approved(self):
        fl = FeedbackLearner(min_samples=1, relax_above=0.9)
        alerting = FakeAlertingEngine()
        o = HITLOrchestrator(alerting_engine=alerting, feedback_learner=fl)
        event = make_event(risk=RiskLevel.HIGH)
        req = await o.submit(event, payload={"_event": event}, summary="risky op")
        assert req.state is RequestState.PENDING
        assert len(alerting.alerted) == 1  # surfaced
        out = o.approve(req.request_id, "alice", comment="ok")
        assert out.is_actionable
        # Decision is audited and fed to the learner.
        assert len(o.audit_trail()) == 1
        assert fl.approval_rate(RiskLevel.HIGH) == 1.0
