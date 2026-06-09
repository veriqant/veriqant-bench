"""LocalAdapterBase state machine: honest transitions, typed failures,
timeouts, cancellation — exercised with minimal in-test adapters."""

from __future__ import annotations

import builtins
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from veriqore_bench.adapters import (
    ExecutionError,
    JobHandle,
    JobSpec,
    JobStatus,
    LocalAdapterBase,
    SubmissionError,
    TimeoutError,
    UnknownJobError,
)

SPEC = JobSpec(circuits=["OPENQASM 3.0;"], shots=8, seed=0)


class InstantAdapter(LocalAdapterBase):
    name = "instant"
    adapter_version = "0.0.0"

    def _prepare(self, spec: JobSpec) -> Any:
        return spec.circuits

    def _execute(self, prepared: Any, spec: JobSpec) -> tuple[list[dict[str, int]], dict[str, Any]]:
        return [{"0": spec.shots} for _ in prepared], {"backend": "instant"}


class SlowAdapter(InstantAdapter):
    name = "slow"

    def _execute(self, prepared: Any, spec: JobSpec) -> tuple[list[dict[str, int]], dict[str, Any]]:
        time.sleep(0.4)
        return super()._execute(prepared, spec)


class FailingAdapter(InstantAdapter):
    name = "failing"

    def _execute(self, prepared: Any, spec: JobSpec) -> tuple[list[dict[str, int]], dict[str, Any]]:
        raise RuntimeError("backend exploded")


class RejectingAdapter(InstantAdapter):
    name = "rejecting"

    def _prepare(self, spec: JobSpec) -> Any:
        raise SubmissionError("rejected at the door")


async def test_completed_job_passed_through_every_state() -> None:
    adapter = InstantAdapter()
    handle = await adapter.submit(SPEC)
    result = await adapter.await_result(handle)
    assert result.counts == [{"0": 8}]
    assert adapter.state_history(handle) == [
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.COMPLETED,
    ]
    assert result.started_at <= result.completed_at


async def test_submission_rejection_creates_no_job() -> None:
    adapter = RejectingAdapter()
    with pytest.raises(SubmissionError, match="rejected at the door"):
        await adapter.submit(SPEC)
    assert adapter._jobs == {}


async def test_failure_is_typed_and_recorded() -> None:
    adapter = FailingAdapter()
    handle = await adapter.submit(SPEC)
    with pytest.raises(ExecutionError, match="backend exploded"):
        await adapter.await_result(handle)
    assert await adapter.poll(handle) is JobStatus.FAILED
    assert adapter.state_history(handle) == [
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.FAILED,
    ]
    # The failure is stable across repeated result() calls.
    with pytest.raises(ExecutionError):
        await adapter.result(handle)


async def test_await_result_times_out_with_adapter_timeout_error() -> None:
    adapter = SlowAdapter()
    handle = await adapter.submit(SPEC)
    with pytest.raises(TimeoutError, match="still"):
        await adapter.await_result(handle, timeout=0.05, poll_interval=0.01)
    # Our TimeoutError is also catchable as the builtin.
    assert issubclass(TimeoutError, builtins.TimeoutError)
    # The job itself keeps running and still completes.
    result = await adapter.await_result(handle, timeout=5)
    assert result.counts == [{"0": 8}]


async def test_cancellation() -> None:
    adapter = SlowAdapter()
    handle = await adapter.submit(SPEC)
    assert adapter.cancel(handle) is True
    with pytest.raises(ExecutionError, match="cancelled"):
        await adapter.result(handle)
    assert await adapter.poll(handle) is JobStatus.CANCELLED
    assert adapter.state_history(handle)[-1] is JobStatus.CANCELLED
    assert adapter.cancel(handle) is False  # already terminal


async def test_foreign_handles_raise_unknown_job_error() -> None:
    adapter = InstantAdapter()
    foreign = JobHandle(job_id="nope", adapter="instant", submitted_at=datetime.now(tz=UTC))
    with pytest.raises(UnknownJobError):
        await adapter.poll(foreign)
    with pytest.raises(UnknownJobError):
        await adapter.result(foreign)
    with pytest.raises(UnknownJobError):
        adapter.state_history(foreign)

    # A handle from a different adapter instance is just as foreign.
    other = InstantAdapter()
    handle = await other.submit(SPEC)
    await other.await_result(handle)
    with pytest.raises(UnknownJobError):
        await adapter.poll(handle)
