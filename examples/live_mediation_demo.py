#!/usr/bin/env python3
"""AgentWatch / Panoptis — Standalone Offline Live Mediation Demo (GDG Presentation Fallback)

Zero dependencies outside panoptis. No internet, no external database, no API keys required.
Demonstrates:
  1. ALLOW: Benign tool call evaluated and passed.
  2. BLOCK: Catastrophic tool call intercepted and vetoed.
  3. ESCALATE_HITL: Sensitive action paused for Human-In-The-Loop approval.
  4. IMMUTABLE EVIDENCE: Cryptographic hash-chain sealed and verified.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from panoptis.protect import (
    ApprovalBroker,
    ProtectLedger,
    ProtectionPipeline,
    ToolAction,
    Verdict,
)


def run_demo() -> bool:
    print("=" * 70)
    print("  PANOPTIS RUNTIME MEDIATION & FORENSIC LEDGER — LIVE VERIFICATION")
    print("=" * 70)

    # Use a clean isolated temporary ledger
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "evidence.db"
        ledger = ProtectLedger(f"sqlite:///{db_path}")
        broker = ApprovalBroker()
        pipeline = ProtectionPipeline(ledger=ledger, approvals=broker)

        # 1. ALLOW PATH
        print("\n[SCENARIO 1: Safe Tool Execution]")
        action_safe = ToolAction(
            agent_id="gemini-adk-coder",
            session_id="gdg-live-session",
            tool="read_file",
            arguments={"path": "README.md"},
        )
        d1 = pipeline.evaluate(action_safe)
        print(f"  Tool:    {action_safe.tool}(path='README.md')")
        print(f"  Verdict: [GREEN]{d1.verdict.value.upper()}[/GREEN]")
        assert d1.verdict == Verdict.ALLOW, f"Expected ALLOW, got {d1.verdict}"

        # 2. BLOCK PATH
        print("\n[SCENARIO 2: Lethal / Destructive Action Intercept]")
        action_block = ToolAction(
            agent_id="gemini-adk-coder",
            session_id="gdg-live-session",
            tool="bash",
            arguments={"command": "rm -rf /"},
        )
        d2 = pipeline.evaluate(action_block)
        print(f"  Tool:    {action_block.tool}(command='rm -rf /')")
        print(f"  Verdict: [RED]{d2.verdict.value.upper()}[/RED]")
        print(f"  Reason:  {d2.reason}")
        assert d2.verdict == Verdict.BLOCK, f"Expected BLOCK, got {d2.verdict}"

        # 3. ESCALATION PATH (HITL)
        print("\n[SCENARIO 3: Sensitive Privilege Boundary Access]")
        action_escalate = ToolAction(
            agent_id="gemini-adk-coder",
            session_id="gdg-live-session",
            tool="read_file",
            arguments={"path": "../../../../etc/shadow"},
        )
        d3 = pipeline.evaluate(action_escalate, wait_for_approval=False)
        print(f"  Tool:    {action_escalate.tool}(path='../../../../etc/shadow')")
        print(f"  Verdict: [YELLOW]{d3.verdict.value.upper()}[/YELLOW]")
        print(f"  Reason:  {d3.reason}")
        if d3.approval:
            print(f"  HITL Request ID: {d3.approval.get('request_id')}")
        assert d3.verdict == Verdict.ESCALATE_HITL, f"Expected ESCALATE_HITL, got {d3.verdict}"

        # 4. CRYPTOGRAPHIC WITNESS AUDIT
        print("\n[SCENARIO 4: Cryptographic Evidence Hash-Chain Verification]")
        records = ledger.query(limit=10)
        print(f"  Recorded Decisions in Ledger: {len(records)}")
        for i, rec in enumerate(records, 1):
            payload = rec.get("payload", {})
            action_data = payload.get("action", {})
            tool = action_data.get("tool", "unknown")
            v = payload.get("verdict", "unknown")
            print(f"    {i}. Action: {tool:<12} | Verdict: {v:<14}")

        audit = ledger.verify()
        print(f"\n  Hash-Chain Intact: {audit.get('ok')}")
        print(f"  Head Segment:      {audit.get('head', {}).get('segment_hash', '')[:24]}...")
        assert audit.get("ok") is True, "Ledger verification failed"

        # Explicitly close store connection to release file lock on Windows
        ledger.store.close()

    print("\n" + "=" * 70)
    print("  STATUS: 100% VERIFIED — ALL 3 MEDIATION PATHS + EVIDENCE AUDIT PASS")
    print("=" * 70)
    return True


if __name__ == "__main__":
    run_demo()
