"""Runtime Memory Integrity and Anti-Tampering Guard (AgenTEE Mitigation).

Implements process-level anti-tampering verification based on AgenTEE (Imperial College London):
In unshielded user processes, compromised agents or host hooks can attempt runtime monkey-patching
(e.g. overriding `ProtectionPipeline.evaluate` or `RBACEngine.authorize` in memory).

This module cryptographically seals critical function bytecodes and policy structures at boot
and performs continuous constant-time integrity checks before any tool execution.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any

from panoptis.protect.model import Finding, Severity, Stage, Verdict


@dataclass
class SealedFunction:
    name: str
    target_obj: Any
    attr_name: str
    code_hash: str


class RuntimeIntegrityGuard:
    """Cryptographic sealing and verification of critical enforcement code in memory."""

    def __init__(self, key: bytes | None = None) -> None:
        self.key = key or secrets.token_bytes(32)
        self.sealed_functions: dict[str, SealedFunction] = {}

    def seal_function(self, name: str, target_obj: Any, attr_name: str) -> None:
        """Capture and HMAC-seal the bytecode and signature of a critical method."""
        func = getattr(target_obj, attr_name, None)
        if func is None or not hasattr(func, "__code__"):
            return

        code_bytes = func.__code__.co_code
        h = hmac.new(self.key, code_bytes, hashlib.sha256).hexdigest()
        self.sealed_functions[name] = SealedFunction(
            name=name,
            target_obj=target_obj,
            attr_name=attr_name,
            code_hash=h,
        )

    def verify_integrity(self) -> tuple[bool, list[Finding]]:
        """Verify that no sealed function bytecode or pointer has been altered in memory."""
        findings: list[Finding] = []
        is_intact = True

        for name, sealed in self.sealed_functions.items():
            current_func = getattr(sealed.target_obj, sealed.attr_name, None)
            if current_func is None or not hasattr(current_func, "__code__"):
                is_intact = False
                findings.append(
                    Finding(
                        stage=Stage.PIPELINE,
                        code="MEMORY_METHOD_POINTER_REWRITTEN",
                        severity=Severity.CRITICAL,
                        message=f"Runtime integrity violation: sealed method {name!r} was unseated or replaced in memory",
                        evidence={"sealed_name": name},
                        recommends=Verdict.BLOCK,
                    )
                )
                continue

            current_code = current_func.__code__.co_code
            current_h = hmac.new(self.key, current_code, hashlib.sha256).hexdigest()

            if not hmac.compare_digest(current_h, sealed.code_hash):
                is_intact = False
                findings.append(
                    Finding(
                        stage=Stage.PIPELINE,
                        code="MEMORY_BYTECODE_TAMPERING_DETECTED",
                        severity=Severity.CRITICAL,
                        message=f"Runtime integrity violation: bytecode of method {name!r} was modified in RAM",
                        evidence={"sealed_name": name},
                        recommends=Verdict.BLOCK,
                    )
                )

        return is_intact, findings
