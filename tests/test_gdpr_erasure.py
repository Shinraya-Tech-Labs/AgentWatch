"""Tests for GDPR cross-session erasure (CMP-002).

Covers:
- ErasureRequest / ErasureReceipt / ErasureScope schema models.
- CrossSessionErasureService orchestration with mock targets.
- Receipt HMAC signature integrity verified via verify_erasure_signature.
- Fail-fast on empty / too-short signing_secret.
- Per-target failure surfacing through ErasureReceipt.failure_count / failed_targets.
- Identifier validation rejects empty / whitespace strings.
"""

from __future__ import annotations

import pytest

from panoptis.governance.gdpr import (
    CrossSessionErasureService,
    ErasureReceipt,
    ErasureRequest,
    ErasureScope,
    ErasureTarget,
    ErasureTargetResult,
    verify_erasure_signature,
)


class _MockTarget:
    """In-memory ErasureTarget for testing the orchestration layer."""

    name = "mock_target"

    def __init__(self, records: list[str] | None = None, *, fail: str | None = None):
        self._records = list(records or [])
        self._fail_message = fail

    async def count_matching(self, identifier: str, scope: ErasureScope) -> int:
        return sum(1 for r in self._records if identifier in r)

    async def erase_matching(self, identifier: str, scope: ErasureScope) -> int:
        if self._fail_message is not None:
            raise RuntimeError(self._fail_message)
        before = len(self._records)
        self._records = [r for r in self._records if identifier not in r]
        return before - len(self._records)


_TEST_SECRET = b"test-signing-secret-32-bytes!"


def _make_service(
    targets: list[ErasureTarget],
    *,
    secret: bytes = _TEST_SECRET,
) -> CrossSessionErasureService:
    return CrossSessionErasureService(targets=targets, signing_secret=secret)


def test_erasure_request_scope_defaults():
    request = ErasureRequest(identifier="user_abc")
    assert request.scope == ErasureScope.USER_ID
    assert request.reason == "right_to_erasure"


@pytest.mark.parametrize(
    "bad_identifier",
    ["", "   ", "\t", "\n"],
)
def test_erasure_request_rejects_empty_or_whitespace_identifier(bad_identifier):
    with pytest.raises(ValueError, match="non-empty, non-whitespace string"):
        ErasureRequest(identifier=bad_identifier)


def test_erasure_request_strips_surrounding_whitespace():
    request = ErasureRequest(identifier="  user_abc  ")
    assert request.identifier == "user_abc"


async def test_single_target_erase():
    target = _MockTarget(["user_abc_event_1", "user_xyz_event_2"])
    service = _make_service([target])
    receipt = await service.erase(ErasureRequest(identifier="user_abc"))
    assert isinstance(receipt, ErasureReceipt)
    assert receipt.items_erased == 1


async def test_receipt_signature_verifies_via_public_helper():
    """Receipt signature is verifiable through verify_erasure_signature."""
    target = _MockTarget(["user_abc_event"])
    service = _make_service([target])
    request = ErasureRequest(identifier="user_abc")
    receipt = await service.erase(request)
    results = [
        ErasureTargetResult(
            target_name=target.name,
            total_items=0,
            erased_items=receipt.items_erased,
        )
    ]
    assert verify_erasure_signature(
        receipt,
        request,
        results,
        key=_TEST_SECRET,
    )


async def test_receipt_signature_detects_request_tampering():
    """A request different from the one the receipt was signed for invalidates the HMAC."""
    target = _MockTarget(["user_abc_event"])
    service = _make_service([target])
    signed_request = ErasureRequest(identifier="user_abc")
    receipt = await service.erase(signed_request)
    results = [
        ErasureTargetResult(
            target_name=target.name,
            total_items=0,
            erased_items=receipt.items_erased,
        )
    ]
    tampered_request = ErasureRequest(identifier="user_XYZ")
    assert not verify_erasure_signature(
        receipt,
        tampered_request,
        results,
        key=_TEST_SECRET,
    )


async def test_receipt_signature_detects_result_tampering():
    """Mutating the post-target outcome list invalidates the HMAC."""
    target = _MockTarget(["user_abc_event"])
    service = _make_service([target])
    request = ErasureRequest(identifier="user_abc")
    receipt = await service.erase(request)
    forged_results = [
        ErasureTargetResult(
            target_name=target.name,
            total_items=0,
            erased_items=999,
        )
    ]
    assert not verify_erasure_signature(
        receipt,
        request,
        forged_results,
        key=_TEST_SECRET,
    )


async def test_signature_uses_all_payload_fields():
    """Distinct (identifier, scope) produce distinct signatures."""
    service = _make_service([_MockTarget(["x"])])
    r1 = await service.erase(ErasureRequest(identifier="user_a"))
    r2 = await service.erase(ErasureRequest(identifier="user_b"))
    assert r1.audit_signature != r2.audit_signature


async def test_receipt_signature_fails_under_wrong_key():
    """Signing secret mismatch is detected as verification failure."""
    service = _make_service([_MockTarget(["user_abc_event"])])
    request = ErasureRequest(identifier="user_abc")
    receipt = await service.erase(request)
    results = [
        ErasureTargetResult(
            target_name="mock_target",
            total_items=0,
            erased_items=receipt.items_erased,
        )
    ]
    assert not verify_erasure_signature(
        receipt,
        request,
        results,
        key=b"a-completely-different-secret",
    )


async def test_multiple_targets():
    t1 = _MockTarget(["user_abc_data_1"])
    t2 = _MockTarget(["user_abc_data_2"])
    service = _make_service([t1, t2])
    receipt = await service.erase(ErasureRequest(identifier="user_abc"))
    assert receipt.items_erased == 2


async def test_target_error_produces_result_with_error():
    """Per-target failure surfaces in receipt.failure_count + failed_targets.

    The endpoint can return HTTP 207 with this same ``ErasureReceipt``
    payload (see api/server.py gdpr_erase) so the test verifies the
    failure-surfacing contract callers depend on for partial-failure
    detection.
    """
    failing = _MockTarget(["user_abc_data"], fail="database connection lost")
    ok = _MockTarget(["user_abc_data"])
    service = _make_service([failing, ok])
    receipt = await service.erase(ErasureRequest(identifier="user_abc"))
    assert receipt.items_erased == 1
    assert receipt.failure_count == 1
    assert receipt.failed_targets == ["mock_target"]
    assert receipt.audit_signature


async def test_successful_erasure_records_zero_failures():
    target = _MockTarget(["user_abc_event", "user_abc_event_2"])
    service = _make_service([target])
    receipt = await service.erase(ErasureRequest(identifier="user_abc"))
    assert receipt.failure_count == 0
    assert receipt.failed_targets == []
    assert receipt.audit_signature


async def test_session_id_scope():
    target = _MockTarget(["user_abc_session"])
    service = _make_service([target])
    receipt = await service.erase(
        ErasureRequest(identifier="user_abc", scope=ErasureScope.SESSION_ID)
    )
    assert receipt.scope == "session_id"


async def test_empty_targets():
    service = _make_service([])
    receipt = await service.erase(ErasureRequest(identifier="user_nonexistent"))
    assert receipt.items_erased == 0
    assert receipt.failure_count == 0


def test_targets_property():
    t = _MockTarget()
    service = _make_service([t])
    assert len(service.targets) == 1
    assert service.targets[0] is t


def test_empty_signing_secret_raises():
    """Empty signing_secret is rejected at construction time (fail-fast)."""
    with pytest.raises(ValueError, match="non-empty signing_secret"):
        CrossSessionErasureService(targets=[], signing_secret=b"")


def test_too_short_signing_secret_raises():
    """Signatures under 16 bytes are rejected because the HMAC key lacks entropy."""
    with pytest.raises(ValueError, match="too short"):
        CrossSessionErasureService(targets=[], signing_secret=b"short")


def test_min_length_signing_secret_accepted():
    """A 16-byte secret is the documented minimum and is accepted."""
    CrossSessionErasureService(targets=[], signing_secret=b"x" * 16)
