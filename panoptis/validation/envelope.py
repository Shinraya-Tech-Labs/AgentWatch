"""Pre-flight event envelope and monotonic timestamp invariant validator."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

_REQUIRED_FIELDS = ("event_id", "operation", "kind", "tenant_id", "interp_id", "normalizer")

JITTER_TOLERANCE_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class ValidationWarning:
    code: str
    message: str
    event_id: str | None = None


@dataclass(slots=True)
class ValidationReport:
    total: int = 0
    warnings: list[ValidationWarning] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.warnings


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    elif isinstance(value, dict):
        start = value.get("start")
        return _parse_timestamp(start)
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def validate_event_stream(
    events: Iterable[dict[str, Any]],
    jitter_tolerance_seconds: float = JITTER_TOLERANCE_SECONDS,
) -> ValidationReport:
    report = ValidationReport()
    seen_ids: set[str] = set()
    last_by_stream: dict[str, datetime] = defaultdict(lambda: None)  # type: ignore[arg-type]

    for raw in events:
        report.total += 1
        eid = raw.get("event_id") if isinstance(raw, dict) else None

        if not isinstance(raw, dict):
            report.warnings.append(
                ValidationWarning("MISSING_FIELD", "Event is not a dict envelope.", None)
            )
            continue

        for field_name in _REQUIRED_FIELDS:
            if raw.get(field_name) is None:
                report.warnings.append(
                    ValidationWarning(
                        "MISSING_FIELD",
                        f"Missing required field '{field_name}'.",
                        eid,
                    )
                )

        if eid is not None:
            if eid in seen_ids:
                report.warnings.append(
                    ValidationWarning(
                        "DUPLICATE_EVENT_ID",
                        f"Duplicate event_id '{eid}'.",
                        eid,
                    )
                )
            seen_ids.add(eid)

        stream_key = (
            raw.get("thread_id") or raw.get("stream_id") or raw.get("ordering_key") or "default"
        )
        # try attributes
        attrs = raw.get("attributes")
        if isinstance(attrs, dict):
            stream_key = attrs.get("thread_id", stream_key) or stream_key

        ts = _parse_timestamp(raw.get("time", raw.get("timestamp")))
        if ts is not None:
            prev = last_by_stream.get(stream_key)
            if prev is not None:
                delta = (ts - prev).total_seconds()
                if delta < -jitter_tolerance_seconds:
                    report.warnings.append(
                        ValidationWarning(
                            "CLOCK_SKEW_DETECTED",
                            f"Retrocausal delta {delta:.3f}s on stream '{stream_key}'.",
                            eid,
                        )
                    )
            last_by_stream[stream_key] = ts if (prev is None or ts >= prev) else prev

    return report
