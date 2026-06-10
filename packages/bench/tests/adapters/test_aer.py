"""Aer-specific behavior: noise injection, calibration verbatim-ness,
honest state history."""

from __future__ import annotations

from veriqant_bench.adapters import JobSpec, JobStatus, NoiseSpec
from veriqant_bench.adapters.aer import AerSimulatorAdapter

GHZ_3Q = (
    "OPENQASM 3.0;\n"
    'include "stdgates.inc";\n'
    "qubit[3] q;\n"
    "bit[3] c;\n"
    "h q[0];\n"
    "cx q[0], q[1];\n"
    "cx q[1], q[2];\n"
    "c[0] = measure q[0];\n"
    "c[1] = measure q[1];\n"
    "c[2] = measure q[2];\n"
)

NOISE = NoiseSpec(depolarizing_1q=0.05, depolarizing_2q=0.05, readout_error_0to1=0.02)


async def test_noise_measurably_degrades_ghz() -> None:
    spec = JobSpec(circuits=[GHZ_3Q], shots=2000, seed=99)

    ideal = AerSimulatorAdapter()
    ideal_counts = (await ideal.await_result(await ideal.submit(spec))).counts[0]
    assert set(ideal_counts) <= {"000", "111"}

    noisy = AerSimulatorAdapter(noise=NOISE)
    noisy_counts = (await noisy.await_result(await noisy.submit(spec))).counts[0]
    corrupted = sum(count for key, count in noisy_counts.items() if key not in {"000", "111"})
    assert corrupted > 0, "depolarizing + readout noise must produce non-GHZ outcomes"
    assert sum(noisy_counts.values()) == 2000


def test_noise_spec_lands_verbatim_in_calibration_snapshot() -> None:
    adapter = AerSimulatorAdapter(noise=NOISE)
    snapshot = adapter.calibration_snapshot()
    assert snapshot is not None
    assert snapshot.source == "noise_spec"
    assert snapshot.data["noise_spec"] == NOISE.model_dump(mode="json", exclude_none=True)


def test_ideal_adapter_has_no_calibration_snapshot() -> None:
    assert AerSimulatorAdapter().calibration_snapshot() is None
    # An all-zero NoiseSpec is normalized to ideal.
    adapter = AerSimulatorAdapter(noise=NoiseSpec())
    assert adapter.noise_spec is None
    assert adapter.calibration_snapshot() is None


async def test_relaxation_noise_path_executes() -> None:
    adapter = AerSimulatorAdapter(noise=NoiseSpec(depolarizing_1q=0.01, t1_us=50.0, t2_us=70.0))
    spec = JobSpec(circuits=[GHZ_3Q], shots=200, seed=5)
    result = await adapter.await_result(await adapter.submit(spec))
    assert sum(result.counts[0].values()) == 200


async def test_state_history_is_honest_even_for_instant_jobs() -> None:
    adapter = AerSimulatorAdapter()
    handle = await adapter.submit(JobSpec(circuits=[GHZ_3Q], shots=10, seed=1))
    await adapter.await_result(handle)
    assert adapter.state_history(handle) == [
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.COMPLETED,
    ]


async def test_result_metadata_records_transpilation() -> None:
    noisy = AerSimulatorAdapter(noise=NOISE)
    spec = JobSpec(circuits=[GHZ_3Q], shots=50, seed=2)
    result = await noisy.await_result(await noisy.submit(spec))
    transpilation = result.metadata["transpilation"]
    assert transpilation["sdk"] == "qiskit"
    assert transpilation["settings"]["seed_transpiler"] == 2

    ideal = AerSimulatorAdapter()
    result = await ideal.await_result(await ideal.submit(spec))
    assert "no transpilation" in result.metadata["transpilation"]["settings"]["note"]
