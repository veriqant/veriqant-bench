"""Unit tests for RB generation determinism and the pure analyze() path."""

from __future__ import annotations

import pydantic
import pytest

from veriqant_bench.benchmarks.base import GeneratedCircuit
from veriqant_bench.benchmarks.rb import (
    RandomizedBenchmarking,
    RBParams,
    epc_from_alpha,
    fit_rb_decay,
)

RB = RandomizedBenchmarking()


def synthetic_rb_data(
    alpha: float,
    *,
    amplitude: float = 0.5,
    baseline: float = 0.5,
    lengths: list[int] | None = None,
    samples: int = 5,
    shots: int = 100_000,
) -> tuple[RBParams, list[GeneratedCircuit], list[dict[str, int]]]:
    """Noise-free synthetic counts following an exact A·alpha^m + B decay."""
    lengths = lengths or [1, 2, 4, 8, 16, 32]
    params = RBParams(qubits=[0], lengths=lengths, samples_per_length=samples)
    circuits: list[GeneratedCircuit] = []
    counts: list[dict[str, int]] = []
    for length in lengths:
        survival = amplitude * alpha**length + baseline
        zeros = round(survival * shots)
        for sample in range(samples):
            circuits.append(
                GeneratedCircuit(
                    name=f"syn_len{length}_s{sample}",
                    qasm3="OPENQASM 3.0;",
                    metadata={"length": length, "sample": sample},
                )
            )
            counts.append({"0": zeros, "1": shots - zeros})
    return params, circuits, counts


def test_known_decay_recovers_epc() -> None:
    alpha = 0.9
    params, circuits, counts = synthetic_rb_data(alpha)
    result = RB.analyze(circuits, counts, 100_000, params)
    epc = next(metric for metric in result.metrics if metric.name == "error_per_clifford")
    expected = epc_from_alpha(alpha, 2)  # 0.05
    assert abs(epc.value - expected) < 0.002
    assert epc.quality is not None and epc.quality.reliable
    assert epc.statistics.ci_lower <= epc.value <= epc.statistics.ci_upper
    assert epc.statistics.sample_size == len(params.lengths) * 5 * 100_000


def test_decay_rate_metric_reports_alpha() -> None:
    params, circuits, counts = synthetic_rb_data(0.85)
    result = RB.analyze(circuits, counts, 100_000, params)
    decay = next(metric for metric in result.metrics if metric.name == "rb_decay_rate")
    assert abs(decay.value - 0.85) < 0.01


def test_flat_floor_data_is_flagged_unreliable() -> None:
    # Survival stuck at the 0.5 guessing floor: amplitude is unidentifiable.
    params, circuits, counts = synthetic_rb_data(0.9, amplitude=0.0, baseline=0.5)
    result = RB.analyze(circuits, counts, 100_000, params)
    epc = next(metric for metric in result.metrics if metric.name == "error_per_clifford")
    assert epc.quality is not None
    assert not epc.quality.reliable
    assert "fit.amplitude_collapsed" in (epc.quality.issues or [])


def test_perfect_survival_is_reliable_zero_epc() -> None:
    params, circuits, counts = synthetic_rb_data(1.0, amplitude=0.5, baseline=0.5)
    result = RB.analyze(circuits, counts, 100_000, params)
    epc = next(metric for metric in result.metrics if metric.name == "error_per_clifford")
    assert epc.value < 0.005
    assert epc.quality is not None and epc.quality.reliable


def test_failed_fit_reports_full_interval() -> None:
    fit = fit_rb_decay([1, 2, 4], [float("inf"), 0.0, 0.0], 2)
    assert not fit.converged


def test_generation_is_deterministic() -> None:
    params = RBParams(qubits=[0], lengths=[1, 2, 4], samples_per_length=2)
    first = RB.generate(params, seed=123)
    second = RB.generate(params, seed=123)
    assert [c.qasm3 for c in first] == [c.qasm3 for c in second]
    different = RB.generate(params, seed=124)
    assert [c.qasm3 for c in first] != [c.qasm3 for c in different]


def test_generation_2q_produces_two_qubit_circuits() -> None:
    params = RBParams(qubits=[0, 1], lengths=[1, 2, 3], samples_per_length=2)
    circuits = RB.generate(params, seed=5)
    assert len(circuits) == 6
    assert all("qubit[2]" in circuit.qasm3 for circuit in circuits)
    assert RB.qpr_benchmark_id(params) == "rb_2q"


def test_params_validation() -> None:
    with pytest.raises(pydantic.ValidationError):
        RBParams(qubits=[0, 0])
    with pytest.raises(pydantic.ValidationError):
        RBParams(qubits=[0, 1, 2])
    with pytest.raises(pydantic.ValidationError):
        RBParams(lengths=[1, 2])  # need >= 3 points
    with pytest.raises(pydantic.ValidationError):
        RBParams(lengths=[1, 2, 2])
    assert RBParams(lengths=[8, 1, 4]).lengths == [1, 4, 8]
