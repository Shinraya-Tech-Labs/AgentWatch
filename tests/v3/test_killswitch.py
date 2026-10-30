"""Comprehensive unit tests for Panopti Emergency Kill Switch (Issue #799)."""

from __future__ import annotations

import concurrent.futures
import os
import signal
import sys
import time
import unittest
from pathlib import Path

from agentwatch.runtime.killswitch import (
    EmergencyKillSwitch as RuntimeEmergencyKillSwitch,
)
from agentwatch.runtime.killswitch import (
    EmergencyStopTriggered as RuntimeEmergencyStopTriggered,
)
from panopti.demo import ActiveSentinel
from panopti.guard import PanoptiGuard
from panopti.killswitch import EmergencyKillSwitch, EmergencyStopTriggered


class TestEmergencyKillSwitch(unittest.TestCase):
    """Verify state transitions, file sentinels, signal trapping, and guardrail fail-closed behavior."""

    def setUp(self) -> None:
        self.tmp_kill = Path(f".panopti_test_{int(time.time() * 1000)}.kill")
        if self.tmp_kill.exists():
            self.tmp_kill.unlink()

    def tearDown(self) -> None:
        if self.tmp_kill.exists():
            self.tmp_kill.unlink()
        if "PANOPTI_KILL_FILE" in os.environ:
            del os.environ["PANOPTI_KILL_FILE"]

    def test_default_state_not_tripped(self) -> None:
        """A new EmergencyKillSwitch starts in untripped nominal state."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        self.assertFalse(ks.is_tripped())
        self.assertIsNone(ks.tripped_at)
        self.assertEqual(ks.reason, "Manual operator trip")

    def test_manual_trip_with_default_reason(self) -> None:
        """Calling trip() sets the tripped state with default reason and timestamp."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        start_time = time.time()
        ks.trip()
        self.assertTrue(ks.is_tripped())
        self.assertIsNotNone(ks.tripped_at)
        assert ks.tripped_at is not None
        self.assertGreaterEqual(ks.tripped_at, start_time)
        self.assertEqual(ks.reason, "Manual operator trip")

    def test_manual_trip_with_custom_reason(self) -> None:
        """Calling trip() with a custom reason preserves forensic audit message."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        ks.trip(reason="Operator detected unauthorized external exfiltration")
        self.assertTrue(ks.is_tripped())
        self.assertEqual(ks.reason, "Operator detected unauthorized external exfiltration")

    def test_reset_clears_tripped_state(self) -> None:
        """Calling reset() restores nominal untripped state and clears timestamp."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        ks.trip("Test trip")
        self.assertTrue(ks.is_tripped())
        ks.reset()
        self.assertFalse(ks.is_tripped())
        self.assertIsNone(ks.tripped_at)
        self.assertEqual(ks.reason, "Manual operator trip")

    def test_file_sentinel_detection(self) -> None:
        """Creating the sentinel file automatically trips the kill switch."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        self.assertFalse(ks.is_tripped())
        self.tmp_kill.touch()
        self.assertTrue(ks.is_tripped())
        self.assertIn("Kill file sentinel detected", ks.reason)

    def test_file_sentinel_with_custom_content(self) -> None:
        """Sentinel file content is extracted as the forensic trip reason."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        self.tmp_kill.write_text(
            "CRITICAL: Watchdog daemon stopped agent cluster", encoding="utf-8"
        )
        self.assertTrue(ks.is_tripped())
        self.assertEqual(ks.reason, "CRITICAL: Watchdog daemon stopped agent cluster")

    def test_reset_removes_file_sentinel(self) -> None:
        """Calling reset() removes the sentinel file from disk to prevent immediate re-tripping."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        self.tmp_kill.write_text("Halt", encoding="utf-8")
        self.assertTrue(ks.is_tripped())
        self.assertTrue(self.tmp_kill.exists())
        ks.reset()
        self.assertFalse(self.tmp_kill.exists())
        self.assertFalse(ks.is_tripped())

    def test_custom_kill_file_path(self) -> None:
        """Custom kill file paths are detected correctly."""
        custom_file = Path(f".custom_kill_{int(time.time() * 1000)}.flag")
        try:
            ks = EmergencyKillSwitch(kill_file=custom_file, enable_signals=False)
            self.assertFalse(ks.is_tripped())
            custom_file.touch()
            self.assertTrue(ks.is_tripped())
        finally:
            if custom_file.exists():
                custom_file.unlink()

    def test_cli_flag_kill_file_detection(self) -> None:
        """Automatic detection of --kill-file in sys.argv."""
        custom_cli_file = f".cli_kill_{int(time.time() * 1000)}.flag"
        old_argv = list(sys.argv)
        try:
            sys.argv = ["panopti", "demo", "--kill-file", custom_cli_file]
            ks = EmergencyKillSwitch(enable_signals=False)
            self.assertEqual(ks.kill_file, Path(custom_cli_file))
        finally:
            sys.argv = old_argv

    def test_env_var_kill_file_detection(self) -> None:
        """Automatic detection of PANOPTI_KILL_FILE environment variable."""
        custom_env_file = f".env_kill_{int(time.time() * 1000)}.flag"
        os.environ["PANOPTI_KILL_FILE"] = custom_env_file
        old_argv = list(sys.argv)
        try:
            sys.argv = ["panopti", "demo"]
            ks = EmergencyKillSwitch(enable_signals=False)
            self.assertEqual(ks.kill_file, Path(custom_env_file))
        finally:
            sys.argv = old_argv

    def test_signal_handling_trips_switch(self) -> None:
        """handle_signal sets atomic tripped state and records signal number."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        ks.handle_signal(10)
        self.assertTrue(ks.is_tripped())
        self.assertIn("POSIX signal 10 received", ks.reason)

    def test_posix_signal_trap_if_available(self) -> None:
        """POSIX SIGUSR1 signal trap sets tripped state if supported on platform."""
        if not hasattr(signal, "SIGUSR1"):
            self.skipTest("SIGUSR1 not available on this platform")
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=True)
        os.kill(os.getpid(), signal.SIGUSR1)
        time.sleep(0.01)
        self.assertTrue(ks.is_tripped())
        self.assertIn("SIGUSR1", ks.reason)

    def test_verify_raises_emergency_stop_triggered(self) -> None:
        """verify() raises EmergencyStopTriggered with reason and timestamp when tripped."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        ks.trip("Forensic lockdown")
        with self.assertRaises(EmergencyStopTriggered) as ctx:
            ks.verify()
        self.assertEqual(ctx.exception.reason, "Forensic lockdown")
        self.assertIsNotNone(ctx.exception.timestamp)

    def test_verify_passes_when_not_tripped(self) -> None:
        """verify() completes without error when not tripped."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        ks.verify()  # Should not raise

    def test_guard_decorator_fail_closed(self) -> None:
        """@guard decorator allows execution normally, but fails closed with EmergencyStopTriggered when tripped."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)

        @ks.guard
        def deploy_pod(pod_name: str) -> str:
            return f"Pod {pod_name} deployed"

        self.assertEqual(deploy_pod("web"), "Pod web deployed")

        ks.trip("Emergency stop initiated by DevOps operator")
        with self.assertRaises(EmergencyStopTriggered) as ctx:
            deploy_pod("db")
        self.assertEqual(ctx.exception.reason, "Emergency stop initiated by DevOps operator")

    def test_thread_safe_concurrent_trip_and_reset(self) -> None:
        """Concurrent trip and reset calls across threads maintain consistency without race conditions."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)

        def worker(idx: int) -> None:
            for _ in range(50):
                if idx % 2 == 0:
                    ks.trip(f"Trip from worker {idx}")
                    _ = ks.is_tripped()
                else:
                    ks.reset()
                    _ = ks.is_tripped()

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(8)]
            for f in futures:
                f.result()

        # Final state check is deterministic
        ks.reset()
        self.assertFalse(ks.is_tripped())

    def test_panopti_guard_integration(self) -> None:
        """PanoptiGuard rejects tool calls with EmergencyStopTriggered when kill switch is tripped."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        guard = PanoptiGuard(kill_switch=ks)

        @guard.tool("test_action")
        def execute_action(payload: str) -> str:
            return f"Executed {payload}"

        self.assertEqual(execute_action("nominal"), "Executed nominal")

        ks.trip("Active cyber-attack detected on perimeter")
        with self.assertRaises(EmergencyStopTriggered) as ctx:
            execute_action("subsequent_action")
        self.assertEqual(ctx.exception.reason, "Active cyber-attack detected on perimeter")

    def test_panopti_demo_sentinel_integration(self) -> None:
        """ActiveSentinel hypervisor intercepts tool intent when kill switch is tripped."""
        ks = EmergencyKillSwitch(kill_file=self.tmp_kill, enable_signals=False)
        sentinel = ActiveSentinel(kill_switch=ks)

        verdict_1 = sentinel.evaluate_intent(
            tool_name="database_query",
            arguments={"query": "SELECT * FROM users LIMIT 10;"},
        )
        self.assertTrue(verdict_1.allowed)

        ks.trip("Sentinel kill-file dropped")
        verdict_2 = sentinel.evaluate_intent(
            tool_name="database_query",
            arguments={"query": "SELECT * FROM users LIMIT 10;"},
        )
        self.assertFalse(verdict_2.allowed)
        self.assertEqual(verdict_2.risk_level, "CRITICAL")
        self.assertIn("Emergency kill switch tripped", verdict_2.reason)

    def test_runtime_alias_import(self) -> None:
        """agentwatch.runtime.killswitch re-exports identical classes."""
        self.assertIs(RuntimeEmergencyKillSwitch, EmergencyKillSwitch)
        self.assertIs(RuntimeEmergencyStopTriggered, EmergencyStopTriggered)
