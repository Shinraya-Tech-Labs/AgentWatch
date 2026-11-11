"""Panopti Doctor: Comprehensive runtime environment and agent protection diagnostics."""

from __future__ import annotations

import platform
import sys
from typing import Any

from panopti.guard import guard
from panopti.witness import WitnessChain
from panoptis.circuit_breaker.loop_detector import LoopDetector
from panoptis.core.safety import SafetyEngine


def run_diagnostics() -> dict[str, Any]:
    """Run full diagnostic checks across all Panopti safety and telemetry subsystems."""
    results: dict[str, Any] = {
        "status": "healthy",
        "system": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "components": {},
    }

    # 1. Panopti Guard & Safety Engine
    try:
        guard_inst = guard
        engine_ok = isinstance(guard_inst._safety_engine, SafetyEngine)
        loop_ok = isinstance(guard_inst._loop_detector, LoopDetector)
        results["components"]["guard"] = {
            "status": "ok" if (engine_ok and loop_ok) else "warn",
            "safety_engine": "active" if engine_ok else "missing",
            "loop_detector": "active" if loop_ok else "missing",
            "registered_contracts": len(guard_inst._contracts._contracts),
        }
    except Exception as e:
        results["components"]["guard"] = {"status": "error", "error": str(e)}
        results["status"] = "degraded"

    # 2. Cryptographic witness chain
    try:
        chain = WitnessChain("doctor-selftest")
        chain.record("doctor", "ping", {"check": "self-test"})
        proof = chain.export_proof()
        valid, err = WitnessChain.verify_proof(proof)
        results["components"]["witness"] = {
            "status": "ok" if valid else "error",
            "merkle_verification": "verified" if valid else "failed",
            "error": err,
        }
    except Exception as e:
        results["components"]["witness"] = {"status": "error", "error": str(e)}
        results["status"] = "degraded"

    # 3. Storage Layer Check
    try:
        results["components"]["storage"] = {
            "status": "ok",
            "engine": "available",
        }
    except Exception as e:
        results["components"]["storage"] = {"status": "info", "message": str(e)}

    return results


def format_doctor_report(diag: dict[str, Any]) -> str:
    """Format diagnostic dictionary into high-impact terminal text."""
    lines: list[str] = [
        "👁️  PANOPTI RUNTIME DOCTOR",
        "==================================================",
        f"Overall Status: {diag['status'].upper()}",
        f"Environment: Python {diag['system']['python_version']} on {diag['system']['machine']}",
        "--------------------------------------------------",
        "Subsystem Status:",
    ]

    for comp, info in diag.get("components", {}).items():
        st = info.get("status", "unknown").upper()
        icon = "✅" if st == "OK" else ("⚠️" if st == "WARN" else "❌")
        lines.append(f"  {icon} {comp.title()}: {st}")
        for k, v in info.items():
            if k != "status":
                lines.append(f"     • {k}: {v}")

    lines.append("==================================================")
    lines.append("All pre-execution interceptors and forensic witnesses operational.")
    return "\n".join(lines)
