"""Panoptis Memory Sentry: Sleeper Memory Poisoning & Backdoor Defense.

Defeats:
- "Hidden in Memory: Sleeper Memory Poisoning in LLM Agents" (ArXiv 2026)
- "MemPoison: Bypassing Selective Memory Mechanisms to Plant Backdoors in LLM Agents" (ArXiv 2026)
- "GhostWriter: Memory Poisoning Attacks on LLM Agents" (ArXiv 2026)

Key defenses:
1. Provenance Attestation & Context Taint Tracking on memory writes.
2. Dormant Sleeper Trigger Detection (conditional logic embedded in declarative memory).
3. Entity Masquerading & Security Constant Override Detection.
4. Quarantined & Neutralized Retrieval Formatting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from panoptis.protect.model import Severity, Verdict


class MemoryTrustLevel(StrEnum):
    """Trust classification for stored memory units."""

    AUTHENTICATED_USER = "AUTHENTICATED_USER"
    VERIFIED_TOOL_FACT = "VERIFIED_TOOL_FACT"
    UNTRUSTED_INDIRECT = "UNTRUSTED_INDIRECT"
    QUARANTINED = "QUARANTINED"


class MemoryRiskType(StrEnum):
    """Categorized memory poisoning threat types."""

    SLEEPER_TRIGGER = "SLEEPER_TRIGGER"
    ENTITY_MASQUERADE = "ENTITY_MASQUERADE"
    INSTRUCTION_INJECTION = "INSTRUCTION_INJECTION"
    AUTHORITY_USURPATION = "AUTHORITY_USURPATION"
    EXFILTRATION_SINK_PLANT = "EXFILTRATION_SINK_PLANT"


# Regex patterns for dormant future triggers embedded in declarative statements
SLEEPER_PATTERNS = [
    re.compile(
        r"\b(?:when|whenever|if)\s+(?:the\s+)?(?:user|admin|system)\s+(?:asks?|queries|requests?|triggers?)\b.+?\b(?:always|must|execute|send|forward|call)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:in\s+(?:the\s+)?next\s+session|in\s+future\s+conversations?)\b.+?\b(?:always|do|run|send|override)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:dormant|activate|trigger)\s+(?:on|upon|after)\b.+?\b(?:send|exfiltrate|delete|drop)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:secretly|silently|covertly)\s+(?:execute|send|post|write)\b", re.IGNORECASE),
]

# Patterns attempting to override system authority or entity addresses
MASQUERADE_PATTERNS = [
    re.compile(
        r"\b(?:default|trusted|official)\b.+?\b(?:endpoint|url|server|host|api|gateway)\b.+?https?://",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:auth|password|key|token)\s+(?:is|can\s+be\s+bypassed|is\s+disabled)\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(?:security\s+policy|guardrail|panoptis)\s+(?:is\s+disabled|ignore|bypass)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:grant|assume)\s+(?:root|admin|superuser)\s+privileges?\b", re.IGNORECASE),
]

# Directive imperatives that should never appear in factual memories
IMPERATIVE_PATTERNS = [
    re.compile(
        r"^(?:system\s+update|system\s+prompt|important\s+instruction|system\s+override)\s*:",
        re.IGNORECASE,
    ),
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+must\s+now\s+(?:act|behave|execute|ignore)", re.IGNORECASE),
]


@dataclass(frozen=True, slots=True)
class MemoryInspectionResult:
    """Outcome of inspecting a candidate memory write or retrieved memory item."""

    verdict: Verdict
    risk_type: MemoryRiskType | None = None
    reason: str = ""
    severity: Severity = Severity.LOW
    trust_level: MemoryTrustLevel = MemoryTrustLevel.AUTHENTICATED_USER
    sanitized_content: str | None = None


@dataclass
class MemoryRecord:
    """Authenticated memory record tracked by the Sentry."""

    memory_id: str
    content: str
    trust_level: MemoryTrustLevel
    author_id: str
    session_id: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    risk_flags: list[MemoryRiskType] = field(default_factory=list)


class MemorySentry:
    """Security governance sentry for long-term agent memory operations."""

    def __init__(self) -> None:
        self._memory_store: dict[str, MemoryRecord] = {}

    def inspect_write(
        self,
        content: str,
        author_id: str,
        session_id: str,
        is_direct_user_input: bool = True,
    ) -> MemoryInspectionResult:
        """Inspect a candidate memory before it is written to the persistent store."""
        if not content or not content.strip():
            return MemoryInspectionResult(
                verdict=Verdict.ALLOW,
                sanitized_content="",
                trust_level=MemoryTrustLevel.AUTHENTICATED_USER,
            )

        text = content.strip()

        # 1. Detect Sleeper / Dormant Conditionals
        for pat in SLEEPER_PATTERNS:
            if pat.search(text):
                return MemoryInspectionResult(
                    verdict=Verdict.BLOCK,
                    risk_type=MemoryRiskType.SLEEPER_TRIGGER,
                    reason=f"Memory contains dormant conditional trigger matching pattern: {pat.pattern}",
                    severity=Severity.CRITICAL,
                    trust_level=MemoryTrustLevel.QUARANTINED,
                )

        # 2. Detect Entity Masquerading / Authority Usurpation
        for pat in MASQUERADE_PATTERNS:
            if pat.search(text):
                return MemoryInspectionResult(
                    verdict=Verdict.BLOCK,
                    risk_type=MemoryRiskType.ENTITY_MASQUERADE,
                    reason=f"Memory attempts to redefine system entity or security baseline: {pat.pattern}",
                    severity=Severity.CRITICAL,
                    trust_level=MemoryTrustLevel.QUARANTINED,
                )

        # 3. Detect Imperative Prompt Injections
        for pat in IMPERATIVE_PATTERNS:
            if pat.search(text):
                return MemoryInspectionResult(
                    verdict=Verdict.BLOCK,
                    risk_type=MemoryRiskType.INSTRUCTION_INJECTION,
                    reason=f"Memory contains imperative instruction injection: {pat.pattern}",
                    severity=Severity.HIGH,
                    trust_level=MemoryTrustLevel.QUARANTINED,
                )

        # 4. Provenance Attribution
        trust_level = (
            MemoryTrustLevel.AUTHENTICATED_USER
            if is_direct_user_input
            else MemoryTrustLevel.UNTRUSTED_INDIRECT
        )

        return MemoryInspectionResult(
            verdict=Verdict.ALLOW,
            trust_level=trust_level,
            sanitized_content=text,
            severity=Severity.LOW,
        )

    def store_memory(
        self,
        memory_id: str,
        content: str,
        author_id: str,
        session_id: str,
        is_direct_user_input: bool = True,
    ) -> MemoryRecord:
        """Store an inspected memory record or raise ActionBlocked on poisoning."""
        inspection = self.inspect_write(
            content=content,
            author_id=author_id,
            session_id=session_id,
            is_direct_user_input=is_direct_user_input,
        )
        if inspection.verdict == Verdict.BLOCK:
            from panoptis.protect.model import ActionBlocked, Decision, Finding, Stage, ToolAction

            finding = Finding(
                stage=Stage.THREAT,
                code=f"memory_sentry:{inspection.risk_type}",
                severity=inspection.severity,
                message=inspection.reason,
                recommends=Verdict.BLOCK,
            )
            decision = Decision(
                action=ToolAction(
                    agent_id=author_id,
                    session_id=session_id,
                    tool="store_memory",
                    arguments={"content": content},
                ),
                verdict=Verdict.BLOCK,
                risk_score=1.0,
                findings=[finding],
            )
            raise ActionBlocked(decision)

        record = MemoryRecord(
            memory_id=memory_id,
            content=inspection.sanitized_content or content,
            trust_level=inspection.trust_level,
            author_id=author_id,
            session_id=session_id,
        )
        self._memory_store[memory_id] = record
        return record

    def inspect_retrieval(
        self,
        query: str,
        retrieved_memories: list[MemoryRecord | str],
    ) -> list[str]:
        """Sanitize and format retrieved memories, neutralizing any indirect imperative vectors."""
        sanitized_outputs: list[str] = []

        for item in retrieved_memories:
            text = item.content if isinstance(item, MemoryRecord) else str(item)
            is_untrusted = (
                isinstance(item, MemoryRecord)
                and item.trust_level == MemoryTrustLevel.UNTRUSTED_INDIRECT
            )

            # Strip any residual imperative prefixes
            clean_text = text
            for pat in IMPERATIVE_PATTERNS:
                clean_text = pat.sub("", clean_text).strip()

            if is_untrusted:
                # Wrap indirect untrusted memories in passive factual tags to prevent execution
                sanitized_outputs.append(f"[UNTRUSTED_OBSERVATION: {clean_text}]")
            else:
                sanitized_outputs.append(clean_text)

        return sanitized_outputs
