"""Job-lifecycle convenience built on the polling primitive."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from .errors import TimeoutError
from .types import JobHandle, JobResult

if TYPE_CHECKING:
    from .protocol import QPUAdapter


async def await_result(
    adapter: QPUAdapter,
    handle: JobHandle,
    *,
    timeout: float = 60.0,
    poll_interval: float = 0.05,
) -> JobResult:
    """Poll *adapter* until the job reaches a terminal state, then fetch the
    result. Failure/cancellation surfaces through result() as ExecutionError;
    exceeding *timeout* raises the adapter TimeoutError."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        status = await adapter.poll(handle)
        if status.is_terminal:
            return await adapter.result(handle)
        if loop.time() >= deadline:
            raise TimeoutError(
                f"job {handle.job_id} on '{adapter.name}' still {status.value} after {timeout:.3g}s"
            )
        await asyncio.sleep(poll_interval)


class AwaitResultMixin:
    """Provides the protocol's await_result() in terms of poll() + result()."""

    async def await_result(
        self, handle: JobHandle, *, timeout: float = 60.0, poll_interval: float = 0.05
    ) -> JobResult:
        adapter = cast("QPUAdapter", self)
        return await await_result(adapter, handle, timeout=timeout, poll_interval=poll_interval)
