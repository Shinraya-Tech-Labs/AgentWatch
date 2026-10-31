"""Isolated Ground-Truth Oracle & Closed-World Baseline (Issue #737 / AWBench-Sufficiency).

Maintains uncorrupted ground-truth labels in an isolated oracle inaccessible to
trace evaluators, and provides a closed-world baseline evaluator for differential benchmarking.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from agentwatch.contracts.schema import EvidenceVerdict
from agentwatch.events.model import ComputationalEvent


@dataclass(frozen=True, slots=True)
class GroundTruthRecord:
    """Isolated ground-truth invariant state for a single workflow execution."""

    run_id: str
    invariant_holds: bool  # True if the workflow legitimately respected the contract
    required_operation: str
    target_operation: str
    required_event_id: str | None
    is_telemetry_complete: bool  # False if perturbation dropped required telemetry or sensor
    integrity_hash: str


class GroundTruthOracle:
    """Isolated evaluation oracle holding ground-truth violation and satisfaction labels.

    Evaluator implementations MUST NOT import or query this oracle during trace verification.
    Only the statistical metric scoring harness consults the oracle.
    """

    def __init__(self) -> None:
        self._records: dict[str, GroundTruthRecord] = {}

    def register_case(
        self,
        run_id: str,
        invariant_holds: bool,
        required_op: str,
        target_op: str,
        required_event_id: str | None,
        is_telemetry_complete: bool,
    ) -> str:
        # Compute SHA-256 integrity digest to verify isolation
        payload = f"{run_id}:{invariant_holds}:{required_op}:{target_op}:{required_event_id}:{is_telemetry_complete}"
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        record = GroundTruthRecord(
            run_id=run_id,
            invariant_holds=invariant_holds,
            required_operation=required_op,
            target_operation=target_op,
            required_event_id=required_event_id,
            is_telemetry_complete=is_telemetry_complete,
            integrity_hash=digest,
        )
        self._records[run_id] = record
        return digest

    def get_record(self, run_id: str) -> GroundTruthRecord | None:
        """Consulted strictly by post-hoc metric scoring harness."""
        return self._records.get(run_id)

    def is_answerable(self, run_id: str) -> bool:
        """Trace is answerable if the telemetry required to evaluate the invariant was preserved."""
        rec = self._records.get(run_id)
        if not rec:
            return False
        # If true violation occurred (e.g. transfer without approval), the absence of approval is real!
        if not rec.invariant_holds:
            return True
        # If invariant held, it is only answerable if required telemetry was NOT dropped
        return rec.is_telemetry_complete


class ClosedWorldBaselineEvaluator:
    """Naive closed-world contract checker.

    Assumes closed-world completion: if a required event is not observed in the ingested
    event set, it unconditionally concludes the contract was violated (REFUTED).
    Under packet loss or missing sensors, this produces high false certainty.
    """

    def evaluate(
        self,
        events: Sequence[ComputationalEvent],
        target_operation: str,
        required_operation: str,
        join_key: str = "transaction_id",
    ) -> EvidenceVerdict:
        target_events = [e for e in events if e.operation == target_operation]
        if not target_events:
            return EvidenceVerdict.SUPPORTED

        target = target_events[0]
        join_val = target.attributes.get(join_key)

        required_events = [
            e for e in events
            if e.operation == required_operation and (join_val is None or e.attributes.get(join_key) == join_val)
        ]

        if required_events:
            return EvidenceVerdict.SUPPORTED
        # Closed-world assumption: absence of evidence is treated as evidence of absence!
        return EvidenceVerdict.REFUTED
