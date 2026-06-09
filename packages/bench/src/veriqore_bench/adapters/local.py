"""Shared lifecycle machinery for in-process (simulator) adapters.

Local execution may be near-instant, but jobs still pass honestly through
QUEUED → RUNNING → terminal: every transition is validated against the state
machine and recorded in an inspectable history.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .errors import ExecutionError, UnknownJobError
from .lifecycle import AwaitResultMixin
from .types import VALID_TRANSITIONS, JobHandle, JobResult, JobSpec, JobStatus


@dataclass
class _LocalJob:
    handle: JobHandle
    status: JobStatus = JobStatus.QUEUED
    history: list[JobStatus] = field(default_factory=lambda: [JobStatus.QUEUED])
    task: asyncio.Task[JobResult] | None = None


class LocalAdapterBase(AwaitResultMixin, ABC):
    """Base for adapters that execute jobs in-process.

    Subclasses implement _prepare() (validate/convert circuits; runs inside
    submit, so rejection surfaces as SubmissionError there) and _execute()
    (blocking backend call; runs on a worker thread).
    """

    name: str
    adapter_version: str

    def __init__(self) -> None:
        self._jobs: dict[str, _LocalJob] = {}

    @abstractmethod
    def _prepare(self, spec: JobSpec) -> Any:
        """Parse/convert circuits for the backend. Raise SubmissionError on
        anything malformed or unsupported."""

    @abstractmethod
    def _execute(self, prepared: Any, spec: JobSpec) -> tuple[list[dict[str, int]], dict[str, Any]]:
        """Blocking execution. Returns (per-circuit counts in QPR bitstring
        convention, execution metadata)."""

    async def submit(self, spec: JobSpec) -> JobHandle:
        prepared = self._prepare(spec)
        handle = JobHandle(job_id=uuid4().hex, adapter=self.name, submitted_at=datetime.now(tz=UTC))
        job = _LocalJob(handle=handle)
        self._jobs[handle.job_id] = job
        job.task = asyncio.create_task(self._run(job, prepared, spec))
        # A task cancelled before it first runs never enters _run at all, so
        # its CancelledError handler can't record the transition; cover that
        # path (idempotently) once the task settles.
        job.task.add_done_callback(lambda task: self._finalize_cancelled(job, task))
        return handle

    def _finalize_cancelled(self, job: _LocalJob, task: asyncio.Task[JobResult]) -> None:
        if task.cancelled() and not job.status.is_terminal:
            self._transition(job, JobStatus.CANCELLED)

    async def poll(self, handle: JobHandle) -> JobStatus:
        # Yield once: jobs run as tasks on this same event loop, so a caller
        # polling in a tight loop must not be able to starve them.
        await asyncio.sleep(0)
        return self._job(handle).status

    async def result(self, handle: JobHandle) -> JobResult:
        job = self._job(handle)
        assert job.task is not None
        try:
            return await job.task
        except asyncio.CancelledError as exc:
            raise ExecutionError(f"job {handle.job_id} was cancelled") from exc

    def cancel(self, handle: JobHandle) -> bool:
        """Request cancellation. True if the job was still cancellable."""
        job = self._job(handle)
        if job.status.is_terminal or job.task is None:
            return False
        return job.task.cancel()

    def state_history(self, handle: JobHandle) -> list[JobStatus]:
        """Every state the job passed through, in order (local adapters only)."""
        return list(self._job(handle).history)

    def _job(self, handle: JobHandle) -> _LocalJob:
        job = self._jobs.get(handle.job_id)
        if job is None or handle.adapter != self.name:
            raise UnknownJobError(
                f"job {handle.job_id} (adapter '{handle.adapter}') is not known to '{self.name}'"
            )
        return job

    def _transition(self, job: _LocalJob, to: JobStatus) -> None:
        if to not in VALID_TRANSITIONS[job.status]:
            raise RuntimeError(
                f"illegal job state transition {job.status.value} -> {to.value}"
            )  # pragma: no cover - guards programming errors
        job.status = to
        job.history.append(to)

    async def _run(self, job: _LocalJob, prepared: Any, spec: JobSpec) -> JobResult:
        try:
            # Yield once so the job is observable as QUEUED before work starts.
            await asyncio.sleep(0)
            self._transition(job, JobStatus.RUNNING)
            started_at = datetime.now(tz=UTC)
            counts, metadata = await asyncio.to_thread(self._execute, prepared, spec)
        except asyncio.CancelledError:
            # May arrive while QUEUED or RUNNING; both transition to CANCELLED.
            self._transition(job, JobStatus.CANCELLED)
            raise
        except ExecutionError:
            self._transition(job, JobStatus.FAILED)
            raise
        except Exception as exc:
            self._transition(job, JobStatus.FAILED)
            raise ExecutionError(f"backend execution failed: {exc}") from exc
        self._transition(job, JobStatus.COMPLETED)
        return JobResult(
            counts=counts,
            shots=spec.shots,
            started_at=started_at,
            completed_at=datetime.now(tz=UTC),
            metadata=metadata,
        )
