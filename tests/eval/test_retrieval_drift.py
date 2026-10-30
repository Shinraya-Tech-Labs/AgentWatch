"""Unit tests for Vector RAG retrieval drift and precision collapse detector."""

from __future__ import annotations

import unittest

from agentwatch.eval import (
    ALERT_RETRIEVAL_PRECISION_DRIFT,
    RetrievalDriftDetector,
)
from agentwatch.eval.retrieval_drift import WelfordAccumulator


class TestRetrievalDriftDetector(unittest.TestCase):
    def test_welford_accumulator(self) -> None:
        acc = WelfordAccumulator()
        values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        for v in values:
            acc.update(v)

        self.assertEqual(acc.count, len(values))
        self.assertAlmostEqual(acc.mean, 5.0, places=4)
        self.assertAlmostEqual(acc.variance, 4.5714, places=3)
        self.assertAlmostEqual(acc.stdev, 2.138, places=3)

    def test_baseline_establishment_window(self) -> None:
        detector = RetrievalDriftDetector(baseline_window=50)
        self.assertFalse(detector.baseline_established)

        for _ in range(49):
            report = detector.ingest([0.85, 0.87, 0.86])
            self.assertEqual(report.status, "BASELINE_ESTABLISHING")
            self.assertFalse(report.drift_detected)
            self.assertFalse(detector.baseline_established)

        # 50th query marks the completion of the baseline window
        report = detector.ingest([0.85, 0.87, 0.86])
        self.assertTrue(detector.baseline_established)
        self.assertIsNotNone(report.baseline_mean)
        self.assertIsNotNone(report.baseline_std)

    def test_steady_baseline_no_drift(self) -> None:
        detector = RetrievalDriftDetector(baseline_window=50, sigma_threshold=2.5)

        # 50 baseline queries around 0.85 +- 0.02
        for q in range(50):
            scores = [0.85 + ((q + i) % 5 - 2) * 0.01 for i in range(5)]
            detector.ingest(scores)

        # Another 50 queries within identical distribution
        for q in range(50):
            scores = [0.85 + ((q + i) % 5 - 2) * 0.01 for i in range(5)]
            report = detector.ingest(scores)
            self.assertEqual(report.status, "STABLE")
            self.assertFalse(report.drift_detected)
            self.assertIsNone(report.alert_code)

        self.assertFalse(detector.is_drifted)

    def test_synthetic_decaying_scores_trigger_drift(self) -> None:
        detector = RetrievalDriftDetector(baseline_window=50, sigma_threshold=2.5)

        # 50 baseline queries around 0.90 +- 0.01
        for q in range(50):
            scores = [0.90 + ((q + i) % 3 - 1) * 0.01 for i in range(5)]
            detector.ingest(scores)

        # Sharp retrieval precision collapse: queries drop to 0.50
        report = detector.ingest([0.50, 0.51, 0.49])
        self.assertTrue(report.drift_detected)
        self.assertEqual(report.status, ALERT_RETRIEVAL_PRECISION_DRIFT)
        self.assertEqual(report.alert_code, ALERT_RETRIEVAL_PRECISION_DRIFT)
        self.assertGreater(report.z_score, 2.5)
        self.assertTrue(detector.is_drifted)

    def test_event_dict_ingestion(self) -> None:
        detector = RetrievalDriftDetector(baseline_window=10)
        event = {
            "kind": "RETRIEVAL",
            "attributes": {
                "similarity_scores": [0.91, 0.93, 0.89],
            },
        }
        report = detector.ingest(event)
        self.assertAlmostEqual(report.query_mean, 0.91, places=3)
        self.assertEqual(report.query_count, 1)

    def test_computational_event_mock_ingestion(self) -> None:
        class MockEvent:
            def __init__(self, scores: list[float]) -> None:
                self.attributes = {"top_k_scores": scores}

        detector = RetrievalDriftDetector(baseline_window=10)
        event = MockEvent([0.80, 0.82, 0.84])
        report = detector.ingest(event)
        self.assertAlmostEqual(report.query_mean, 0.82, places=3)

    def test_single_scalar_score_ingestion(self) -> None:
        detector = RetrievalDriftDetector(baseline_window=5)
        report = detector.ingest(0.77)
        self.assertAlmostEqual(report.query_mean, 0.77, places=3)

    def test_distance_metric_drift(self) -> None:
        # With is_distance=True, higher distance signifies precision collapse
        detector = RetrievalDriftDetector(baseline_window=5, sigma_threshold=2.5, is_distance=True)
        for _ in range(5):
            detector.ingest([0.10, 0.12, 0.11])

        # Sudden increase in distance -> drift
        report = detector.ingest([0.85, 0.90, 0.88])
        self.assertTrue(report.drift_detected)
        self.assertEqual(report.status, ALERT_RETRIEVAL_PRECISION_DRIFT)

    def test_zero_variance_baseline_handling(self) -> None:
        detector = RetrievalDriftDetector(baseline_window=5)
        for _ in range(5):
            detector.ingest([1.0, 1.0])  # zero variance
        # Same as baseline -> z_score 0.0 and no drift
        report_same = detector.ingest([1.0, 1.0])
        self.assertEqual(report_same.z_score, 0.0)
        self.assertFalse(report_same.drift_detected)

        # Decayed score -> inf z_score and drift flagged
        report_drift = detector.ingest([0.5, 0.5])
        self.assertEqual(report_drift.z_score, float("inf"))
        self.assertTrue(report_drift.drift_detected)

    def test_reset_clears_state(self) -> None:
        detector = RetrievalDriftDetector(baseline_window=5)
        for _ in range(5):
            detector.ingest([0.9, 0.9])
        self.assertTrue(detector.baseline_established)
        detector.reset()
        self.assertFalse(detector.baseline_established)
        self.assertFalse(detector.is_drifted)

    def test_empty_or_invalid_scores_raises_value_error(self) -> None:
        detector = RetrievalDriftDetector()
        with self.assertRaises(ValueError):
            detector.ingest([])
        with self.assertRaises(ValueError):
            detector.ingest({"attributes": {}})


if __name__ == "__main__":
    unittest.main()
