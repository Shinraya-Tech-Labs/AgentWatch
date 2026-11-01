"""
CMP-001 — GDPR Data Handling.

- PII detection across all traces
- Auto-redaction option
- Right-to-erasure endpoint (in-memory + persisted, HMAC-SHA256 receipts)
- Cross-session memory erasure (CMP-002) — see :class:`CrossSessionErasureService`
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

SIGNING_KEY_ENV = "AGENTWATCH_GDPR_SIGNING_KEY"
SIGNATURE_PREFIX = "hmac-sha256:"

# Pattern → label
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "email"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "ssn"),
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "credit_card"),
    (
        re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "phone",
    ),
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "ip_address"),
    (re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"), "aws_access_key"),
]


def pii_patterns() -> list[tuple[re.Pattern[str], str]]:
    """Public accessor for the compiled (pattern, label) PII detectors."""
    return list(_PII_PATTERNS)


@dataclass
class PIIFinding:
    label: str
    excerpt: str


@dataclass
class RedactionResult:
    redacted_text: str
    findings: list[PIIFinding] = field(default_factory=list)


def detect_pii(text: str) -> list[PIIFinding]:
    findings: list[PIIFinding] = []
    if not text:
        return findings
    for pat, label in _PII_PATTERNS:
        for m in pat.finditer(text):
            findings.append(PIIFinding(label=label, excerpt=m.group()[:60]))
    return findings


def redact(text: str) -> RedactionResult:
    findings: list[PIIFinding] = []
    out = text or ""
    for pat, label in _PII_PATTERNS:
        for m in pat.finditer(out):
            findings.append(PIIFinding(label=label, excerpt=m.group()[:60]))
        out = pat.sub(f"[REDACTED:{label.upper()}]", out)
    return RedactionResult(redacted_text=out, findings=findings)


class ErasureScope(str, Enum):
    """Valid scope types for a right-to-erasure request."""

    USER_ID = "user_id"
    AGENT_ID = "agent_id"
    SESSION_ID = "session_id"
    TENANT_ID = "tenant_id"


@dataclass
class ErasureReceipt:
    user_id: str
    submitted_at: datetime
    completed_at: datetime
    items_erased: int
    scope: str
    audit_signature: str = ""
    failure_count: int = 0
    failed_targets: list[str] = field(default_factory=list)


@runtime_checkable
class ErasureStore(Protocol):
    """Persistence-layer contract for GDPR Article 17 erasure."""

    async def erase_user_data(self, user_id: str, *, scope: str = "all") -> int: ...


@dataclass
class ErasureRequest:
    """Structured right-to-erasure request suitable for API input.

    ``identifier`` must be a non-empty, non-whitespace string. Empty or
    whitespace-only values are rejected at construction time so a confused
    API caller cannot trigger a global erase with no scope.
    """

    identifier: str
    scope: ErasureScope = ErasureScope.USER_ID
    reason: str = "right_to_erasure"
    initiated_by: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not self.identifier.strip():
            raise ValueError(
                "ErasureRequest.identifier must be a non-empty, non-whitespace string."
            )
        self.identifier = self.identifier.strip()


@dataclass
class ErasureTargetResult:
    """Per-target result of an erasure operation."""

    target_name: str
    total_items: int
    erased_items: int
    error: str | None = None


def _sign_receipt(
    request: ErasureRequest,
    results: list[ErasureTargetResult],
    secret: bytes,
) -> str:
    """Generate an HMAC-SHA256 erasure receipt signature.

    The signature covers the request identity, the per-target erased counts,
    and errors so the receipt cannot be forged or replayed across runs.
    """
    return _sign_erasure_payload(_erasure_payload(request, results), secret)


def _erasure_payload(
    request: ErasureRequest,
    results: list[ErasureTargetResult],
) -> bytes:
    """Stable wire-format payload signed for an erasure receipt."""
    total_erased = sum(r.erased_items for r in results)
    return json.dumps(
        {
            "identifier": request.identifier,
            "scope": request.scope.value,
            "total_erased": total_erased,
            "results": [(r.target_name, r.erased_items, r.error) for r in results],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sign_erasure_payload(payload: bytes, secret: bytes) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_erasure_signature(
    receipt: ErasureReceipt,
    request: ErasureRequest,
    results: list[ErasureTargetResult],
    *,
    key: str | bytes,
) -> bool:
    """Verify the HMAC-SHA256 signature on an ErasureReceipt.

    Recomputes the same per-target payload as :func:`_sign_receipt` and
    compares against ``receipt.audit_signature`` using a constant-time
    digest check. Use this in tests and compliance tooling that needs to
    detect tampered receipts without rebuilding the receipt payload by hand.

    Args:
        receipt: The signed ErasureReceipt returned by the service.
        request: The same ErasureRequest that was originally submitted.
        results: The same list of per-target ErasureTargetResult entries
            produced by the service for the request.
        key: The signing secret (string or bytes) used at receipt issuance.

    Returns:
        ``True`` iff the receipt signature matches the recomputed payload.
    """
    resolved = key.encode("utf-8") if isinstance(key, str) else key
    expected = _sign_erasure_payload(_erasure_payload(request, results), resolved)
    return hmac.compare_digest(expected, receipt.audit_signature)


class ErasureTarget(Protocol):
    """Protocol for a storage backend that supports entity erasure.

    Implementations should be stateless (the service holds the coordination
    state) and handle their own database transactions.
    """

    name: str

    async def count_matching(self, identifier: str, scope: ErasureScope) -> int:
        """Return the number of records matching the identifier for the given scope."""

    async def erase_matching(self, identifier: str, scope: ErasureScope) -> int:
        """Delete or anonymise all matching records and return the count."""


def resolve_signing_key(explicit: str | bytes | None = None) -> bytes:
    """Return the HMAC key from an explicit value or SIGNING_KEY_ENV, else raise."""
    if explicit is not None:
        key = explicit.encode("utf-8") if isinstance(explicit, str) else explicit
        if key.strip():
            return key
    else:
        from_env = os.getenv(SIGNING_KEY_ENV)
        if from_env:
            key = from_env.encode("utf-8")
            if key.strip():
                return key

    raise RuntimeError(
        f"No GDPR receipt signing key configured. Set {SIGNING_KEY_ENV} "
        "or pass signing_key=... to GDPREngine."
    )


def _receipt_payload(receipt: ErasureReceipt) -> bytes:
    # Deterministic across processes; excludes the signature itself.
    return json.dumps(
        {
            "user_id": receipt.user_id,
            "submitted_at": receipt.submitted_at.isoformat(),
            "completed_at": receipt.completed_at.isoformat(),
            "items_erased": receipt.items_erased,
            "scope": receipt.scope,
            "failure_count": receipt.failure_count,
            "failed_targets": list(receipt.failed_targets),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_receipt(receipt: ErasureReceipt, *, key: str | bytes) -> str:
    """Return the hmac-sha256:<hex> signature for a receipt."""
    resolved = key.encode("utf-8") if isinstance(key, str) else key
    digest = hmac.new(resolved, _receipt_payload(receipt), hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify_receipt(receipt: ErasureReceipt, *, key: str | bytes) -> bool:
    """Return whether receipt.audit_signature matches a fresh HMAC (constant-time)."""
    return hmac.compare_digest(sign_receipt(receipt, key=key), receipt.audit_signature)


class GDPREngine:
    """GDPR handler over an in-memory list or a persistent store."""

    def __init__(self, *, signing_key: str | bytes | None = None) -> None:
        # Resolve eagerly so misconfiguration fails at startup, not at erase time.
        self._signing_key = resolve_signing_key(signing_key)

    def scan_records(self, records: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in records:
            blob = " ".join(str(v) for v in r.values() if isinstance(v, (str, int, float)))
            for f in detect_pii(blob):
                counts[f.label] = counts.get(f.label, 0) + 1
        return counts

    def _build_receipt(
        self, user_id: str, submitted: datetime, erased: int, scope: str
    ) -> ErasureReceipt:
        receipt = ErasureReceipt(
            user_id=user_id,
            submitted_at=submitted,
            completed_at=datetime.now(UTC),
            items_erased=erased,
            scope=scope,
        )
        receipt.audit_signature = sign_receipt(receipt, key=self._signing_key)
        return receipt

    def erase(
        self, user_id: str, records: list[dict[str, Any]], *, scope: str = "all"
    ) -> tuple[list[dict[str, Any]], ErasureReceipt]:
        """Erase a subject's rows from an in-memory record list."""
        submitted = datetime.now(UTC)
        kept = [r for r in records if r.get("user_id") != user_id]
        erased = len(records) - len(kept)
        return kept, self._build_receipt(user_id, submitted, erased, scope)

    async def erase_persisted(
        self, store: ErasureStore, user_id: str, *, scope: str = "all"
    ) -> ErasureReceipt:
        """Erase a subject's data through the persistence layer and sign a receipt."""
        submitted = datetime.now(UTC)
        erased = await store.erase_user_data(user_id, scope=scope)
        return self._build_receipt(user_id, submitted, erased, scope)


class CrossSessionErasureService:
    """Orchestrates right-to-erasure actions across multiple storage backends.

    This is the high-level entry point for GDPR ``/api/v1/gdpr/erase``.
    It coordinates erasure sessions, events, and memory entries tied to a
    particular identifier (user_id, agent_id, session_id, or tenant_id),
    generates an HMAC-signed receipt, and persists the audit log.

    Usage::

        service = CrossSessionErasureService(
            targets=[session_target, event_target, memory_target],
            signing_secret=b"<32-byte-key>",
        )
        receipt = await service.erase(ErasureRequest(identifier="user_abc123"))
    """

    def __init__(
        self,
        targets: Iterable[ErasureTarget],
        signing_secret: bytes = b"",
        min_signing_secret_bytes: int = 16,
    ):
        if not signing_secret:
            raise ValueError(
                "CrossSessionErasureService requires a non-empty signing_secret. "
                "Random/zero fallback is unsafe - pass a real key."
            )
        if len(signing_secret) < min_signing_secret_bytes:
            raise ValueError(
                f"CrossSessionErasureService signing_secret is too short "
                f"({len(signing_secret)} bytes); expected at least "
                f"{min_signing_secret_bytes} bytes so the HMAC key has enough entropy."
            )
        self._targets = list(targets)
        self._secret = signing_secret

    async def erase(self, request: ErasureRequest) -> ErasureReceipt:
        """Execute the erasure across all registered targets.

        Args:
            request: The structured erasure request.

        Returns:
            A signed :class:`ErasureReceipt` with per-target counts. Receipt
            carries ``failure_count``/``failed_targets`` when targets error.
        """
        submitted = datetime.now(UTC)
        results: list[ErasureTargetResult] = []

        for target in self._targets:
            try:
                count = await target.count_matching(request.identifier, request.scope)
                erased = await target.erase_matching(request.identifier, request.scope)
                results.append(
                    ErasureTargetResult(
                        target_name=target.name,
                        total_items=count,
                        erased_items=erased,
                    )
                )
            except Exception as exc:
                results.append(
                    ErasureTargetResult(
                        target_name=target.name,
                        total_items=-1,
                        erased_items=0,
                        error=str(exc),
                    )
                )

        completed = datetime.now(UTC)
        total_erased = sum(r.erased_items for r in results)
        failed = [r.target_name for r in results if r.error is not None]
        signature = _sign_receipt(request, results, self._secret)

        return ErasureReceipt(
            user_id=request.identifier,
            submitted_at=submitted,
            completed_at=completed,
            items_erased=total_erased,
            scope=request.scope.value,
            audit_signature=signature,
            failure_count=len(failed),
            failed_targets=list(failed),
        )

    @property
    def targets(self) -> list[ErasureTarget]:
        return list(self._targets)


__all__ = [
    "SIGNING_KEY_ENV",
    "SIGNATURE_PREFIX",
    "PIIFinding",
    "RedactionResult",
    "ErasureScope",
    "ErasureRequest",
    "ErasureReceipt",
    "ErasureStore",
    "ErasureTargetResult",
    "ErasureTarget",
    "GDPREngine",
    "CrossSessionErasureService",
    "detect_pii",
    "pii_patterns",
    "redact",
    "resolve_signing_key",
    "sign_receipt",
    "verify_receipt",
    "verify_erasure_signature",
]
