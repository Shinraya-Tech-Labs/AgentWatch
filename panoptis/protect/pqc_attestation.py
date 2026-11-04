"""Simulated post-quantum and "zk-STARK" attestation — NOT cryptography.

.. warning::
   The class and constant names in this module match NIST and STARK vocabulary, but
   the implementation provides **no security**. It exists to exercise the shape of a
   sovereign attestation pipeline (keypair -> signature -> proof -> certificate), not
   to protect anything.

   What is actually implemented, and why it is not the real thing:

   * :class:`PQCSigner` is a 4-dimensional toy module-LWE scheme. ML-DSA-65
     (FIPS 204) operates over 256 coefficient rings with k=6, l=5 and a ~2^256
     challenge space; here ``K_DIM = 4`` and the Fiat-Shamir challenge is an integer
     in ``[1, 250]``. There is no rejection sampling, no norm bound, no hint, and no
     side-channel hardening — Python big-int arithmetic is not constant time. The
     ``PQCAlgorithm`` label only appears in the signature prefix; it changes nothing
     about the arithmetic.
   * :class:`StarkRecursiveProver` does not build an execution trace, a polynomial
     commitment, or a FRI low-degree test. Its "rounds" are a SHA-256 hash chain, and
     :meth:`StarkRecursiveVerifier.verify_proof` recomputes that same chain from the
     proof it was given. A verifier that re-runs the prover's own arithmetic proves
     nothing about the trace, provides no soundness, and reveals everything — there
     is no zero-knowledge.

   Do not use this module for signatures, integrity, compliance evidence, or any
   claim of post-quantum security. Replace it with a vetted implementation
   (``liboqs``/``dilithium-py`` for ML-DSA, a real STARK library) before any such
   claim is made.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from panoptis.protect.enclave_attestation import (
    EnclaveAttestationDocument,
    EnclaveAttestationVerifier,
    EnclavePlatform,
    EnclaveVerificationConfig,
    EnclaveVerificationResult,
)
from panoptis.protect.model import Decision, Verdict

logger = logging.getLogger(__name__)

# NIST FIPS 204 ML-DSA (Dilithium) primary modulus
Q = 8380417
K_DIM = 4


class PQCAlgorithm(str, Enum):
    """NIST algorithm names used as labels by the simulation.

    The values are real standard names; the arithmetic behind them is not the
    standard (see module docstring). The label only sets the signature prefix.
    """

    ML_DSA_44 = "ML-DSA-44"  # NIST Security Level 2 (Dilithium2)
    ML_DSA_65 = "ML-DSA-65"  # NIST Security Level 3 (Dilithium3)
    ML_DSA_87 = "ML-DSA-87"  # NIST Security Level 5 (Dilithium5)
    ML_KEM_768 = "ML-KEM-768"  # NIST Security Level 3 (Kyber768)


@dataclass(frozen=True)
class PQCKeyPair:
    """Post-quantum Module-LWE keypair container."""

    algorithm: PQCAlgorithm
    public_key_hex: str  # Encodes (rho_hex, t_vector_hex)
    secret_key_hex: str  # Encodes (s_vector_hex, rho_hex)
    key_id: str


class PQCSigner:
    """Toy 4-dimensional module-LWE signature scheme. Not ML-DSA; see module docstring.

    ponytail: keeps a hash-based 4-dim lattice scheme so the attestation pipeline has
    the right shape with zero C-deps. Not a security boundary — replace with
    dilithium-py before trusting any signature it produces.
    """

    def __init__(
        self,
        keypair: PQCKeyPair | None = None,
        algorithm: PQCAlgorithm = PQCAlgorithm.ML_DSA_65,
    ) -> None:
        self.algorithm = algorithm
        self.keypair = keypair or self.generate_keypair(algorithm)

    @classmethod
    def generate_keypair(cls, algorithm: PQCAlgorithm = PQCAlgorithm.ML_DSA_65) -> PQCKeyPair:
        """Generate a post-quantum lattice keypair over Module-LWE."""
        seed_rho = secrets.token_bytes(32)
        matrix_a = cls._expand_matrix_a(seed_rho)

        # Generate small secret vector s in [-4, 4]^K_DIM
        s = [(secrets.randbelow(9) - 4) for _ in range(K_DIM)]

        # Public key vector t = A * s mod Q
        t = cls._matrix_vector_mul(matrix_a, s)

        rho_hex = seed_rho.hex()
        t_hex = ",".join(str(val) for val in t)
        s_hex = ",".join(str(val) for val in s)

        pk_str = f"{rho_hex}:{t_hex}"
        sk_str = f"{s_hex}:{rho_hex}"

        key_id = hashlib.sha256(pk_str.encode()).hexdigest()[:16]

        return PQCKeyPair(
            algorithm=algorithm,
            public_key_hex=pk_str,
            secret_key_hex=sk_str,
            key_id=f"pqc-{algorithm.value.lower()}-{key_id}",
        )

    def sign(self, message: bytes) -> str:
        """Produce a toy lattice signature (z, c) over message. Not ML-DSA."""
        s_part, rho_hex = self.keypair.secret_key_hex.split(":")
        s = [int(v) for v in s_part.split(",")]
        matrix_a = self._expand_matrix_a(bytes.fromhex(rho_hex))

        # Deterministic nonce vector y derived from (sk, message)
        h_nonce = hashlib.sha3_512(self.keypair.secret_key_hex.encode() + message).digest()
        y = [int.from_bytes(h_nonce[i * 4 : (i + 1) * 4], "big") % 100000 for i in range(K_DIM)]

        # Commitment w = A * y mod Q
        w = self._matrix_vector_mul(matrix_a, y)

        # Fiat-Shamir scalar challenge c
        c = self._hash_challenge(self.keypair.public_key_hex, message, w)

        # Response vector z = y + c * s mod Q
        z = [(y[i] + (c * s[i])) % Q for i in range(K_DIM)]

        z_str = ",".join(str(v) for v in z)
        return f"{self.algorithm.value}${c}${z_str}"

    @classmethod
    def verify(cls, public_key_hex: str, message: bytes, signature_str: str) -> bool:
        """Verify the toy lattice signature. Not ML-DSA."""
        try:
            parts = signature_str.split("$")
            if len(parts) != 3:
                return False
            _algo, c_str, z_str = parts
            c = int(c_str)
            z = [int(v) % Q for v in z_str.split(",")]
            if len(z) != K_DIM:
                return False

            rho_hex, t_str = public_key_hex.split(":")
            matrix_a = cls._expand_matrix_a(bytes.fromhex(rho_hex))
            t = [int(v) for v in t_str.split(",")]

            # Algebraic verification: w' = A * z - c * t mod Q
            az = cls._matrix_vector_mul(matrix_a, z)
            w_prime = [(az[i] - (c * t[i])) % Q for i in range(K_DIM)]

            # Check challenge consistency c' == c
            c_prime = cls._hash_challenge(public_key_hex, message, w_prime)
            return c == c_prime
        except Exception:
            return False

    @staticmethod
    def _matrix_vector_mul(matrix: list[list[int]], vec: list[int]) -> list[int]:
        """Compute A * v mod Q."""
        res = [0] * K_DIM
        for i in range(K_DIM):
            s = 0
            for j in range(K_DIM):
                s = (s + matrix[i][j] * vec[j]) % Q
            res[i] = s
        return res

    @staticmethod
    def _expand_matrix_a(rho: bytes) -> list[list[int]]:
        """SHAKE-256 expansion of public seed rho into K_DIM x K_DIM matrix mod Q."""
        matrix: list[list[int]] = []
        for i in range(K_DIM):
            row: list[int] = []
            for j in range(K_DIM):
                cell_hash = hashlib.sha3_256(rho + bytes([i, j])).digest()
                val = int.from_bytes(cell_hash[:4], "big") % Q
                row.append(val)
            matrix.append(row)
        return matrix

    @staticmethod
    def _hash_challenge(pk_hex: str, message: bytes, w: list[int]) -> int:
        """Hash public key, message, and commitment vector w into scalar challenge."""
        w_repr = ",".join(str(val) for val in w)
        payload = f"{pk_hex}:{w_repr}:".encode() + message
        h = hashlib.sha3_256(payload).digest()
        # Scalar challenge in [1, 255]
        return (int.from_bytes(h[:2], "big") % 250) + 1


@dataclass
class StarkFRICommitment:
    """One round of FRI (Fast Reed-Solomon IOP of Proximity) low-degree polynomial folding."""

    round_index: int
    merkle_root: str
    evaluation_sample: str


@dataclass
class StarkProof:
    """Succinct transparent zero-knowledge STARK argument proof."""

    proof_id: str
    trace_length: int
    execution_trace_root: str
    constraint_polynomial_root: str
    fri_commitments: list[StarkFRICommitment]
    policy_root: str
    enclave_pcr2: str
    public_inputs: dict[str, Any]
    pqc_signature: str
    generation_time_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "trace_length": self.trace_length,
            "execution_trace_root": self.execution_trace_root,
            "constraint_polynomial_root": self.constraint_polynomial_root,
            "fri_commitments": [
                {
                    "round_index": fri.round_index,
                    "merkle_root": fri.merkle_root,
                    "evaluation_sample": fri.evaluation_sample,
                }
                for fri in self.fri_commitments
            ],
            "policy_root": self.policy_root,
            "enclave_pcr2": self.enclave_pcr2,
            "public_inputs": self.public_inputs,
            "pqc_signature": self.pqc_signature,
            "generation_time_ms": round(self.generation_time_ms, 3),
        }


class StarkRecursiveProver:
    """Builds a hash-chain "proof" over a decision trace. Not a STARK.

    There is no execution trace commitment beyond a Merkle root of row hashes, no
    polynomial, and no low-degree test. The output is a deterministic transcript for
    pipeline-shape testing only.
    """

    def __init__(self, signer: PQCSigner | None = None) -> None:
        self.signer = signer or PQCSigner()

    def prove_execution_trace(
        self,
        decisions: list[Decision],
        policy_root: str,
        enclave_pcr2: str,
    ) -> StarkProof:
        """Commit to the decisions and hash-chain the result. Adds no zero-knowledge."""
        t0 = time.perf_counter()
        trace_length = len(decisions)
        if trace_length == 0:
            raise ValueError("Execution trace cannot be empty for STARK proof generation")

        # 1. Algebraic Execution Trace (AET) commitment
        trace_leaves: list[str] = []
        for idx, dec in enumerate(decisions):
            row_repr = f"{idx}:{dec.verdict.value}:{int(dec.risk_score * 1000)}:{dec.action.tool}"
            trace_leaves.append(hashlib.sha256(row_repr.encode()).hexdigest())

        trace_root = self._compute_merkle_root(trace_leaves)

        # 2. Composition / Constraint Polynomial (AIR Constraints)
        constraint_leaves: list[str] = []
        for idx, dec in enumerate(decisions):
            violates = dec.verdict == Verdict.ALLOW and dec.risk_score > 0.85
            c_val = "0" if not violates else "1"
            constraint_leaves.append(
                hashlib.sha256(
                    f"AIR:{idx}:{c_val}:{policy_root}:{enclave_pcr2}".encode()
                ).hexdigest()
            )

        constraint_root = self._compute_merkle_root(constraint_leaves)

        # 3. FRI Low-Degree Testing (3 reduction folding rounds)
        fri_commitments: list[StarkFRICommitment] = []
        curr_root = constraint_root
        for round_idx in range(3):
            next_data = hashlib.sha256(f"FRI_ROUND_{round_idx}:{curr_root}".encode()).hexdigest()
            eval_sample = hashlib.sha256(f"EVAL_{round_idx}:{next_data}".encode()).hexdigest()[:16]
            fri_commitments.append(
                StarkFRICommitment(
                    round_index=round_idx,
                    merkle_root=next_data,
                    evaluation_sample=eval_sample,
                )
            )
            curr_root = next_data

        public_inputs = {
            "total_decisions": trace_length,
            "allowed_decisions": sum(1 for d in decisions if d.allowed),
            "blocked_decisions": sum(1 for d in decisions if not d.allowed),
            "max_risk_observed": max((d.risk_score for d in decisions), default=0.0),
        }

        # 4. Bind public inputs + trace root to post-quantum signature
        proof_id_seed = f"{trace_root}:{constraint_root}:{policy_root}:{enclave_pcr2}"
        proof_id = hashlib.sha256(proof_id_seed.encode()).hexdigest()

        sig_payload = f"{proof_id}:{trace_root}:{enclave_pcr2}:{json.dumps(public_inputs, sort_keys=True)}".encode()
        pqc_sig = self.signer.sign(sig_payload)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        return StarkProof(
            proof_id=proof_id,
            trace_length=trace_length,
            execution_trace_root=trace_root,
            constraint_polynomial_root=constraint_root,
            fri_commitments=fri_commitments,
            policy_root=policy_root,
            enclave_pcr2=enclave_pcr2,
            public_inputs=public_inputs,
            pqc_signature=pqc_sig,
            generation_time_ms=elapsed_ms,
        )

    def _compute_merkle_root(self, leaves: list[str]) -> str:
        """Compute Merkle tree root over hex leaves."""
        current = [bytes.fromhex(leaf) for leaf in leaves]
        if not current:
            return hashlib.sha256(b"").hexdigest()

        while len(current) > 1:
            if len(current) % 2 == 1:
                current.append(current[-1])
            next_level: list[bytes] = []
            for i in range(0, len(current), 2):
                h = hashlib.sha256(current[i] + current[i + 1]).digest()
                next_level.append(h)
            current = next_level

        return current[0].hex()


class StarkRecursiveVerifier:
    """Recomputes the prover's hash chain. Structural check only, not proof verification."""

    @staticmethod
    def verify_proof(proof: StarkProof, public_key_pqc: str | None = None) -> tuple[bool, str]:
        """Check structural consistency of the transcript and, optionally, the toy signature.

        Because every value is recomputed from the proof itself, this cannot detect a
        forged proof. It only catches accidental corruption.
        """
        t0 = time.perf_counter()

        # 1. Structural check
        if len(proof.fri_commitments) < 2:
            return False, "Insufficient FRI commitment rounds (soundness parameter error)"

        # 2. Check public inputs boundary
        pub = proof.public_inputs
        if pub.get("total_decisions", 0) <= 0:
            return False, "Public input total_decisions must be positive"

        # 3. Verify FRI round consistency
        curr_root = proof.constraint_polynomial_root
        for fri in proof.fri_commitments:
            expected_root = hashlib.sha256(
                f"FRI_ROUND_{fri.round_index}:{curr_root}".encode()
            ).hexdigest()
            if fri.merkle_root != expected_root:
                return (
                    False,
                    f"FRI round {fri.round_index} commitment mismatch (low-degree test failed)",
                )
            curr_root = expected_root

        # 4. Verify Post-Quantum Signature if key supplied
        if public_key_pqc:
            sig_payload = f"{proof.proof_id}:{proof.execution_trace_root}:{proof.enclave_pcr2}:{json.dumps(pub, sort_keys=True)}".encode()
            if not PQCSigner.verify(public_key_pqc, sig_payload, proof.pqc_signature):
                return False, "Toy lattice signature does not verify"

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return (
            True,
            f"Structural consistency check passed in {elapsed_ms:.2f}ms "
            "(no zero-knowledge or soundness guarantee)",
        )


@dataclass
class SovereignAttestationCertificate:
    """Container binding a simulated enclave document to a simulated proof transcript."""

    certificate_id: str
    platform: EnclavePlatform
    enclave_document: EnclaveAttestationDocument
    stark_proof: StarkProof
    pqc_algorithm: PQCAlgorithm
    pqc_public_key: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "certificate_id": self.certificate_id,
            "platform": self.platform.value,
            "enclave_document": self.enclave_document.to_dict(),
            "stark_proof": self.stark_proof.to_dict(),
            "pqc_algorithm": self.pqc_algorithm.value,
            "pqc_public_key": self.pqc_public_key,
            "timestamp": self.timestamp,
        }


class SovereignAttestationEngine:
    """Simulation glue: enclave document + hash-chain transcript + toy signature.

    Every component is reference-only (see module docstring); a successful
    ``verify_sovereign_certificate`` means the pieces are mutually consistent, not
    that any hardware or post-quantum guarantee holds.
    """

    def __init__(
        self,
        enclave_config: EnclaveVerificationConfig | None = None,
        pqc_signer: PQCSigner | None = None,
    ) -> None:
        self.enclave_verifier = EnclaveAttestationVerifier(enclave_config)
        self.pqc_signer = pqc_signer or PQCSigner()
        self.stark_prover = StarkRecursiveProver(self.pqc_signer)
        self.stark_verifier = StarkRecursiveVerifier()

    def generate_sovereign_certificate(
        self,
        decisions: list[Decision],
        enclave_doc: EnclaveAttestationDocument,
        policy_root: str,
    ) -> SovereignAttestationCertificate:
        """Mint a simulated certificate binding enclave document, transcript and toy signature."""
        stark_proof = self.stark_prover.prove_execution_trace(
            decisions=decisions,
            policy_root=policy_root,
            enclave_pcr2=enclave_doc.pcr2,
        )

        cert_seed = f"{enclave_doc.compute_document_hash()}:{stark_proof.proof_id}:{self.pqc_signer.keypair.public_key_hex}"
        cert_id = hashlib.sha256(cert_seed.encode()).hexdigest()

        return SovereignAttestationCertificate(
            certificate_id=cert_id,
            platform=enclave_doc.platform,
            enclave_document=enclave_doc,
            stark_proof=stark_proof,
            pqc_algorithm=self.pqc_signer.algorithm,
            pqc_public_key=self.pqc_signer.keypair.public_key_hex,
        )

    def verify_sovereign_certificate(
        self,
        cert: SovereignAttestationCertificate,
        expected_nonce: str | None = None,
    ) -> tuple[bool, str]:
        """Check that the three simulated artifacts agree with each other."""
        enclave_res: EnclaveVerificationResult = self.enclave_verifier.verify_attestation(
            cert.enclave_document, expected_nonce=expected_nonce
        )
        if not enclave_res.is_valid:
            return False, f"Hardware Enclave Attestation rejected: {enclave_res.reason}"

        if cert.stark_proof.enclave_pcr2 != cert.enclave_document.pcr2:
            return (
                False,
                f"STARK proof enclave_pcr2 ({cert.stark_proof.enclave_pcr2[:12]}) "
                f"does not match hardware document PCR2 ({cert.enclave_document.pcr2[:12]})",
            )

        stark_valid, stark_reason = self.stark_verifier.verify_proof(
            cert.stark_proof, public_key_pqc=cert.pqc_public_key
        )
        if not stark_valid:
            return False, f"Transcript consistency check failed: {stark_reason}"

        return (
            True,
            "Simulated verification succeeded: document fields, hash-chain transcript and toy "
            "signature are consistent. This is NOT hardware or post-quantum verification.",
        )
