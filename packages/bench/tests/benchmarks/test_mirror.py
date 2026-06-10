"""Unit tests for mirror circuit generation and the pure analyze() path."""

from __future__ import annotations

import pydantic
import pytest
from qiskit import qasm3

from veriqant_bench.benchmarks.base import GeneratedCircuit
from veriqant_bench.benchmarks.mirror import MirrorCircuits, MirrorParams, polarization

MIRROR = MirrorCircuits()


def test_polarization_rescaling() -> None:
    assert polarization(1.0, 3) == 1.0
    assert polarization(1.0 / 8.0, 3) == 0.0  # guessing floor maps to zero
    assert polarization(0.5, 1) == 0.0


def test_generation_is_deterministic_and_targets_recorded() -> None:
    params = MirrorParams(qubits=[0, 1], depths=[1, 2], samples_per_depth=2)
    first = MIRROR.generate(params, seed=11)
    second = MIRROR.generate(params, seed=11)
    assert [c.qasm3 for c in first] == [c.qasm3 for c in second]
    assert all(set(str(c.metadata["target"])) <= {"0", "1"} for c in first)
    assert all(len(str(c.metadata["target"])) == 2 for c in first)


def test_generated_circuits_are_loadable_qasm3() -> None:
    params = MirrorParams(qubits=[0, 1, 2], depths=[2], samples_per_depth=2)
    for circuit in MIRROR.generate(params, seed=3):
        loaded = qasm3.loads(circuit.qasm3)
        assert loaded.num_qubits == 3


def test_analyze_perfect_counts_gives_unit_polarization() -> None:
    params = MirrorParams(qubits=[0, 1], depths=[2, 4], samples_per_depth=2)
    circuits = [
        GeneratedCircuit(
            name=f"m_d{depth}_s{sample}",
            qasm3="OPENQASM 3.0;",
            metadata={"depth": depth, "sample": sample, "target": "01"},
        )
        for depth in (2, 4)
        for sample in (0, 1)
    ]
    counts = [{"01": 100} for _ in circuits]
    result = MIRROR.analyze(circuits, counts, 100, params)
    by_name = {metric.name: metric for metric in result.metrics}
    for depth in (2, 4):
        assert by_name[f"success_probability.depth_{depth}"].value == 1.0
        assert by_name[f"mirror_polarization.depth_{depth}"].value == 1.0


def test_analyze_uniform_counts_gives_zero_polarization() -> None:
    params = MirrorParams(qubits=[0, 1], depths=[2], samples_per_depth=2)
    circuits = [
        GeneratedCircuit(
            name=f"m_d2_s{sample}",
            qasm3="OPENQASM 3.0;",
            metadata={"depth": 2, "sample": sample, "target": "01"},
        )
        for sample in (0, 1)
    ]
    counts = [{"00": 25, "01": 25, "10": 25, "11": 25} for _ in circuits]
    result = MIRROR.analyze(circuits, counts, 100, params)
    by_name = {metric.name: metric for metric in result.metrics}
    assert by_name["success_probability.depth_2"].value == 0.25
    assert abs(by_name["mirror_polarization.depth_2"].value) < 1e-12


def test_params_validation() -> None:
    with pytest.raises(pydantic.ValidationError):
        MirrorParams(qubits=[1, 1])
    with pytest.raises(pydantic.ValidationError):
        MirrorParams(depths=[0])
    with pytest.raises(pydantic.ValidationError):
        MirrorParams(qubits=list(range(13)))  # statevector width cap
    assert MirrorParams(depths=[8, 2]).depths == [2, 8]
    assert MIRROR.qpr_benchmark_id(MirrorParams()) == "mirror_circuits"
