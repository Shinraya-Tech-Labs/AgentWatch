"""Asynchronous incremental contract evaluation stream processor (Issue #731).

Provides bounded async queue execution, backpressure handling, worker lifecycle,
graceful cancellation, and tenant-scoped assessment job management.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Sequence
from datetime import UTC, datetime
from typing import Any

from agentwatch.contracts.incremental import IncrementalContractEvaluator
from agentwatch.contracts.schema import (
    AssessmentStatus,
    CoverageSnapshot,
    EvidenceAssessment,
    EvidenceContract,
)
from agentwatch.events.model import ComputationalEvent
from agentwatch.evidence.ids import new_ulid

logger = logging.getLogger(__name__)

_SENTINEL = object()


class AsyncContractStreamProcessor:
    """Asynchronously processes a stream of ComputationalEvents using a bounded queue."""

    def __init__(
        self,
        contract: EvidenceContract,
        coverage: Sequence[CoverageSnapshot] = (),
        queue_capacity: int = 1_000,
        max_buffer_size: int = 10_000,
    ) -> None:
        self.contract = contract
        self.coverage = tuple(coverage)
        self.queue_capacity = queue_capacity
        self.evaluator = IncrementalContractEvaluator(
            contract, coverage=self.coverage, max_buffer_size=max_buffer_size
        )

        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_capacity)
        self._worker_task: asyncio.Task[None] | None = None
        self._status = AssessmentStatus.PENDING
        self._final_assessment: EvidenceAssessment | None = None
        self._error: str | None = None
        self._listeners: list[Callable[[dict[str, Any]], Coroutine[Any, Any, None]]] = []

    @property
    def status(self) -> AssessmentStatus:
        return self._status

    @property
    def is_cancelled(self) -> bool:
        return self._status == AssessmentStatus.CANCELLED

    @property
    def events_evaluated(self) -> int:
        return self.evaluator.events_evaluated

    def subscribe(self, listener: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        self._listeners.append(listener)

    async def _notify_progress(self) -> None:
        if not self._listeners:
            return
        payload = self.get_status()
        for listener in list(self._listeners):
            try:
                await listener(payload)
            except Exception:
                logger.debug("Progress listener notification failed", exc_info=True)

    async def start(self) -> None:
        """Starts the background processing worker."""
        if self._worker_task is not None:
            return
        self._status = AssessmentStatus.RUNNING
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is _SENTINEL:
                    self._queue.task_done()
                    break
                try:
                    self.evaluator.process_event(item)
                    if self.evaluator.events_evaluated % 50 == 0:
                        await self._notify_progress()
                finally:
                    self._queue.task_done()

            # Finalize evaluator
            self._final_assessment = self.evaluator.finalize()
            self._status = AssessmentStatus.COMPLETED
            await self._notify_progress()
        except asyncio.CancelledError:
            self._status = AssessmentStatus.CANCELLED
            await self._notify_progress()
            raise
        except Exception as exc:
            logger.error("Async contract stream worker failed", exc_info=True)
            self._status = AssessmentStatus.FAILED
            self._error = str(exc)
            await self._notify_progress()

    async def push_event(self, event: ComputationalEvent) -> None:
        """Pushes an event into the stream, awaiting if queue capacity is exceeded (backpressure)."""
        if self._status in (
            AssessmentStatus.COMPLETED,
            AssessmentStatus.FAILED,
            AssessmentStatus.CANCELLED,
        ):
            return
        await self._queue.put(event)

    async def finalize(self) -> EvidenceAssessment:
        """Flushes the stream, terminates worker, and returns the final EvidenceAssessment."""
        if self._status == AssessmentStatus.COMPLETED and self._final_assessment is not None:
            return self._final_assessment

        await self._queue.put(_SENTINEL)
        if self._worker_task is not None:
            await self._worker_task
        if self._final_assessment is None:
            self._final_assessment = self.evaluator.finalize()
        self._status = AssessmentStatus.COMPLETED
        return self._final_assessment

    async def cancel(self) -> None:
        """Cancels background worker cleanly."""
        self._status = AssessmentStatus.CANCELLED
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        await self._notify_progress()

    def get_status(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "status": self._status.value,
            "contract": self.contract.name,
            "events_evaluated": self.evaluator.events_evaluated,
            "error": self._error,
        }
        if self._final_assessment is not None:
            doc.update(self._final_assessment.to_dict())
        return doc


class AssessmentJob:
    """A managed assessment job binding a tenant, run, contract, and processor."""

    def __init__(
        self,
        assessment_id: str,
        tenant_id: str,
        run_id: str,
        contract: EvidenceContract,
        coverage: Sequence[CoverageSnapshot] = (),
        queue_capacity: int = 1_000,
    ) -> None:
        self.assessment_id = assessment_id
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.contract = contract
        self.coverage = tuple(coverage)
        self.created_at = datetime.now(UTC).isoformat()
        self.processor = AsyncContractStreamProcessor(
            contract, coverage=self.coverage, queue_capacity=queue_capacity
        )

    async def push_event(self, event: ComputationalEvent) -> None:
        await self.processor.push_event(event)

    async def finalize(self) -> EvidenceAssessment:
        return await self.processor.finalize()

    async def cancel(self) -> None:
        await self.processor.cancel()

    def get_status(self) -> dict[str, Any]:
        st = self.processor.get_status()
        st["assessment_id"] = self.assessment_id
        st["tenant_id"] = self.tenant_id
        st["run_id"] = self.run_id
        st["created_at"] = self.created_at
        return st


class AssessmentManager:
    """Manages active and historical assessment jobs with tenant isolation."""

    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str], AssessmentJob] = {}
        self._lock = asyncio.Lock()

    async def create_assessment(
        self,
        tenant_id: str,
        run_id: str,
        contract: EvidenceContract,
        coverage: Sequence[CoverageSnapshot] = (),
        assessment_id: str | None = None,
        queue_capacity: int = 1_000,
    ) -> AssessmentJob:
        aid = assessment_id or f"asm-{new_ulid()}"
        job = AssessmentJob(
            assessment_id=aid,
            tenant_id=tenant_id,
            run_id=run_id,
            contract=contract,
            coverage=coverage,
            queue_capacity=queue_capacity,
        )
        await job.processor.start()
        async with self._lock:
            self._jobs[(tenant_id, aid)] = job
        return job

    def get_job(self, tenant_id: str, assessment_id: str) -> AssessmentJob | None:
        return self._jobs.get((tenant_id, assessment_id))

    def get_assessment(self, tenant_id: str, assessment_id: str) -> dict[str, Any] | None:
        job = self.get_job(tenant_id, assessment_id)
        if job is None:
            return None
        return job.get_status()

    async def shutdown(self) -> None:
        """Cancels all active jobs."""
        async with self._lock:
            jobs = list(self._jobs.values())
        for j in jobs:
            await j.cancel()
        self._jobs.clear()


_global_manager = AssessmentManager()


def get_assessment_manager() -> AssessmentManager:
    return _global_manager
