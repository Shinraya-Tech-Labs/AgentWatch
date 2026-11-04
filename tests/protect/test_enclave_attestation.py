from __future__ import annotations

import time

from panoptis.protect.enclave_attestation import (
    EnclaveAttestationVerifier,
    EnclavePlatform,
    EnclaveVerificationConfig,
)
from panoptis.protect.model import Verdict


def test_enclave_attestation_success():
    golden_pcr0 = "a" * 64
    golden_pcr1 = "b" * 64
    golden_pcr2 = "c" * 64
    nonce = "session_challenge_42"

    config = EnclaveVerificationConfig(
        golden_pcr0=[golden_pcr0],
        golden_pcr1=[golden_pcr1],
        golden_pcr2=[golden_pcr2],
    )
    verifier = EnclaveAttestationVerifier(config)

    doc = verifier.mint_mock_document(
        pcr0=golden_pcr0,
        pcr1=golden_pcr1,
        pcr2=golden_pcr2,
        nonce=nonce,
        platform=EnclavePlatform.AWS_NITRO,
    )

    res = verifier.verify_attestation(doc, expected_nonce=nonce)
    assert res.is_valid
    assert res.pcr_matches
    assert res.nonce_matches
    assert res.finding is None


def test_enclave_nonce_mismatch_fails():
    verifier = EnclaveAttestationVerifier()
    doc = verifier.mint_mock_document(nonce="old_challenge_99")

    res = verifier.verify_attestation(doc, expected_nonce="expected_new_challenge")
    assert not res.is_valid
    assert not res.nonce_matches
    assert res.finding is not None
    assert res.finding.code == "ENCLAVE_NONCE_MISMATCH"
    assert res.finding.recommends == Verdict.BLOCK


def test_enclave_pcr_mismatch_fails():
    config = EnclaveVerificationConfig(
        golden_pcr0=["11" * 32],
        golden_pcr2=["22" * 32],
    )
    verifier = EnclaveAttestationVerifier(config)

    # doc with untrusted altered application code in PCR2
    doc = verifier.mint_mock_document(
        pcr0="11" * 32,
        pcr2="33" * 32,  # Tampered application code hash!
        nonce="challenge",
    )

    res = verifier.verify_attestation(doc, expected_nonce="challenge")
    assert not res.is_valid
    assert not res.pcr_matches
    assert res.finding is not None
    assert res.finding.code == "ENCLAVE_PCR_MEASUREMENT_MISMATCH"
    assert res.finding.recommends == Verdict.BLOCK


def test_enclave_expired_attestation_fails():
    config = EnclaveVerificationConfig(max_attestation_age_seconds=60.0)
    verifier = EnclaveAttestationVerifier(config)

    doc = verifier.mint_mock_document()
    doc.timestamp = time.time() - 3600.0  # 1 hour ago

    res = verifier.verify_attestation(doc)
    assert not res.is_valid
    assert res.finding is not None
    assert res.finding.code == "ENCLAVE_ATTESTATION_EXPIRED"


def test_enclave_missing_signature_fails():
    verifier = EnclaveAttestationVerifier()
    doc = verifier.mint_mock_document()
    doc.signature = ""

    res = verifier.verify_attestation(doc)
    assert not res.is_valid
    assert res.finding is not None
    assert res.finding.code == "ENCLAVE_MISSING_SIGNATURE"


def test_enclave_emulator_rejected_by_default():
    """An emulator document is not hardware attestation; it must be opt-in."""
    verifier = EnclaveAttestationVerifier()
    doc = verifier.mint_mock_document(platform=EnclavePlatform.HARDWARE_EMULATOR)

    res = verifier.verify_attestation(doc)
    assert not res.is_valid
    assert res.finding is not None
    assert res.finding.code == "ENCLAVE_EMULATOR_DISALLOWED"


def test_enclave_verify_does_not_claim_signature_verification():
    """Known limitation, asserted so nobody mistakes this for real attestation.

    No signature is validated - any non-empty string passes - and the success
    reason must say so.
    """
    verifier = EnclaveAttestationVerifier()
    doc = verifier.mint_mock_document()
    doc.signature = "not-a-real-signature"

    res = verifier.verify_attestation(doc)
    assert res.is_valid
    assert "NOT verified" in res.reason
