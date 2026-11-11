"""Unit tests for Panopti Court and Merkle Witness Verification."""

from __future__ import annotations

from panopti.court import WitnessChain


def test_witness_chain_records_and_seals() -> None:
    """Witness chain appends entries and updates root hash deterministically."""
    chain = WitnessChain("session-xyz")
    e1 = chain.record("agent_01", "read_file", {"path": "/app/data.txt"})
    e2 = chain.record("agent_01", "write_file", {"path": "/app/out.txt", "lines": 42})

    assert chain.length == 2
    assert e1.parent_hash == WitnessChain.GENESIS_HASH
    assert e2.parent_hash == e1.entry_hash
    assert chain.root_hash == e2.entry_hash


def test_witness_chain_verification_passes_on_unaltered_proof() -> None:
    """Unaltered witness proof validates successfully."""
    chain = WitnessChain("session-audit")
    chain.record("supervisor", "plan", {"objective": "refactor"})
    chain.record("worker", "execute", {"tool": "pytest"})

    proof = chain.export_proof()
    valid, err = WitnessChain.verify_proof(proof)

    assert valid is True
    assert err is None


def test_witness_chain_verification_catches_tampered_payload() -> None:
    """Modifying an entry's payload hash triggers cryptographic verification failure."""
    chain = WitnessChain("session-tamper")
    chain.record("agent_a", "call_api", {"endpoint": "/v1/users"})
    chain.record("agent_a", "call_api", {"endpoint": "/v1/billing"})

    proof = chain.export_proof()

    # Tamper with the payload hash of step 1
    proof["entries"][0]["payload_hash"] = "deadbeef" * 8

    valid, err = WitnessChain.verify_proof(proof)
    assert valid is False
    assert "Hash mismatch at step 1" in (err or "")


def test_witness_chain_verification_catches_tampered_root_hash() -> None:
    """Modifying the root hash triggers root verification failure."""
    chain = WitnessChain("session-root-tamper")
    chain.record("agent_b", "step_1", {})

    proof = chain.export_proof()
    proof["root_hash"] = "0" * 64

    valid, err = WitnessChain.verify_proof(proof)
    assert valid is False
    assert "Root hash mismatch" in (err or "")
