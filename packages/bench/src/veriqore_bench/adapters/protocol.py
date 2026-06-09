"""The QPUAdapter protocol — the neutral contract every backend satisfies.

Structural (typing.Protocol), not nominal: third-party adapters need no
import-time relationship with veriqore-bench beyond matching this shape.
The conformance suite in veriqore_bench.adapters.conformance is the
behavioral half of the contract.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import (
    CalibrationSnapshot,
    CostEstimate,
    DeviceCapabilities,
    JobHandle,
    JobResult,
    JobSpec,
    JobStatus,
)


@runtime_checkable
class QPUAdapter(Protocol):
    """Contract for anything that can execute OpenQASM 3 circuits."""

    name: str
    """Stable adapter identifier, recorded in QPR provider.adapter."""
    adapter_version: str
    """Version of the underlying execution stack, recorded in provenance."""

    def capabilities(self) -> DeviceCapabilities:
        """Discover what the backend can do."""
        ...

    def calibration_snapshot(self) -> CalibrationSnapshot | None:
        """Backend calibration data in effect now; None for ideal simulators."""
        ...

    def estimate_cost(self, spec: JobSpec) -> CostEstimate:
        """Cost of executing *spec*. Simulators return CostEstimate.free()."""
        ...

    async def submit(self, spec: JobSpec) -> JobHandle:
        """Submit circuits for execution. Raises SubmissionError on rejection."""
        ...

    async def poll(self, handle: JobHandle) -> JobStatus:
        """Current lifecycle state of the job."""
        ...

    async def result(self, handle: JobHandle) -> JobResult:
        """Result of a completed job. Raises ExecutionError if the job
        failed or was cancelled."""
        ...

    async def await_result(
        self, handle: JobHandle, *, timeout: float = 60.0, poll_interval: float = 0.05
    ) -> JobResult:
        """Poll until terminal, then return the result. Raises
        veriqore_bench.adapters.TimeoutError past the deadline."""
        ...
