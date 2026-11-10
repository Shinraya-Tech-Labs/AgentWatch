"""
PANOPTIS Universal Event Schema
All agent events are normalized into this schema regardless of framework.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ─────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────


class EventType(str, Enum):
    """Normalized event kinds emitted by PANOPTIS adapters."""

    # Lifecycle
    SESSION_START = "session.start"
    SESSION_END = "session.end"
    AGENT_START = "agent.start"
    AGENT_END = "agent.end"
    AGENT_ERROR = "agent.error"

    # Planning
    PLANNER_INPUT = "planner.input"
    PLANNER_OUTPUT = "planner.output"
    GOAL_SET = "goal.set"
    GOAL_DRIFT = "goal.drift"

    # Tool execution
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"
    TOOL_RETRY = "tool.retry"

    # Memory
    MEMORY_READ = "memory.read"
    MEMORY_WRITE = "memory.write"
    MEMORY_EVICT = "memory.evict"
    MEMORY_CONTRADICTION = "memory.contradiction"

    # Safety
    SAFETY_CHECK = "safety.check"
    SAFETY_BLOCK = "safety.block"
    SAFETY_APPROVE = "safety.approve"
    SAFETY_ESCALATE = "safety.escalate"

    # Multi-agent
    AGENT_MESSAGE = "agent.message"
    TASK_DELEGATE = "task.delegate"
    TASK_COMPLETE = "task.complete"
    TASK_FAIL = "task.fail"

    # Rollback
    CHECKPOINT_CREATE = "checkpoint.create"
    ROLLBACK_TRIGGER = "rollback.trigger"
    ROLLBACK_COMPLETE = "rollback.complete"

    # Confidence
    CONFIDENCE_SCORE = "confidence.score"
    ANOMALY_DETECTED = "anomaly.detected"

    # Reasoning auditor
    STYLE_FINGERPRINT_COMPUTED = "reasoning.style_fingerprint"
    STYLE_SWAP_DETECTED = "reasoning.style_swap"

    # LLM intent
    AGENT_INTENT = "agent.intent"

    # Generic
    CUSTOM = "custom"


class RiskLevel(str, Enum):
    """Severity tier assigned by the safety engine to a tool call or action."""

    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AgentFramework(str, Enum):
    """Source framework that produced an event or session."""

    CLAUDE_CODE = "claude_code"
    LANGCHAIN = "langchain"
    CREWAI = "crewai"
    AUTOGPT = "autogpt"
    OPENAI_AGENTS = "openai_agents"
    HERMES = "hermes"
    OPENCLAW = "openclaw"
    CUSTOM = "custom"


class ExecutionStatus(str, Enum):
    """Lifecycle state of an event, session, or task."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"
    ROLLED_BACK = "rolled_back"
    TIMEOUT = "timeout"


# ─────────────────────────────────────────────
# Core sub-models
# ─────────────────────────────────────────────


class TokenUsage(BaseModel):
    """LLM token counts and optional cost estimate for a single step."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None


class ToolCallData(BaseModel):
    """Payload for a tool invocation before execution."""

    tool_name: str
    tool_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_command: str | None = None
    affected_resources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_command_fields(self) -> ToolCallData:
        """Catch the silent-risk footgun: command-like argument key without raw_command.

        When a caller puts the shell command in ``arguments['command']`` but leaves
        ``raw_command`` unset, the safety engine sees an empty scorer input and
        classifies the call as SAFE — even for ``rm -rf /``.  Raising early here
        surfaces the mistake at construction time rather than silently neutering
        safety checks at runtime.

        Raises:
            ValueError: If a command-like argument key holds a non-empty value and
                ``raw_command`` is not set.  Use ``raw_command=<value>`` or call
                :meth:`ToolCallData.from_dict` to auto-populate it.
        """
        _cmd_keys = frozenset({"command", "cmd", "shell", "exec", "bash", "script"})
        offending = next(
            (
                k
                for k in _cmd_keys
                if k in self.arguments
                and isinstance(self.arguments[k], str)
                and self.arguments[k].strip()
            ),
            None,
        )
        if offending and not (self.raw_command and self.raw_command.strip()):
            val = self.arguments[offending]
            raise ValueError(
                f"ToolCallData has '{offending}' in arguments (value: {val!r}) "
                f"but raw_command is not set. "
                f"The safety engine reads raw_command for pattern matching — "
                f"set raw_command='{val}' or use ToolCallData.from_dict() "
                f"to auto-populate it."
            )
        return self

    @classmethod
    def from_dict(cls, tool_name: str, params_dict: dict[str, Any]) -> ToolCallData:
        """Convenience constructor that auto-maps common parameter names to raw_command.

        Searches ``params_dict`` for a command-like key (``command``, ``cmd``,
        ``shell``, ``exec``, ``bash``, ``script``) and promotes its value to
        ``raw_command`` so the safety engine can scan it.

        Args:
            tool_name: Name of the tool being called.
            params_dict: Arbitrary parameters dict from the tool caller.

        Returns:
            A :class:`ToolCallData` with ``raw_command`` set when a command key
            is found, or ``None`` when no recognizable command key is present.
        """
        promote_keys = ("command", "cmd", "shell", "exec", "bash", "script")
        raw_command: str | None = None
        for key in promote_keys:
            val = params_dict.get(key)
            if isinstance(val, str) and val.strip():
                raw_command = val.strip()
                break
        return cls(
            tool_name=tool_name,
            arguments=dict(params_dict),
            raw_command=raw_command,
        )


class ToolResultData(BaseModel):
    """Payload for a completed or failed tool execution."""

    tool_name: str
    tool_id: str | None = None
    output: Any = None
    error: str | None = None
    exit_code: int | None = None
    execution_time_ms: float | None = None


class SafetyCheckData(BaseModel):
    """Outcome of a safety policy evaluation on a tool call."""

    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=1.0)
    blocked: bool = False
    reasons: list[str] = Field(default_factory=list)
    matched_policies: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    approval_timeout_seconds: int = 60
    blast_radius: dict[str, Any] | None = None


class MemoryData(BaseModel):
    """Memory read/write metadata (episodic, semantic, or procedural)."""

    memory_type: str  # episodic | semantic | procedural
    key: str | None = None
    content: str | None = None
    embedding_model: str | None = None
    similarity_score: float | None = None
    retrieved_count: int = 0


class ConfidenceData(BaseModel):
    """Confidence and anomaly signals attached to an agent step."""

    overall_score: float = Field(ge=0.0, le=1.0)
    goal_alignment: float = Field(ge=0.0, le=1.0, default=1.0)
    consistency_score: float = Field(ge=0.0, le=1.0, default=1.0)
    anomaly_flags: list[str] = Field(default_factory=list)
    explanation: str | None = None


class ReasoningStyleFingerprint(BaseModel):
    """Per-session stylistic profile of the model's reasoning output (RSN-008).

    Captures *observable* artifacts of the reasoning style — not chain-of-thought
    contents — so it can be used to detect silent mid-session model swaps
    (e.g. a provider rolling out a new model version without changing the
    exposed model name).
    """

    mean_planner_tokens: float = 0.0
    lex_diversity: float = 0.0
    tools_per_plan: float = 0.0
    punctuation_rate: float = 0.0
    sample_size: int = 0  # number of PLANNER_OUTPUT events used

    @classmethod
    def from_dataclass(cls, other: object) -> ReasoningStyleFingerprint:
        """Adapt the dataclass form in ``panoptis.reasoning.fingerprint``.

        Returns:
            A pydantic instance mirroring the dataclass fields.
        """
        return cls(
            mean_planner_tokens=float(getattr(other, "mean_planner_tokens", 0.0)),
            lex_diversity=float(getattr(other, "lex_diversity", 0.0)),
            tools_per_plan=float(getattr(other, "tools_per_plan", 0.0)),
            punctuation_rate=float(getattr(other, "punctuation_rate", 0.0)),
            sample_size=int(getattr(other, "sample_size", 0)),
        )


class StyleSwapAlert(BaseModel):
    """Alert raised when mid-session fingerprint drift suggests a model swap.

    Distance is the Euclidean-style distance between the first-half and
    second-half fingerprints, normalized to be roughly comparable across
    sessions with different sizes.
    """

    session_id: str
    detected: bool
    distance: float
    threshold: float
    first_half: ReasoningStyleFingerprint
    second_half: ReasoningStyleFingerprint
    reason: str | None = None


class AgentMessageData(BaseModel):
    """Inter-agent message in a multi-agent workflow."""

    sender_agent_id: str
    receiver_agent_id: str
    message_type: str  # task | result | query | broadcast
    content: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


class CheckpointData(BaseModel):
    """Rollback checkpoint reference (filesystem, git, memory, or container)."""

    checkpoint_id: str
    snapshot_type: str  # filesystem | memory | git | container
    snapshot_path: str | None = None
    git_ref: str | None = None
    size_bytes: int | None = None


# ─────────────────────────────────────────────
# Universal Event
# ─────────────────────────────────────────────


class AgentEvent(BaseModel):
    """
    Universal event emitted by any PANOPTIS adapter.
    All framework-specific events are normalized to this schema.
    """

    # Identity
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    agent_id: str
    agent_name: str | None = None
    framework: AgentFramework = AgentFramework.CUSTOM
    parent_event_id: str | None = None
    trace_id: str | None = None  # OpenTelemetry trace ID

    # Classification
    event_type: EventType
    status: ExecutionStatus = ExecutionStatus.RUNNING

    # Timing
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float | None = None

    # Context
    step_number: int = 0
    iteration: int = 0
    goal: str | None = None
    task_id: str | None = None
    parent_task_id: str | None = None

    # Payload — one of these is populated per event type
    tool_call: ToolCallData | None = None
    tool_result: ToolResultData | None = None
    safety: SafetyCheckData | None = None
    memory: MemoryData | None = None
    confidence: ConfidenceData | None = None
    agent_message: AgentMessageData | None = None
    checkpoint: CheckpointData | None = None

    # Prompt/planner data (non-hidden, observable artifacts only)
    prompt_preview: str | None = None  # first 500 chars of prompt
    planner_output_preview: str | None = None  # observable planner text

    # Token / cost
    token_usage: TokenUsage | None = None

    # Extensible metadata
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    # Environment snapshot
    working_directory: str | None = None
    environment_vars_redacted: list[str] = Field(default_factory=list)

    @field_validator("timestamp", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        """Parse ISO strings and normalize timestamps to UTC-aware datetimes.

        Args:
            v: Raw timestamp value from JSON or Python.

        Returns:
            A timezone-aware datetime in UTC.
        """
        if isinstance(v, str):
            parsed = datetime.fromisoformat(v)
        elif isinstance(v, datetime):
            parsed = v
        else:
            return v

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def model_dump_for_storage(self) -> dict[str, Any]:
        """Serialize the event for persistence with ISO-8601 timestamps.

        Returns:
            JSON-compatible dict with ``timestamp`` as an ISO string.
        """
        data = self.model_dump(exclude_none=True)
        data["timestamp"] = self.timestamp.isoformat()
        return data

    @property
    def is_dangerous(self) -> bool:
        """Return True if safety metadata indicates HIGH or CRITICAL risk."""
        return bool(self.safety and self.safety.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL))

    @property
    def is_blocked(self) -> bool:
        """Return True if the event was blocked by the safety engine."""
        return self.status == ExecutionStatus.BLOCKED or (
            self.safety is not None and self.safety.blocked
        )


# ─────────────────────────────────────────────
# Session model
# ─────────────────────────────────────────────


class AgentSession(BaseModel):
    """Top-level session grouping events from one agent run."""

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    agent_name: str | None = None
    framework: AgentFramework = AgentFramework.CUSTOM
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    status: ExecutionStatus = ExecutionStatus.RUNNING
    goal: str | None = None
    total_events: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    final_confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────
# Task graph node
# ─────────────────────────────────────────────


class TaskNode(BaseModel):
    """Node in a delegated task graph within a session."""

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    parent_task_id: str | None = None
    assigned_agent_id: str | None = None
    title: str
    description: str | None = None
    status: ExecutionStatus = ExecutionStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    dependencies: list[str] = Field(default_factory=list)
    outputs: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────
# Plugin manifest
# ─────────────────────────────────────────────


class PluginPermissions(BaseModel):
    """Capability flags requested by a sandboxed plugin."""

    filesystem_read: bool = False
    filesystem_write: bool = False
    network_outbound: bool = False
    network_inbound: bool = False
    subprocess_exec: bool = False
    credential_access: bool = False
    memory_read: bool = True
    memory_write: bool = False


class PluginManifest(BaseModel):
    """Signed manifest describing a plugin and its permissions."""

    plugin_id: str
    name: str
    version: str
    author: str
    description: str
    homepage: str | None = None
    license: str = "MIT"
    permissions: PluginPermissions = Field(default_factory=PluginPermissions)
    trust_level: int = Field(ge=0, le=5, default=0)  # 0=untrusted, 5=core
    signature: str | None = None  # Ed25519 signature
    checksum_sha256: str | None = None
    min_panoptis_version: str = "0.1.0"
    tags: list[str] = Field(default_factory=list)
