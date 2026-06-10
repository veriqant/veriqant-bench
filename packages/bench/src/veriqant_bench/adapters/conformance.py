"""Shared conformance suite for QPUAdapter implementations.

This is the behavioral contract of the adapter protocol — every adapter,
including third-party ones, must pass it. Usage in an adapter's test suite
(requires pytest and pytest-asyncio):

    from veriqant_bench.adapters.conformance import AdapterConformanceSuite

    class TestMyAdapterConformance(AdapterConformanceSuite):
        def make_adapter(self):
            return MyAdapter()
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from veriqant_bench.qpr._generated import Device

from .errors import AdapterError, ExecutionError, SubmissionError
from .protocol import QPUAdapter
from .types import DeviceCapabilities, JobHandle, JobSpec, JobStatus

SMOKE_1Q = (
    "OPENQASM 3.0;\n"
    'include "stdgates.inc";\n'
    "qubit[1] q;\n"
    "bit[1] c;\n"
    "h q[0];\n"
    "c[0] = measure q[0];\n"
)

BELL_2Q = (
    "OPENQASM 3.0;\n"
    'include "stdgates.inc";\n'
    "qubit[2] q;\n"
    "bit[2] c;\n"
    "h q[0];\n"
    "cx q[0], q[1];\n"
    "c[0] = measure q[0];\n"
    "c[1] = measure q[1];\n"
)

# X on qubit 0 only: distinguishes bitstring conventions. The only valid
# outcome in QPR convention (bit 0 rightmost) is '01'.
ASYMMETRIC_2Q = (
    "OPENQASM 3.0;\n"
    'include "stdgates.inc";\n'
    "qubit[2] q;\n"
    "bit[2] c;\n"
    "x q[0];\n"
    "c[0] = measure q[0];\n"
    "c[1] = measure q[1];\n"
)

MALFORMED = "OPENQASM 3.0;\nthis is not a quantum circuit ;;;\n"

_STATUS_ORDER = {
    JobStatus.QUEUED: 0,
    JobStatus.RUNNING: 1,
    JobStatus.COMPLETED: 2,
    JobStatus.FAILED: 2,
    JobStatus.CANCELLED: 2,
}


class AdapterConformanceSuite:
    """Subclass and implement make_adapter() to certify an adapter."""

    def make_adapter(self) -> QPUAdapter:
        raise NotImplementedError("conformance subclasses must implement make_adapter()")

    @pytest.fixture
    def adapter(self) -> QPUAdapter:
        return self.make_adapter()

    def test_satisfies_protocol(self, adapter: QPUAdapter) -> None:
        assert isinstance(adapter, QPUAdapter)
        assert isinstance(adapter.name, str) and adapter.name
        assert isinstance(adapter.adapter_version, str) and adapter.adapter_version

    def test_capabilities_map_onto_qpr_device(self, adapter: QPUAdapter) -> None:
        capabilities = adapter.capabilities()
        assert isinstance(capabilities, DeviceCapabilities)
        device = capabilities.to_qpr_device(adapter.calibration_snapshot())
        # Round-trip through the schema-generated model: the dict form must
        # validate exactly as it would inside a QPR document.
        Device.model_validate(device.model_dump(mode="json", exclude_none=True))

    def test_simulators_cost_nothing(self, adapter: QPUAdapter) -> None:
        spec = JobSpec(circuits=[SMOKE_1Q], shots=100, seed=7)
        estimate = adapter.estimate_cost(spec)
        if adapter.capabilities().is_simulator:
            assert estimate.amount == Decimal(0)
            assert estimate.confidence == "exact"

    @pytest.mark.asyncio
    async def test_lifecycle_reaches_completed_through_valid_states(
        self, adapter: QPUAdapter
    ) -> None:
        import asyncio

        handle = await adapter.submit(JobSpec(circuits=[BELL_2Q], shots=128, seed=11))
        assert isinstance(handle, JobHandle)
        observed: list[JobStatus] = []
        while True:
            status = await adapter.poll(handle)
            observed.append(status)
            if status.is_terminal:
                break
            # Yield to the event loop: in-process adapters do their work on
            # tasks of this same loop, and poll() itself need not yield.
            await asyncio.sleep(0.005)
        ranks = [_STATUS_ORDER[status] for status in observed]
        assert ranks == sorted(ranks), f"job state regressed: {observed}"
        assert observed[-1] is JobStatus.COMPLETED
        result = await adapter.result(handle)
        assert len(result.counts) == 1
        assert sum(result.counts[0].values()) == 128
        # Terminal states are stable.
        assert await adapter.poll(handle) is JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_counts_use_qpr_bit_order(self, adapter: QPUAdapter) -> None:
        spec = JobSpec(circuits=[ASYMMETRIC_2Q], shots=64, seed=3)
        handle = await adapter.submit(spec)
        result = await adapter.await_result(handle)
        counts = result.counts[0]
        assert sum(counts.values()) == 64
        assert all(len(key) == 2 and set(key) <= {"0", "1"} for key in counts)
        # Majority (not exact) so noisy backends can certify too; '10' as the
        # dominant outcome means the adapter got bit order backwards.
        assert max(counts, key=lambda key: counts[key]) == "01"

    @pytest.mark.asyncio
    async def test_identical_seed_reproduces_identical_counts(self, adapter: QPUAdapter) -> None:
        if not adapter.capabilities().is_simulator:
            pytest.skip("determinism is only contractual for simulators")
        spec = JobSpec(circuits=[BELL_2Q, SMOKE_1Q], shots=256, seed=1234)
        first = await adapter.await_result(await adapter.submit(spec))
        second = await adapter.await_result(await adapter.submit(spec))
        assert first.counts == second.counts

    @pytest.mark.asyncio
    async def test_malformed_qasm_raises_typed_error(self, adapter: QPUAdapter) -> None:
        spec = JobSpec(circuits=[MALFORMED], shots=16, seed=0)
        with pytest.raises((SubmissionError, ExecutionError)):
            handle = await adapter.submit(spec)
            await adapter.await_result(handle)

    @pytest.mark.asyncio
    async def test_foreign_handle_raises_adapter_error(self, adapter: QPUAdapter) -> None:
        from datetime import UTC, datetime

        foreign = JobHandle(
            job_id="not-a-real-job", adapter=adapter.name, submitted_at=datetime.now(tz=UTC)
        )
        with pytest.raises(AdapterError):
            await adapter.poll(foreign)
