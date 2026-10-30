"""
Detect one entitlement being used across multiple devices (issue #463).

Under our bearer-token entitlement model, a token can be copied to other machines
along with its associated metadata machine ID. This tracker records the device
fingerprints reported during entitlement use and flags when the same subject/token
appears on more distinct device fingerprints than allowed within a time window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

__all__ = ["AbuseEvent", "EntitlementUsageTracker"]

_DEFAULT_MAX_DEVICES = 1
_DEFAULT_WINDOW = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class AbuseEvent:
    subject: str
    machine_ids: tuple[str, ...]
    window_seconds: int
    detected_at: datetime

    @property
    def distinct_devices(self) -> int:
        return len(self.machine_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "machine_ids": list(self.machine_ids),
            "distinct_devices": self.distinct_devices,
            "window_seconds": self.window_seconds,
            "detected_at": self.detected_at.isoformat(),
        }


class EntitlementUsageTracker:
    """Record (subject, machine_id) sightings and flag cross-device usage."""

    def __init__(
        self,
        *,
        max_devices: int = _DEFAULT_MAX_DEVICES,
        window: timedelta = _DEFAULT_WINDOW,
    ) -> None:
        if max_devices < 1:
            raise ValueError("max_devices must be at least 1")
        self._max_devices = max_devices
        self._window = window
        self._sightings: dict[str, dict[str, datetime]] = {}
        self._alerted: dict[str, frozenset[str]] = {}

    def record(
        self, subject: str, machine_id: str, *, now: datetime | None = None
    ) -> AbuseEvent | None:
        """Record a sighting; return an AbuseEvent when newly flagged, else None."""
        moment = now or datetime.now(UTC)
        self._sightings.setdefault(subject, {})[machine_id] = moment
        self._prune(subject, moment)

        active = frozenset(self._sightings.get(subject, {}))
        if len(active) <= self._max_devices:
            self._alerted.pop(subject, None)
            return None
        if self._alerted.get(subject) == active:
            return None  # same offending set already reported

        self._alerted[subject] = active
        return AbuseEvent(
            subject=subject,
            machine_ids=tuple(sorted(active)),
            window_seconds=int(self._window.total_seconds()),
            detected_at=moment,
        )

    def active_devices(self, subject: str, *, now: datetime | None = None) -> set[str]:
        self._prune(subject, now or datetime.now(UTC))
        return set(self._sightings.get(subject, {}))

    def reset(self) -> None:
        self._sightings.clear()
        self._alerted.clear()

    def _prune(self, subject: str, now: datetime) -> None:
        devices = self._sightings.get(subject)
        if not devices:
            return
        cutoff = now - self._window
        for mid in [m for m, seen in devices.items() if seen < cutoff]:
            del devices[mid]
        if not devices:
            del self._sightings[subject]
