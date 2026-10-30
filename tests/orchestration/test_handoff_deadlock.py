"""Unit tests for directed handoff cycle and multi-agent delegation deadlock detector."""

from __future__ import annotations

import unittest

from agentwatch.orchestration import (
    ALERT_AGENT_DELEGATION_DEADLOCK,
    HandoffGraphTracker,
)


class TestHandoffDeadlock(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = HandoffGraphTracker()

    def test_two_node_deadlock_cycle(self) -> None:
        # A delegates to B
        rep1 = self.tracker.record_delegation("Researcher", "Coder", task_id="task_1")
        self.assertFalse(rep1.deadlock_detected)

        # B delegates back to A -> A -> B -> A cycle!
        rep2 = self.tracker.record_delegation("Coder", "Researcher", task_id="task_2")
        self.assertTrue(rep2.deadlock_detected)
        self.assertEqual(rep2.alert_code, ALERT_AGENT_DELEGATION_DEADLOCK)
        self.assertEqual(rep2.cycle, ["Coder", "Researcher", "Coder"])
        self.assertIn("Coder -> Researcher -> Coder", rep2.cycle_str)

    def test_three_node_deadlock_cycle(self) -> None:
        # A -> B -> C -> A
        self.tracker.record_delegation("AgentA", "AgentB", task_id="t1")
        self.tracker.record_delegation("AgentB", "AgentC", task_id="t2")
        rep = self.tracker.record_delegation("AgentC", "AgentA", task_id="t3")

        self.assertTrue(rep.deadlock_detected)
        self.assertEqual(rep.alert_code, ALERT_AGENT_DELEGATION_DEADLOCK)
        self.assertEqual(rep.cycle, ["AgentA", "AgentB", "AgentC", "AgentA"])

    def test_valid_fan_out_tree_no_deadlock(self) -> None:
        # Leader delegates to Worker1, Worker2, Worker3
        self.tracker.record_delegation("Leader", "Worker1", task_id="w1")
        self.tracker.record_delegation("Leader", "Worker2", task_id="w2")
        rep = self.tracker.record_delegation("Leader", "Worker3", task_id="w3")

        self.assertFalse(rep.deadlock_detected)
        self.assertFalse(self.tracker.detect_cycles().deadlock_detected)

    def test_valid_fan_in_dag_no_deadlock(self) -> None:
        # Worker1 and Worker2 delegate outputs to Synthesizer (DAG, acyclic)
        self.tracker.record_delegation("Worker1", "Synthesizer", task_id="s1")
        rep = self.tracker.record_delegation("Worker2", "Synthesizer", task_id="s2")

        self.assertFalse(rep.deadlock_detected)
        self.assertFalse(self.tracker.detect_cycles().deadlock_detected)

    def test_progress_prevents_false_deadlock(self) -> None:
        # A delegates to B with task_1
        self.tracker.record_delegation("A", "B", task_id="task_1")
        # Task 1 finishes and produces state progress
        self.tracker.record_progress("task_1")

        # Now B initiates a new task back to A -> not a deadlock because task_1 resolved
        rep = self.tracker.record_delegation("B", "A", task_id="task_2")
        self.assertFalse(rep.deadlock_detected)

    def test_resolve_delegation_clears_cycle(self) -> None:
        self.tracker.record_delegation("AgentX", "AgentY", task_id="xy")
        rep1 = self.tracker.record_delegation("AgentY", "AgentX", task_id="yx")
        self.assertTrue(rep1.deadlock_detected)

        # Resolve edge AgentY -> AgentX
        self.tracker.resolve_delegation("AgentY", "AgentX", task_id="yx")
        rep2 = self.tracker.detect_cycles()
        self.assertFalse(rep2.deadlock_detected)

    def test_clear_resets_tracker(self) -> None:
        self.tracker.record_delegation("A", "B")
        self.tracker.record_delegation("B", "A")
        self.assertTrue(self.tracker.detect_cycles().deadlock_detected)

        self.tracker.clear()
        self.assertFalse(self.tracker.detect_cycles().deadlock_detected)


if __name__ == "__main__":
    unittest.main()
