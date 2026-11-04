"""Reference implementation for handling hardware-enclave attestation documents.

.. warning::
   This module does **not** verify hardware attestation. It checks the *shape* of a
   document: PCR values against configured golden measurements, nonce freshness and
   timestamp age, plus whether the ``signature`` field is non-empty. It does not
   verify signatures, X.509 certificate chains, or the hardware root of trust, so a
   document carrying any non-empty signature string passes. Treat
   ``is_valid=True`` as "the fields looked plausible", never as proof that code is
   running inside a TEE.

   With the default config no golden PCRs are configured, so no measurement is
   compared either.

   Real verification needs the platform verifier (AWS Nitro: COSE Sign1 document and
   Nitro root CA; AMD SEV-SNP: SNP report signature and VCEK certificate chain) plus
   published golden measurements. Until that exists this module is for tests, demos
   and API shape only. Do not deploy it as a security control.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from panoptis.protect.model import Finding, Severity, Stage, Verdict


class EnclavePlatform(str, Enum):
    AWS_NITRO = "AWS_NITRO"
    AMD_SEV_SNP = "AMD_SEV_SNP"
    HARDWARE_EMULATOR = "HARDWARE_EMULATOR"


@dataclass
class EnclaveAttestationDocument:
    platform: EnclavePlatform
    pcr0: str  # Kernel / Image SHA-384 or SHA-256
    pcr1: str  # Kernel arguments / Bootloader
    pcr2: str  # Panoptis Agent Runtime binary hash
    nonce: str
    user_data: str = ""
    timestamp: float = field(default_factory=time.time)
    signature: str = ""
    certificate_chain: list[str] = field(default_factory=list)

    def compute_document_hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.platform.value.encode("utf-8"))
        h.update(self.pcr0.encode("utf-8"))
        h.update(self.pcr1.encode("utf-8"))
        h.update(self.pcr2.encode("utf-8"))
        h.update(self.nonce.encode("utf-8"))
        h.update(self.user_data.encode("utf-8"))
        return h.hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform.value,
            "pcr0": self.pcr0,
            "pcr1": self.pcr1,
            "pcr2": self.pcr2,
            "nonce": self.nonce,
            "user_data": self.user_data,
            "timestamp": self.timestamp,
            "signature": self.signature,
            "certificate_chain": self.certificate_chain,
        }


@dataclass
class EnclaveVerificationResult:
    is_valid: bool
    platform: EnclavePlatform
    pcr_matches: bool
    nonce_matches: bool
    reason: str
    finding: Finding | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "platform": self.platform.value,
            "pcr_matches": self.pcr_matches,
            "nonce_matches": self.nonce_matches,
            "reason": self.reason,
            "finding": self.finding.to_dict() if self.finding else None,
        }


@dataclass
class EnclaveVerificationConfig:
    enabled: bool = True
    golden_pcr0: list[str] = field(default_factory=list)
    golden_pcr1: list[str] = field(default_factory=list)
    golden_pcr2: list[str] = field(default_factory=list)
    max_attestation_age_seconds: float = 300.0  # 5 minutes
    allow_emulator: bool = False  # emulator documents are rejected unless opted in


class EnclaveAttestationVerifier:
    """Structural validator for enclave attestation documents.

    No signature or certificate-chain verification happens here; see the module
    docstring. Suitable for exercising the decision pipeline, not for trust.
    """

    def __init__(self, config: EnclaveVerificationConfig | None = None) -> None:
        self.config = config or EnclaveVerificationConfig()

    def verify_attestation(
        self,
        doc: EnclaveAttestationDocument,
        expected_nonce: str | None = None,
    ) -> EnclaveVerificationResult:
        """Check document fields against golden measurements, nonce and age.

        The signature is only checked for presence, and certificate chains are not
        inspected at all.
        """
        # 1. Platform validation
        if doc.platform == EnclavePlatform.HARDWARE_EMULATOR and not self.config.allow_emulator:
            finding = Finding(
                stage=Stage.IDENTITY,
                code="ENCLAVE_EMULATOR_DISALLOWED",
                severity=Severity.CRITICAL,
                message="Confidential hardware attestation required; hardware emulator rejected",
                recommends=Verdict.BLOCK,
            )
            return EnclaveVerificationResult(
                is_valid=False,
                platform=doc.platform,
                pcr_matches=False,
                nonce_matches=False,
                reason="Emulator disallowed in strict hardware mode",
                finding=finding,
            )

        # 2. Replay protection (nonce match)
        nonce_matches = True
        if expected_nonce is not None and doc.nonce != expected_nonce:
            nonce_matches = False
            finding = Finding(
                stage=Stage.IDENTITY,
                code="ENCLAVE_NONCE_MISMATCH",
                severity=Severity.CRITICAL,
                message=f"Attestation document nonce {doc.nonce!r} does not match challenge {expected_nonce!r} (replay attack suspected)",
                recommends=Verdict.BLOCK,
            )
            return EnclaveVerificationResult(
                is_valid=False,
                platform=doc.platform,
                pcr_matches=False,
                nonce_matches=False,
                reason="Nonce mismatch: potential attestation replay",
                finding=finding,
            )

        # 3. Freshness check
        age = time.time() - doc.timestamp
        if age > self.config.max_attestation_age_seconds:
            finding = Finding(
                stage=Stage.IDENTITY,
                code="ENCLAVE_ATTESTATION_EXPIRED",
                severity=Severity.HIGH,
                message=f"Attestation document expired ({round(age, 1)}s old, max {self.config.max_attestation_age_seconds}s)",
                recommends=Verdict.BLOCK,
            )
            return EnclaveVerificationResult(
                is_valid=False,
                platform=doc.platform,
                pcr_matches=False,
                nonce_matches=nonce_matches,
                reason="Attestation document expired",
                finding=finding,
            )

        # 4. PCR measurement checks against approved golden measurements
        pcr_matches = True
        mismatched_pcrs: list[str] = []

        if self.config.golden_pcr0 and doc.pcr0 not in self.config.golden_pcr0:
            pcr_matches = False
            mismatched_pcrs.append(f"PCR0 ({doc.pcr0[:12]}...)")

        if self.config.golden_pcr1 and doc.pcr1 not in self.config.golden_pcr1:
            pcr_matches = False
            mismatched_pcrs.append(f"PCR1 ({doc.pcr1[:12]}...)")

        if self.config.golden_pcr2 and doc.pcr2 not in self.config.golden_pcr2:
            pcr_matches = False
            mismatched_pcrs.append(f"PCR2 ({doc.pcr2[:12]}...)")

        if not pcr_matches:
            finding = Finding(
                stage=Stage.IDENTITY,
                code="ENCLAVE_PCR_MEASUREMENT_MISMATCH",
                severity=Severity.CRITICAL,
                message=f"Enclave code integrity compromised; untrusted PCR measurements: {', '.join(mismatched_pcrs)}",
                evidence={"mismatched_pcrs": mismatched_pcrs},
                recommends=Verdict.BLOCK,
            )
            return EnclaveVerificationResult(
                is_valid=False,
                platform=doc.platform,
                pcr_matches=False,
                nonce_matches=nonce_matches,
                reason=f"PCR measurement mismatch: {', '.join(mismatched_pcrs)}",
                finding=finding,
            )

        # 5. Signature presence only — nothing here can validate a hardware root signature
        if not doc.signature:
            finding = Finding(
                stage=Stage.IDENTITY,
                code="ENCLAVE_MISSING_SIGNATURE",
                severity=Severity.HIGH,
                message="Attestation document missing hardware root signature",
                recommends=Verdict.BLOCK,
            )
            return EnclaveVerificationResult(
                is_valid=False,
                platform=doc.platform,
                pcr_matches=pcr_matches,
                nonce_matches=nonce_matches,
                reason="Missing signature",
                finding=finding,
            )

        return EnclaveVerificationResult(
            is_valid=True,
            platform=doc.platform,
            pcr_matches=True,
            nonce_matches=True,
            reason="Document fields accepted; signature presence checked but NOT verified",
            finding=None,
        )

    @classmethod
    def mint_mock_document(
        cls,
        pcr0: str = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        pcr1: str = "f4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afb",
        pcr2: str = "1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc",
        nonce: str = "challenge_session_1",
        platform: EnclavePlatform = EnclavePlatform.AWS_NITRO,
        user_data: str = "",
    ) -> EnclaveAttestationDocument:
        """Build a document with plausible-looking PCRs and a fake signature.

        The "signature" is ``sha256(document_hash)`` with no key, so it proves
        nothing about any hardware root. Use it to exercise validation logic only.
        """
        doc = EnclaveAttestationDocument(
            platform=platform,
            pcr0=pcr0,
            pcr1=pcr1,
            pcr2=pcr2,
            nonce=nonce,
            user_data=user_data,
            timestamp=time.time(),
        )
        doc.signature = hashlib.sha256(doc.compute_document_hash().encode("utf-8")).hexdigest()
        return doc
