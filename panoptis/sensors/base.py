"""Sensor protocol, sensor context and observation sinks."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol

from panoptis.evidence.canonical import canonical_json
from panoptis.evidence.model import ClockInfo, ObservationDraft, SensorRef

logger = logging.getLogger(__name__)


class ObservationSink(Protocol):
    def emit(self, draft: ObservationDraft) -> None:
        """Accept an observation. Must not block for long and must not raise."""

    def flush(self) -> None: ...


class SensorContext:
    """Per-sensor-instance state: identity and a monotonic sequence counter."""

    def __init__(
        self,
        sensor_type: str,
        sensor_version: str,
        sink: ObservationSink,
        tenant_id: str = "default",
    ) -> None:
        self.ref = SensorRef(sensor_type, sensor_version, f"{sensor_type}-{uuid.uuid4().hex[:12]}")
        self.sink = sink
        self.tenant_id = tenant_id
        self._seq = 0
        self._lock = threading.Lock()
        self.errors = 0

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def emit(
        self,
        source_kind: str,
        payload: Any,
        *,
        declared_ids: dict[str, str | None] | None = None,
        observed_at: datetime | None = None,
        clock_source: str | None = None,
    ) -> None:
        """Build a draft and emit it. Never raises into the caller."""
        try:
            now = datetime.now(UTC)
            ids = {k: str(v) for k, v in (declared_ids or {}).items() if v not in (None, "")}
            draft = ObservationDraft(
                sensor=self.ref,
                source_kind=source_kind,
                payload=payload,
                source_seq=self.next_seq(),
                observed_at=observed_at or now,
                clock=ClockInfo(
                    source=clock_source or ("source" if observed_at else "sensor"),
                    clock_id=self.ref.instance_id,
                ),
                declared_ids=ids,
                tenant_id=self.tenant_id,
            )
            self.sink.emit(draft)
        except Exception:  # pragma: no cover - defensive: never break the host
            self.errors += 1
            logger.debug("sensor emit failed", exc_info=True)


class Sensor:
    """Base class for sensors."""

    sensor_type: ClassVar[str] = "base"
    version: ClassVar[str] = "1"

    def __init__(self, sink: ObservationSink, tenant_id: str = "default") -> None:
        self.ctx = SensorContext(self.sensor_type, self.version, sink, tenant_id)


class ListSink:
    """Collects drafts in memory (tests, embedded use)."""

    def __init__(self) -> None:
        self.drafts: list[ObservationDraft] = []
        self._lock = threading.Lock()

    def emit(self, draft: ObservationDraft) -> None:
        with self._lock:
            self.drafts.append(draft)

    def flush(self) -> None:
        return None


class FileSink:
    """Appends drafts as NDJSON lines; ``panoptis ingest`` imports the file later."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, draft: ObservationDraft) -> None:
        line = canonical_json(draft.to_dict())
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def flush(self) -> None:
        return None


def read_ndjson(path: str | Path) -> list[ObservationDraft]:
    drafts = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                drafts.append(ObservationDraft.from_dict(json.loads(line)))
    return drafts


class HttpSink:
    """Batches drafts and POSTs them to ``/api/v3/observations`` from a background thread.

    Bounded buffer: when full, the oldest drafts are dropped and counted. Drops are
    reported, never silent (see :attr:`dropped`).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        max_buffer: int = 10_000,
        batch_size: int = 200,
        interval_s: float = 1.0,
    ) -> None:
        self.url = base_url.rstrip("/") + "/api/v3/observations"
        self.api_key = api_key
        self.batch_size = batch_size
        self.interval_s = interval_s
        self._buf: deque[ObservationDraft] = deque(maxlen=max_buffer)
        self._cv = threading.Condition()
        self.dropped = 0
        self.sent = 0
        self.failed_batches = 0
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="panoptis-http-sink", daemon=True)
        self._thread.start()

    def emit(self, draft: ObservationDraft) -> None:
        with self._cv:
            if len(self._buf) == self._buf.maxlen:
                self.dropped += 1
            self._buf.append(draft)
            if len(self._buf) >= self.batch_size:
                self._cv.notify()

    def _take(self) -> list[ObservationDraft]:
        with self._cv:
            n = min(self.batch_size, len(self._buf))
            return [self._buf.popleft() for _ in range(n)]

    def _send(self, batch: list[ObservationDraft]) -> None:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        body = {"observations": [d.to_dict() for d in batch]}
        try:
            resp = httpx.post(self.url, content=canonical_json(body), headers=headers, timeout=10.0)
            resp.raise_for_status()
            self.sent += len(batch)
        except Exception:
            self.failed_batches += 1
            self.dropped += len(batch)
            logger.debug("HttpSink batch failed", exc_info=True)

    def _run(self) -> None:
        while True:
            with self._cv:
                if not self._stop and len(self._buf) < self.batch_size:
                    self._cv.wait(self.interval_s)
                stop = self._stop
            batch = self._take()
            if batch:
                self._send(batch)
            if stop and not self._buf:
                return

    def flush(self) -> None:
        while True:
            batch = self._take()
            if not batch:
                return
            self._send(batch)

    def close(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()
        self._thread.join(timeout=10)
        self.flush()
