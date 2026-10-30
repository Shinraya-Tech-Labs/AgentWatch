"""Unit tests for working-memory mutation and context snapshot diffing."""

from __future__ import annotations

import unittest

from agentwatch.memory import (
    MemoryDelta,
    MemoryEngine,
    MemorySnapshot,
    compute_memory_delta,
)


class TestMemoryDiff(unittest.TestCase):
    def test_immutable_snapshot_hash_consistency(self) -> None:
        snap1 = MemorySnapshot(
            turn_id=1,
            timestamp="2026-10-30T10:00:00Z",
            state={"query": "test", "role": "assistant"},
            scratchpad="Thought 1\nThought 2",
        )
        snap2 = MemorySnapshot(
            turn_id=1,
            timestamp="2026-10-30T10:00:00Z",
            state={"role": "assistant", "query": "test"},  # keys reordered
            scratchpad="Thought 1\nThought 2",
        )
        # Deterministic canonical serialization must produce identical SHA-256 hashes
        self.assertEqual(snap1.sha256_hash, snap2.sha256_hash)
        self.assertTrue(len(snap1.sha256_hash) == 64)

        # Frozen immutability check
        with self.assertRaises(Exception):
            snap1.turn_id = 2  # type: ignore[misc]

    def test_added_and_removed_context(self) -> None:
        before = MemorySnapshot(
            turn_id=1,
            timestamp="2026-10-30T10:00:00Z",
            state={"system_prompt": "act helpful", "old_context": "deprecated chunk"},
        )
        after = MemorySnapshot(
            turn_id=2,
            timestamp="2026-10-30T10:01:00Z",
            state={"system_prompt": "act helpful", "new_context": "fresh retrieved chunk"},
        )

        delta = compute_memory_delta(before, after)
        self.assertEqual(delta.turn_id_before, 1)
        self.assertEqual(delta.turn_id_after, 2)
        self.assertEqual(delta.added_keys, ["new_context"])
        self.assertEqual(delta.removed_keys, ["old_context"])
        self.assertEqual(delta.modified_values, {})
        self.assertTrue(delta.has_mutations)

    def test_modified_variables(self) -> None:
        before = MemorySnapshot(
            turn_id=1,
            timestamp="2026-10-30T10:00:00Z",
            state={"attempt_count": 1, "confidence": 0.95},
        )
        after = MemorySnapshot(
            turn_id=2,
            timestamp="2026-10-30T10:01:00Z",
            state={"attempt_count": 2, "confidence": 0.80},
        )

        delta = compute_memory_delta(before, after)
        self.assertEqual(delta.added_keys, [])
        self.assertEqual(delta.removed_keys, [])
        self.assertEqual(
            delta.modified_values,
            {
                "attempt_count": (1, 2),
                "confidence": (0.95, 0.80),
            },
        )
        self.assertTrue(delta.has_mutations)

    def test_precise_scratchpad_line_diff(self) -> None:
        before_text = "Step 1: Discover\nStep 2: Plan\nStep 3: Execute\n"
        after_text = "Step 1: Discover\nStep 2: Plan Revision\nStep 3: Execute\nStep 4: Verify\n"

        before = MemorySnapshot(
            turn_id=3, timestamp="2026-10-30T10:00:00Z", state={}, scratchpad=before_text
        )
        after = MemorySnapshot(
            turn_id=4, timestamp="2026-10-30T10:01:00Z", state={}, scratchpad=after_text
        )

        delta = compute_memory_delta(before, after)
        self.assertTrue(delta.has_mutations)
        diff_str = "".join(delta.scratchpad_diff)
        self.assertIn("-Step 2: Plan\n", diff_str)
        self.assertIn("+Step 2: Plan Revision\n", diff_str)
        self.assertIn("+Step 4: Verify\n", diff_str)

    def test_identical_snapshot_no_mutation(self) -> None:
        snap = MemorySnapshot(
            turn_id=1,
            timestamp="2026-10-30T10:00:00Z",
            state={"key": "val"},
            scratchpad="hello",
        )
        delta = compute_memory_delta(snap, snap)
        self.assertFalse(delta.has_mutations)
        self.assertEqual(delta.added_keys, [])
        self.assertEqual(delta.removed_keys, [])
        self.assertEqual(delta.modified_values, {})
        self.assertEqual(delta.scratchpad_diff, [])
        self.assertEqual(delta.before_hash, delta.after_hash)

    def test_backwards_turn_id_raises_value_error(self) -> None:
        snap_turn_5 = MemorySnapshot(turn_id=5, timestamp="2026-10-30T10:00:00Z", state={})
        snap_turn_2 = MemorySnapshot(turn_id=2, timestamp="2026-10-30T10:01:00Z", state={})

        with self.assertRaises(ValueError):
            compute_memory_delta(snap_turn_5, snap_turn_2)

    def test_memory_engine_integration(self) -> None:
        engine = MemoryEngine()
        snap1 = engine.create_snapshot(
            turn_id=1, scratchpad="Init", extra_state={"active_goal": "build"}
        )
        snap2 = engine.create_snapshot(
            turn_id=2, scratchpad="Init\nDone", extra_state={"active_goal": "test"}
        )

        delta = engine.record_mutation(snap1, snap2)
        self.assertIsInstance(delta, MemoryDelta)
        self.assertEqual(delta.modified_values, {"active_goal": ("build", "test")})
        self.assertTrue(delta.has_mutations)


if __name__ == "__main__":
    unittest.main()
