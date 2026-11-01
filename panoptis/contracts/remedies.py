"""Remedy generator for instrumentation gaps.

Maps detected missing facts directly to actionable SDK/sensor recommendations.
"""

from __future__ import annotations

from panoptis.contracts.schema import MissingFact, MissingReason, Recoverability


def get_remedy_for_missing_fact(fact: MissingFact) -> str:
    """Generate a human-actionable instrumentation suggestion for a missing fact.

    ponytail: uses declarative string mapping rather than a dynamic planner.
    Covers the 8 standard missingness cases with zero runtime overhead.
    """
    if fact.recoverability == Recoverability.FUNDAMENTAL_LIMIT:
        return f"Fact '{fact.field_name}' represents unobservable internal state; cannot be instrumented."

    if fact.reason == MissingReason.SENSOR_DISABLED:
        sensors = ", ".join(fact.candidate_sensors) if fact.candidate_sensors else "relevant sensor"
        return f"Enable capture sensor '{sensors}' for operation '{fact.required_for}'."

    if fact.reason == MissingReason.FIELD_ABSENT:
        return f"Configure sensor for '{fact.required_for}' to include correlation attribute '{fact.field_name}'."

    if fact.reason == MissingReason.SPAN_ORPHAN:
        return "Ensure distributed context propagation (W3C traceparent) is active across service boundaries."

    if fact.reason == MissingReason.SAMPLED_OUT:
        sensors = ", ".join(fact.candidate_sensors) if fact.candidate_sensors else "relevant sensor"
        return f"Increase telemetry sampling rate or configure tail-based sampling rule for operation '{fact.required_for}' on sensor '{sensors}'."

    if fact.reason == MissingReason.SEQUENCE_GAP:
        sensors = ", ".join(fact.candidate_sensors) if fact.candidate_sensors else "relevant sensor"
        return f"Sequence counter gap detected on sensor '{sensors}' for operation '{fact.required_for}'; inspect network transport, socket buffers, or message queue drop counters."

    if fact.reason == MissingReason.CLOCK_AMBIGUITY:
        return "Synchronize host clocks (NTP/PTP) or rely on explicit parent-child span links instead of wall clock."

    if fact.reason == MissingReason.REDACTED:
        return f"Attribute '{fact.field_name}' was redacted by privacy policy; configure an anonymized hash token instead."

    return f"Instrument field '{fact.field_name}' on events providing '{fact.required_for}'."
