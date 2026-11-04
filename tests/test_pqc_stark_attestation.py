"""Tests for the simulated PQC / STARK attestation modules.

These cover the *reference* implementation in ``panoptis.protect.pqc_attestation``:
a 4-dimensional toy lattice scheme and a hash-chain "proof". They check structural
consistency and tamper detection only. They do not, and cannot, establish that any
post-quantum or zero-knowledge property holds - see that module's docstring.
"""

from __future__ import annotations

import time

from panoptis.protect.enclave_attestation import (
    EnclaveAttestationVerifier,
    EnclavePlatform,
    EnclaveVerificationConfig,
)
from panoptis.protect.model import Decision, ToolAction, Verdict
from panoptis.protect.pqc_attestation import (
    PQCAlgorithm,
    PQCSigner,
    SovereignAttestationEngine,
    StarkProof,
    StarkRecursiveProver,
    StarkRecursiveVerifier,
)


def _make_dummy_decision(tool: str, allowed: bool = True, risk: float = 0.1) -> Decision:
    action = ToolAction(
        agent_id="sovereign-agent",
        session_id="session-pqc-01",
        tool=tool,
        arguments={"arg": "val"},
    )
    return Decision(
        action=action,
        verdict=Verdict.ALLOW if allowed else Verdict.BLOCK,
        risk_score=risk,
    )


def test_pqc_ml_dsa_signing_and_verification() -> None:
    """The toy lattice signature round-trips and detects tampering (not ML-DSA)."""
    signer = PQCSigner(algorithm=PQCAlgorithm.ML_DSA_65)
    message = b"PANOPTIS-TOP-0.001%-SOVEREIGN-AUDIT-ROOT"

    sig = signer.sign(message)
    assert sig.startswith("ML-DSA-65$")

    # Positive verification
    is_valid = PQCSigner.verify(signer.keypair.public_key_hex, message, sig)
    assert is_valid is True

    # Tampered message must fail
    tampered_msg = b"PANOPTIS-CORRUPTED-AUDIT-ROOT"
    assert PQCSigner.verify(signer.keypair.public_key_hex, tampered_msg, sig) is False

    # Tampered signature must fail
    tampered_sig = sig[:-4] + "0000"
    assert PQCSigner.verify(signer.keypair.public_key_hex, message, tampered_sig) is False


def test_stark_proof_generation_and_sub_5ms_verification() -> None:
    """The hash-chain transcript verifies quickly (not a STARK, not a soundness proof)."""
    signer = PQCSigner(algorithm=PQCAlgorithm.ML_DSA_44)
    prover = StarkRecursiveProver(signer)
    verifier = StarkRecursiveVerifier()

    decisions = [
        _make_dummy_decision("read_file", allowed=True, risk=0.05),
        _make_dummy_decision("write_file", allowed=True, risk=0.20),
        _make_dummy_decision("query_db", allowed=True, risk=0.15),
        _make_dummy_decision("http_post", allowed=False, risk=0.95),
    ]

    policy_root = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    enclave_pcr2 = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"

    proof: StarkProof = prover.prove_execution_trace(
        decisions=decisions,
        policy_root=policy_root,
        enclave_pcr2=enclave_pcr2,
    )

    assert proof.trace_length == 4
    assert len(proof.fri_commitments) == 3
    assert proof.enclave_pcr2 == enclave_pcr2

    # Benchmark verification latency (< 5ms SLA)
    t0 = time.perf_counter()
    ok, reason = verifier.verify_proof(proof, public_key_pqc=signer.keypair.public_key_hex)
    verify_ms = (time.perf_counter() - t0) * 1000.0

    assert ok is True
    assert verify_ms < 5.0, f"STARK verification took {verify_ms:.2f}ms, exceeding 5ms SLA"


def test_stark_proof_fri_tamper_detection() -> None:
    """Corrupting a reduction round is rejected by the structural check."""
    signer = PQCSigner()
    prover = StarkRecursiveProver(signer)
    verifier = StarkRecursiveVerifier()

    decisions = [_make_dummy_decision("read_file", allowed=True, risk=0.1)]
    proof = prover.prove_execution_trace(decisions, "policy-hash", "pcr2-hash")

    # Corrupt FRI commitment round 1
    proof.fri_commitments[
        1
    ].merkle_root = "badbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadb"
    ok, reason = verifier.verify_proof(proof)
    assert ok is False
    assert "FRI round 1 commitment mismatch" in reason


def test_sovereign_attestation_engine_end_to_end() -> None:
    """The three simulated artifacts are checked against each other (no trust implied)."""
    enclave_doc = EnclaveAttestationVerifier.mint_mock_document(
        platform=EnclavePlatform.AWS_NITRO,
        nonce="challenge-nonce-001",
    )
    pcr2_val = enclave_doc.pcr2

    config = EnclaveVerificationConfig(
        golden_pcr2=[pcr2_val],
        allow_emulator=True,
    )
    engine = SovereignAttestationEngine(enclave_config=config)

    decisions = [
        _make_dummy_decision("auth_login", allowed=True, risk=0.01),
        _make_dummy_decision("process_order", allowed=True, risk=0.10),
    ]

    cert = engine.generate_sovereign_certificate(
        decisions=decisions,
        enclave_doc=enclave_doc,
        policy_root="verified-sovereign-policy-root-hash",
    )

    # 1. Consistent artifacts "verify" - and the message must not claim otherwise
    valid, msg = engine.verify_sovereign_certificate(cert, expected_nonce="challenge-nonce-001")
    assert valid is True
    assert "Simulated verification succeeded" in msg
    assert "NOT hardware or post-quantum verification" in msg

    # 2. Replay protection: mismatched nonce fails
    invalid_nonce, msg_nonce = engine.verify_sovereign_certificate(
        cert, expected_nonce="stale-challenge-nonce"
    )
    assert invalid_nonce is False
    assert "Nonce mismatch" in msg_nonce

    # 3. PCR binding tamper detection: modifying enclave PCR2 without updating STARK proof fails
    cert.enclave_document.pcr2 = "compromised-kernel-hash-ffffffffffffffffffffffffffffff"
    mismatch_pcr, msg_pcr = engine.verify_sovereign_certificate(
        cert, expected_nonce="challenge-nonce-001"
    )
    assert mismatch_pcr is False
    assert (
        "does not match hardware document PCR2" in msg_pcr or "PCR measurement mismatch" in msg_pcr
    )
