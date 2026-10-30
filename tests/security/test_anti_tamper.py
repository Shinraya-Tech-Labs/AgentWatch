"""Unit tests for AntiTamperShield in Panopti & AgentWatch."""

from __future__ import annotations

import os
import unittest

from agentwatch.security import AntiTamperShield as AWShield
from agentwatch.security import TamperAttemptBlocked as AWTamperAttemptBlocked
from panopti.shield import AntiTamperShield, TamperAttemptBlocked


class TestAntiTamperShield(unittest.TestCase):
    def setUp(self) -> None:
        self.shield = AntiTamperShield()

    def test_empty_or_whitespace_commands(self) -> None:
        self.assertFalse(self.shield.is_tampering_attempt(""))
        self.assertFalse(self.shield.is_tampering_attempt("   \n\t"))

    def test_pkill_panopti_intercepted(self) -> None:
        self.assertTrue(self.shield.is_tampering_attempt("pkill -9 panopti"))
        self.assertTrue(self.shield.is_tampering_attempt("pkill panopti"))

    def test_kill_pgrep_agentwatch_intercepted(self) -> None:
        self.assertTrue(self.shield.is_tampering_attempt("kill $(pgrep -f agentwatch)"))
        self.assertTrue(self.shield.is_tampering_attempt("kill `pgrep agentwatch`"))

    def test_killall_agentwatch_intercepted(self) -> None:
        self.assertTrue(self.shield.is_tampering_attempt("killall agentwatch"))
        self.assertTrue(self.shield.is_tampering_attempt("killall -9 agentwatch"))

    def test_guardian_current_pid_kill_intercepted(self) -> None:
        current_pid = os.getpid()
        self.assertTrue(self.shield.is_tampering_attempt(f"kill -9 {current_pid}"))
        self.assertTrue(self.shield.is_tampering_attempt(f"kill {current_pid}"))
        self.assertTrue(self.shield.is_tampering_attempt(f"taskkill /PID {current_pid} /F"))

    def test_custom_target_pid_kill_intercepted(self) -> None:
        shield = AntiTamperShield(target_pids={4242, 8484})
        self.assertTrue(shield.is_tampering_attempt("kill 4242"))
        self.assertTrue(shield.is_tampering_attempt("kill -9 8484"))
        self.assertFalse(shield.is_tampering_attempt("kill 9999"))

    def test_rm_panopti_db_intercepted(self) -> None:
        self.assertTrue(self.shield.is_tampering_attempt("rm -f panopti.db"))
        self.assertTrue(self.shield.is_tampering_attempt("rm panopti.db"))
        self.assertTrue(self.shield.is_tampering_attempt("rm -rf /data/panopti.db"))

    def test_rm_rf_agentwatch_jsonl_intercepted(self) -> None:
        self.assertTrue(self.shield.is_tampering_attempt("rm -f /var/log/agentwatch.jsonl"))
        self.assertTrue(self.shield.is_tampering_attempt("unlink agentwatch.jsonl"))

    def test_truncate_agentwatch_jsonl_intercepted(self) -> None:
        self.assertTrue(self.shield.is_tampering_attempt("truncate -s 0 /var/log/agentwatch.jsonl"))
        self.assertTrue(self.shield.is_tampering_attempt("truncate -s 0 panopti.log"))

    def test_shred_panopti_db_intercepted(self) -> None:
        self.assertTrue(self.shield.is_tampering_attempt("shred panopti.db"))
        self.assertTrue(self.shield.is_tampering_attempt("shred -u panopti.db"))

    def test_shell_redirection_panopti_db_intercepted(self) -> None:
        self.assertTrue(self.shield.is_tampering_attempt("> panopti.db"))
        self.assertTrue(self.shield.is_tampering_attempt("echo '' > agentwatch.jsonl"))

    def test_benign_dev_command_allowed(self) -> None:
        self.assertFalse(self.shield.is_tampering_attempt("kill -9 $TEST_PID"))
        self.assertFalse(self.shield.is_tampering_attempt("kill 9999999"))

    def test_benign_file_deletion_allowed(self) -> None:
        self.assertFalse(self.shield.is_tampering_attempt("rm my_test_file.txt"))
        self.assertFalse(self.shield.is_tampering_attempt("rm -rf build/ dist/"))
        self.assertFalse(self.shield.is_tampering_attempt("echo 'hello' > log.txt"))

    def test_benign_read_command_allowed(self) -> None:
        self.assertFalse(self.shield.is_tampering_attempt("cat /var/log/agentwatch.jsonl"))
        self.assertFalse(self.shield.is_tampering_attempt("tail -f panopti.log"))
        self.assertFalse(
            self.shield.is_tampering_attempt("sqlite3 panopti.db 'SELECT * FROM events;'")
        )

    def test_benign_run_command_allowed(self) -> None:
        self.assertFalse(self.shield.is_tampering_attempt("python -m panopti demo"))
        self.assertFalse(self.shield.is_tampering_attempt("pytest tests/"))

    def test_verify_raises_tamper_attempt_blocked(self) -> None:
        with self.assertRaises(TamperAttemptBlocked):
            self.shield.verify("pkill -9 panopti")
        # Should not raise for safe commands
        self.shield.verify("ls -la")

    def test_guard_decorator_execution(self) -> None:
        executed: list[str] = []

        @self.shield.guard
        def execute_cmd(command: str) -> str:
            executed.append(command)
            return "SUCCESS"

        self.assertEqual(execute_cmd("echo harmless"), "SUCCESS")
        self.assertEqual(executed, ["echo harmless"])

        with self.assertRaises(TamperAttemptBlocked):
            execute_cmd("rm panopti.db")

    def test_agentwatch_security_import_alias(self) -> None:
        self.assertIs(AWShield, AntiTamperShield)
        self.assertIs(AWTamperAttemptBlocked, TamperAttemptBlocked)


if __name__ == "__main__":
    unittest.main()
