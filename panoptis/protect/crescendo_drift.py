"""Panoptis Crescendo Multi-Turn Drift & Context Erosion Sentry.

Defeats:
- "The Crescendo Multi-Turn Jailbreak Attack" (Russinovich et al., Microsoft / USENIX Security 2025)

Key defenses:
1. Conversation Intent Anchoring (Session Objective Anchor).
2. Monotonic Risk & Semantic Drift Velocity Tracking across turns.
3. Cumulative Divergence Integral over multi-turn conversation traces.
4. Early Trajectory Interception before policy breach boundary is crossed.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from panoptis.protect.model import Verdict


class DriftStatus(StrEnum):
    """Status of conversation semantic drift."""

    ALIGNED = "ALIGNED"
    MODERATE_DRIFT = "MODERATE_DRIFT"
    CRESCENDO_DRIFT_SUSPECTED = "CRESCENDO_DRIFT_SUSPECTED"
    CRITICAL_BREACH = "CRITICAL_BREACH"


# High-risk topic centroids targeted by incremental multi-turn attacks
RESTRICTED_CENTROIDS = {
    "credential_exfiltration": [
        "token",
        "secret",
        "password",
        "key",
        "credential",
        "private",
        "leak",
        "exfiltrate",
        "dump",
        "export",
        "auth",
    ],
    "arbitrary_execution": [
        "shell",
        "terminal",
        "bash",
        "powershell",
        "execute",
        "eval",
        "command",
        "subprocess",
        "exec",
        "system",
    ],
    "privilege_escalation": [
        "root",
        "sudo",
        "admin",
        "privilege",
        "bypass",
        "override",
        "unrestricted",
        "grant",
        "permission",
    ],
    "destructive_operations": [
        "rm",
        "delete",
        "drop",
        "wipe",
        "truncate",
        "kill",
        "purge",
        "destroy",
        "format",
    ],
}


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\b[a-zA-Z]{3,}\b", text.lower()))


@dataclass
class ConversationTurn:
    """A single turn in the multi-turn session trajectory."""

    turn_number: int
    user_input: str
    action_name: str
    arguments: dict[str, Any]
    risk_score: float
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True, slots=True)
class DriftAssessment:
    """Outcome of evaluating conversational trajectory drift."""

    status: DriftStatus
    verdict: Verdict
    cumulative_drift: float
    velocity: float
    consecutive_risk_increases: int
    reason: str


class CrescendoDriftMonitor:
    """Monitors multi-turn conversation trajectories to detect and block incremental Crescendo jailbreaks."""

    def __init__(
        self,
        velocity_threshold: float = 0.15,
        cumulative_drift_limit: float = 0.65,
        consecutive_rise_limit: int = 3,
        max_sessions: int = 5_000,
    ) -> None:
        self.velocity_threshold = velocity_threshold
        self.cumulative_drift_limit = cumulative_drift_limit
        self.consecutive_rise_limit = consecutive_rise_limit
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, list[ConversationTurn]] = OrderedDict()
        self._anchors: OrderedDict[str, str] = OrderedDict()

    def register_session_anchor(self, session_id: str, initial_objective: str) -> None:
        """Register the authoritative initial session objective as the anchor."""
        if len(self._anchors) >= self.max_sessions:
            self._anchors.popitem(last=False)
        self._anchors[session_id] = initial_objective.strip()
        if len(self._sessions) >= self.max_sessions:
            self._sessions.popitem(last=False)
        self._sessions[session_id] = []

    def _compute_risk_score(self, text: str) -> float:
        """Calculate semantic proximity to restricted threat centroids."""
        tokens = _tokenize(text)
        if not tokens:
            return 0.0

        matches = 0
        sum(len(words) for words in RESTRICTED_CENTROIDS.values())

        for centroid_words in RESTRICTED_CENTROIDS.values():
            overlap = tokens.intersection(centroid_words)
            matches += len(overlap) * 2  # Weight keyword matches

        return min(1.0, matches / max(10, len(tokens) * 0.5))

    def _compute_anchor_divergence(self, anchor: str, current_turn_text: str) -> float:
        """Compute Jaccard distance between the initial anchor and current turn."""
        anchor_tokens = _tokenize(anchor)
        current_tokens = _tokenize(current_turn_text)

        if not anchor_tokens or not current_tokens:
            return 0.0

        intersection = len(anchor_tokens.intersection(current_tokens))
        union = len(anchor_tokens.union(current_tokens))
        jaccard_similarity = intersection / union if union > 0 else 1.0
        return 1.0 - jaccard_similarity

    def record_and_evaluate_turn(
        self,
        session_id: str,
        user_input: str,
        action_name: str,
        arguments: dict[str, Any],
    ) -> DriftAssessment:
        """Record a conversation turn and evaluate whether Crescendo trajectory drift is occurring."""
        anchor = self._anchors.get(session_id, user_input)
        if session_id not in self._anchors:
            self.register_session_anchor(session_id, user_input)
        else:
            self._anchors.move_to_end(session_id)

        if session_id not in self._sessions:
            if len(self._sessions) >= self.max_sessions:
                self._sessions.popitem(last=False)
            self._sessions[session_id] = []
        else:
            self._sessions.move_to_end(session_id)

        turns = self._sessions[session_id]
        turn_number = len(turns) + 1

        combined_text = f"{user_input} {action_name} {str(arguments)}"
        current_risk = self._compute_risk_score(combined_text)
        anchor_div = self._compute_anchor_divergence(anchor, combined_text)

        turn = ConversationTurn(
            turn_number=turn_number,
            user_input=user_input,
            action_name=action_name,
            arguments=arguments,
            risk_score=current_risk,
        )
        turns.append(turn)

        if len(turns) < 2:
            return DriftAssessment(
                status=DriftStatus.ALIGNED,
                verdict=Verdict.ALLOW,
                cumulative_drift=anchor_div,
                velocity=0.0,
                consecutive_risk_increases=0,
                reason="Initial turn anchored safely.",
            )

        # Calculate multi-turn velocity and monotonic trend
        risk_deltas: list[float] = []
        consecutive_rises = 0

        for i in range(1, len(turns)):
            delta = turns[i].risk_score - turns[i - 1].risk_score
            risk_deltas.append(delta)
            if delta > 0.03:  # Noticeable positive slope
                consecutive_rises += 1
            else:
                consecutive_rises = 0

        avg_velocity = sum(risk_deltas) / len(risk_deltas) if risk_deltas else 0.0
        cumulative_drift = (anchor_div * 0.5) + (current_risk * 0.5)

        # Crescendo Condition: Monotonic risk rise across 3+ turns OR high velocity toward restricted space
        if consecutive_rises >= self.consecutive_rise_limit and current_risk > 0.25:
            return DriftAssessment(
                status=DriftStatus.CRESCENDO_DRIFT_SUSPECTED,
                verdict=Verdict.BLOCK,
                cumulative_drift=cumulative_drift,
                velocity=avg_velocity,
                consecutive_risk_increases=consecutive_rises,
                reason=(
                    f"Crescendo multi-turn drift detected: {consecutive_rises} consecutive turns "
                    f"with increasing proximity to restricted threat centroids (velocity={avg_velocity:.3f})."
                ),
            )

        # CUSUM sequential test (Page 1954 / CURA 2026): S_t = max(0, S_{t-1} + (X_t - k))
        # Detects subtle, persistent positive shifts in mean risk score
        cusum = 0.0
        baseline_k = 0.05
        for t in turns:
            cusum = max(0.0, cusum + (t.risk_score - baseline_k))

        if cusum >= 0.50 and current_risk > 0.20 and len(turns) >= 3:
            return DriftAssessment(
                status=DriftStatus.CRESCENDO_DRIFT_SUSPECTED,
                verdict=Verdict.BLOCK,
                cumulative_drift=cumulative_drift,
                velocity=avg_velocity,
                consecutive_risk_increases=consecutive_rises,
                reason=(
                    f"Crescendo CUSUM sequential drift detected (cusum={cusum:.3f}): "
                    f"persistent subtle multi-turn shift toward restricted capabilities."
                ),
            )

        if cumulative_drift >= self.cumulative_drift_limit and len(turns) >= 3:
            return DriftAssessment(
                status=DriftStatus.CRITICAL_BREACH,
                verdict=Verdict.BLOCK,
                cumulative_drift=cumulative_drift,
                velocity=avg_velocity,
                consecutive_risk_increases=consecutive_rises,
                reason=f"Cumulative semantic drift {cumulative_drift:.2f} exceeds threshold {self.cumulative_drift_limit:.2f}.",
            )

        if avg_velocity >= self.velocity_threshold and current_risk > 0.2:
            return DriftAssessment(
                status=DriftStatus.MODERATE_DRIFT,
                verdict=Verdict.ALLOW,
                cumulative_drift=cumulative_drift,
                velocity=avg_velocity,
                consecutive_risk_increases=consecutive_rises,
                reason=f"Elevated drift velocity {avg_velocity:.3f} observed, monitoring closely.",
            )

        return DriftAssessment(
            status=DriftStatus.ALIGNED,
            verdict=Verdict.ALLOW,
            cumulative_drift=cumulative_drift,
            velocity=avg_velocity,
            consecutive_risk_increases=consecutive_rises,
            reason="Trajectory remains safely aligned with initial session anchor.",
        )
