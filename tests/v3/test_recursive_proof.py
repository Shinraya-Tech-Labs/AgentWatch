from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

import pytest

from panoptis.evidence.attestation import (
    HmacSha256Signer,
    issue_token,
)
from panoptis.evidence.merkle import MerkleTree
from panoptis.evidence.recursive_proof import (
    RecursiveProofCertificate,
    RecursiveProofCompressor,
)

SEALED_AT = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


def make_tokens(count: int, signer: HmacSha256Signer) -> list:
    leaves = [hashlib.sha256(f"action-evidence-{i}".encode()).hexdigest() for i in range(count)]
    tree = MerkleTree(leaves)
    return [
        issue_token(tree, i, segment_id=f"seg-{i // 5}", signer=signer, timestamp=SEALED_AT)
        for i in range(count)
    ]


def test_batch_proof_compression_aggregates_tokens_and_proves_validity():
    signer_key = secrets.token_bytes(32)
    signer = HmacSha256Signer(signer_key, key_id="audit-root-1")
    tokens = make_tokens(10, signer)

    compressor = RecursiveProofCompressor(signer=signer)
    disclosures = [
        {"verdict": "allow", "human_approved": True, "fail_closed": True} for _ in range(10)
    ]

    cert = compressor.generate_batch_proof(tokens, selective_disclosures=disclosures)

    assert isinstance(cert, RecursiveProofCertificate)
    assert cert.batch_size == 10
    assert cert.format == "panoptis-recursive-audit/1"
    assert len(cert.proven_invariants) == 30
    assert cert.signature is not None

    # Verify validity of batch certificate
    ok, reason = RecursiveProofCompressor.verify_batch_certificate(cert, tokens, verifier=signer)
    assert ok
    assert "verified valid" in reason


def test_tampered_batch_proof_fails_verification():
    signer_key = secrets.token_bytes(32)
    signer = HmacSha256Signer(signer_key, key_id="audit-root-1")
    tokens = make_tokens(5, signer)

    compressor = RecursiveProofCompressor(signer=signer)
    cert = compressor.generate_batch_proof(tokens)

    # Tamper with the certificate commitment
    tampered_cert = RecursiveProofCertificate(
        format=cert.format,
        batch_size=cert.batch_size,
        merkle_roots=cert.merkle_roots,
        aggregated_commitment="0" * 64,  # corrupted commitment
        proven_invariants=cert.proven_invariants,
        created_at=cert.created_at,
        signature=cert.signature,
        signer_key_id=cert.signer_key_id,
    )

    ok, reason = RecursiveProofCompressor.verify_batch_certificate(
        tampered_cert, tokens, verifier=signer
    )
    assert not ok
    assert "commitment mismatch" in reason


def test_empty_batch_raises_error():
    compressor = RecursiveProofCompressor()
    with pytest.raises(ValueError, match="cannot be empty"):
        compressor.generate_batch_proof([])
