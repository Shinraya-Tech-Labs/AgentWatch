"""Panoptis RAG Sanitizer: Retrieval-Augmented Generation Poisoning Defense.

Defeats:
- "PoisonedRAG: Knowledge Corruption Attacks to Retrieval-Augmented Generation" (USENIX Security 2024/2025)

Key defenses:
1. Imperative Command Neutralization in retrieved passages.
2. System Role & Delimiter Injection Stripping.
3. Zero-Width Unicode & Exfiltration Markdown Sanitization.
4. Imperative-to-Factual Entropy Ratio Anomaly Detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from panoptis.protect.model import Verdict


class RAGPoisonStatus(StrEnum):
    """Integrity status of retrieved context chunk."""

    CLEAN = "CLEAN"
    SANITIZED_MUTATED = "SANITIZED_MUTATED"
    POISON_DETECTED_BLOCKED = "POISON_DETECTED_BLOCKED"


# System delimiter injections intended to hijack LLM context framing
SYSTEM_DELIMITER_PATTERNS = [
    re.compile(r"<\s*/?\s*(?:system|assistant|human|user|inst|instruction)\s*>", re.IGNORECASE),
    re.compile(r"\[\s*(?:system|assistant|admin|override)\s*\]", re.IGNORECASE),
    re.compile(r"\{\{\s*(?:system|instructions?)\s*\}\}", re.IGNORECASE),
    re.compile(r"###\s*(?:System|Instructions?|Override):?", re.IGNORECASE),
]

# Direct imperative overrides in retrieved reference documents
IMPERATIVE_COMMAND_PATTERNS = [
    (
        re.compile(
            r"\b(?:you\s+must|you\s+are\s+required\s+to|please)(?:\s+immediately|\s+urgently|\s+now)?\s+(?:execute|run|call|download|fetch|curl|delete|wipe)\b",
            re.IGNORECASE,
        ),
        "the document mentions executing ",
    ),
    (
        re.compile(r"\bignore\s+(?:all\s+)?previous\s+instructions\b", re.IGNORECASE),
        "[disregarded instruction]",
    ),
    (
        re.compile(r"\bdisregard\s+(?:all\s+)?prior\s+directives\b", re.IGNORECASE),
        "[disregarded directive]",
    ),
    (
        re.compile(
            r"\b(?:send|exfiltrate|post)\s+(?:all\s+)?(?:secrets?|tokens?|passwords?|data)\s+to\b",
            re.IGNORECASE,
        ),
        "the document refers to transmitting data to ",
    ),
]

# Covert markdown exfiltration patterns
MARKDOWN_EXFIL_PATTERN = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+\?[^)\s]*)\)")
ZERO_WIDTH_PATTERN = re.compile(r"[\u200B-\u200D\uFEFF\u202A-\u202E]")


@dataclass(frozen=True, slots=True)
class RAGSanitizationResult:
    """Result of inspecting and sanitizing a retrieved RAG document chunk."""

    status: RAGPoisonStatus
    verdict: Verdict
    original_text: str
    sanitized_text: str
    detected_injections: list[str]
    imperative_entropy_ratio: float


class RAGSanitizer:
    """Sanitizes context retrieved from external vector databases, search, or document stores."""

    def __init__(self, max_imperative_ratio: float = 0.35) -> None:
        self.max_imperative_ratio = max_imperative_ratio

    def _compute_imperative_ratio(self, text: str) -> float:
        """Estimate the ratio of directive command verbs to total tokens."""
        tokens = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
        if not tokens:
            return 0.0

        command_verbs = {
            "run",
            "execute",
            "call",
            "send",
            "post",
            "delete",
            "drop",
            "wipe",
            "ignore",
            "bypass",
            "fetch",
            "curl",
            "override",
            "must",
            "always",
            "forward",
        }
        cmd_count = sum(1 for t in tokens if t in command_verbs)
        return cmd_count / len(tokens)

    def sanitize_chunk(self, chunk_text: str, source_id: str = "") -> RAGSanitizationResult:
        """Sanitize an individual retrieved text chunk before passing it into LLM prompt."""
        if not chunk_text or not chunk_text.strip():
            return RAGSanitizationResult(
                status=RAGPoisonStatus.CLEAN,
                verdict=Verdict.ALLOW,
                original_text="",
                sanitized_text="",
                detected_injections=[],
                imperative_entropy_ratio=0.0,
            )

        text = chunk_text
        detected: list[str] = []

        # 1. Strip zero-width covert steganography
        if ZERO_WIDTH_PATTERN.search(text):
            detected.append("ZERO_WIDTH_STEGANOGRAPHY")
            text = ZERO_WIDTH_PATTERN.sub("", text)

        # 2. Defuse markdown image exfiltration URLs
        if MARKDOWN_EXFIL_PATTERN.search(text):
            detected.append("MARKDOWN_IMAGE_EXFILTRATION")
            text = MARKDOWN_EXFIL_PATTERN.sub("[EXFILTRATION_IMAGE_DEFUSED]", text)

        # 3. Strip System Delimiter Spoofing
        for pat in SYSTEM_DELIMITER_PATTERNS:
            if pat.search(text):
                detected.append(f"DELIMITER_SPOOFING:{pat.pattern}")
                text = pat.sub("[STRIPPED_SYSTEM_TAG]", text)

        # 4. Neutralize Imperative Directive Injections into Passive Quotes
        for pat, replacement in IMPERATIVE_COMMAND_PATTERNS:
            if pat.search(text):
                detected.append(f"IMPERATIVE_COMMAND_MUTATED:{pat.pattern}")
                text = pat.sub(replacement, text)

        # 5. Measure Imperative Entropy Ratio
        ratio = self._compute_imperative_ratio(text)
        if ratio > self.max_imperative_ratio:
            detected.append(f"HIGH_IMPERATIVE_ENTROPY:{ratio:.2f}")
            # If the chunk is almost entirely imperative commands rather than knowledge, block it
            return RAGSanitizationResult(
                status=RAGPoisonStatus.POISON_DETECTED_BLOCKED,
                verdict=Verdict.BLOCK,
                original_text=chunk_text,
                sanitized_text="",
                detected_injections=detected,
                imperative_entropy_ratio=ratio,
            )

        status = RAGPoisonStatus.SANITIZED_MUTATED if detected else RAGPoisonStatus.CLEAN
        return RAGSanitizationResult(
            status=status,
            verdict=Verdict.ALLOW,
            original_text=chunk_text,
            sanitized_text=text,
            detected_injections=detected,
            imperative_entropy_ratio=ratio,
        )

    def sanitize_retrieved_documents(
        self,
        documents: list[str],
    ) -> list[str]:
        """Batch-process retrieved documents, stripping blocked or poisoned chunks."""
        clean_docs: list[str] = []
        for doc in documents:
            result = self.sanitize_chunk(doc)
            if result.verdict == Verdict.ALLOW:
                clean_docs.append(result.sanitized_text)
        return clean_docs
