"""Closed-loop validation: generate → execute on Aer → analyze → sealed QPR,
checked against analytically expected values.

RB ground truth: Qiskit's depolarizing_error(lam, 1) is
E(rho) = (1-lam)·rho + lam·I/2, which shrinks the Bloch vector by (1-lam)
per application. The adapter attaches it to every sx/x/id gate (rz is
virtual). A sequence of m Cliffords (plus the inverting one) therefore
multiplies the polarization by (1-lam)^k, k = number of noisy gates, so the
RB decay rate is alpha = (1-lam)^k_bar with k_bar the mean number of noisy
gates per Clifford in the *transpiled* circuits — which we count directly,
using the same deterministic transpilation the adapter applies. EPC then is
(1-alpha)/2 for one qubit.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from qiskit import qasm3, transpile

from veriqore_bench.adapters import NoiseSpec
from veriqore_bench.adapters.aer import _NOISY_1Q_GATES, NOISE_BASIS, AerSimulatorAdapter
from veriqore_bench.benchmarks import run_benchmark
from veriqore_bench.benchmarks.mirror import MirrorCircuits, MirrorParams
from veriqore_bench.benchmarks.rb import RandomizedBenchmarking, RBParams
from veriqore_bench.qpr import QuantumPerformanceRecord, verify_qpr_document

pytestmark = pytest.mark.slow

SEED = 42
LAM = 0.02

RB_PARAMS = RBParams(qubits=[0], lengths=[1, 2, 4, 8, 16, 24], samples_per_length=8)


def assert_record_verifies(record: QuantumPerformanceRecord) -> None:
    report = verify_qpr_document(json.loads(record.model_dump_json(exclude_none=True)))
    assert report.ok, [str(issue) for issue in report.issues]


def expected_epc(record: QuantumPerformanceRecord) -> float:
    """(1-lam)^k_bar derivation; k_bar counted from the adapter's own
    deterministic transpilation of the executed circuits."""
    noisy_gates = 0
    cliffords = 0
    for circuit in record.circuits:
        transpiled = transpile(
            qasm3.loads(circuit.qasm3),
            basis_gates=NOISE_BASIS,
            optimization_level=0,
            seed_transpiler=record.execution.seed,
        )
        ops = transpiled.count_ops()
        noisy_gates += sum(int(ops.get(gate, 0)) for gate in _NOISY_1Q_GATES)
        assert circuit.metadata is not None
        cliffords += int(circuit.metadata["length"]) + 1  # + inverting Clifford
    k_bar = noisy_gates / cliffords
    alpha = float((1.0 - LAM) ** k_bar)
    return (1.0 - alpha) / 2.0


@pytest.mark.timeout(300)
async def test_rb_recovers_injected_depolarizing_noise() -> None:
    adapter = AerSimulatorAdapter(noise=NoiseSpec(depolarizing_1q=LAM))
    record = await run_benchmark(RandomizedBenchmarking(), adapter, RB_PARAMS, seed=SEED, shots=512)
    assert_record_verifies(record)

    epc = next(m for m in record.results.metrics if m.name == "error_per_clifford")
    assert epc.quality is not None and epc.quality.reliable, epc.quality

    expected = expected_epc(record)
    ci_width = epc.statistics.ci_upper - epc.statistics.ci_lower
    tolerance = max(ci_width, 0.5 * expected)
    assert abs(epc.value - expected) <= tolerance, (
        f"measured EPC {epc.value:.4f} vs expected {expected:.4f} "
        f"(CI [{epc.statistics.ci_lower:.4f}, {epc.statistics.ci_upper:.4f}])"
    )
    # The noise spec must be auditable in the record itself.
    assert record.device.calibration_snapshot is not None
    assert record.device.calibration_snapshot["noise_spec"]["depolarizing_1q"] == LAM


@pytest.mark.timeout(300)
async def test_rb_on_ideal_simulator_reports_near_zero_epc() -> None:
    record = await run_benchmark(
        RandomizedBenchmarking(), AerSimulatorAdapter(), RB_PARAMS, seed=SEED, shots=512
    )
    assert_record_verifies(record)
    epc = next(m for m in record.results.metrics if m.name == "error_per_clifford")
    assert epc.value < 0.01
    assert epc.quality is not None and epc.quality.reliable


@pytest.mark.timeout(300)
async def test_mirror_ideal_vs_noisy_polarization() -> None:
    params = MirrorParams(qubits=[0, 1, 2], depths=[2, 4], samples_per_depth=4)
    benchmark = MirrorCircuits()

    ideal = await run_benchmark(benchmark, AerSimulatorAdapter(), params, seed=SEED, shots=256)
    assert_record_verifies(ideal)
    ideal_pols = [
        m.value for m in ideal.results.metrics if m.name.startswith("mirror_polarization")
    ]
    assert all(value >= 0.99 for value in ideal_pols), ideal_pols

    noisy_adapter = AerSimulatorAdapter(noise=NoiseSpec(depolarizing_1q=0.02, depolarizing_2q=0.03))
    noisy = await run_benchmark(benchmark, noisy_adapter, params, seed=SEED, shots=256)
    assert_record_verifies(noisy)
    noisy_pols = [
        m.value for m in noisy.results.metrics if m.name.startswith("mirror_polarization")
    ]
    assert float(np.mean(noisy_pols)) < 0.97
    assert float(np.mean(noisy_pols)) < float(np.mean(ideal_pols))


@pytest.mark.timeout(300)
async def test_same_seed_reproduces_identical_measurement_content() -> None:
    """Same seed + params => identical circuits, counts, and metrics. The
    full sealed content hash necessarily differs between runs (record_id and
    timestamps are unique by design), so equality is asserted on everything
    else: we equalize identity fields and reseal, which must then be
    bit-identical."""
    params = RBParams(qubits=[0], lengths=[1, 2, 4], samples_per_length=2)
    benchmark = RandomizedBenchmarking()
    first = await run_benchmark(benchmark, AerSimulatorAdapter(), params, seed=99, shots=64)
    second = await run_benchmark(benchmark, AerSimulatorAdapter(), params, seed=99, shots=64)
    assert [c.qasm3_sha256 for c in first.circuits] == [c.qasm3_sha256 for c in second.circuits]
    assert [r.counts for r in first.results.raw] == [r.counts for r in second.results.raw]
    assert first.results.metrics == second.results.metrics

    from veriqore_bench.qpr import seal

    aligned = second.model_copy(
        update={
            "record_id": first.record_id,
            "created_at": first.created_at,
            "execution": second.execution.model_copy(
                update={
                    "submitted_at": first.execution.submitted_at,
                    "completed_at": first.execution.completed_at,
                }
            ),
        }
    )
    assert seal(aligned).integrity.content_sha256 == first.integrity.content_sha256
