"""Quantum volume: heavy-output computation against hand-checkable cases,
generation determinism, and the pure analyze() path."""

from __future__ import annotations

import numpy as np
import pydantic
import pytest

from veriqant_bench.benchmarks.base import GeneratedCircuit
from veriqant_bench.benchmarks.qv import QuantumVolume, QVParams, heavy_outputs

QV = QuantumVolume()


def test_heavy_outputs_identity_circuit() -> None:
    # All weight on |00>: median over [1,0,0,0] is 0, heavy = {p > 0} = {"00"}.
    heavy, median, ideal = heavy_outputs(np.array([1.0, 0.0, 0.0, 0.0]), 2)
    assert heavy == ["00"]
    assert median == 0.0
    assert ideal == 1.0


def test_heavy_outputs_uniform_superposition_has_no_heavy_set() -> None:
    # Uniform distribution: nothing is strictly above the median.
    heavy, median, ideal = heavy_outputs(np.array([0.25, 0.25, 0.25, 0.25]), 2)
    assert heavy == []
    assert median == 0.25
    assert ideal == 0.0


def test_heavy_outputs_half_weight_case() -> None:
    # H on qubit 0 (LSB): p = [0.5, 0.5, 0, 0] over indices 00,01,10,11.
    heavy, median, ideal = heavy_outputs(np.array([0.5, 0.5, 0.0, 0.0]), 2)
    assert heavy == ["00", "01"]
    assert median == 0.25
    assert ideal == 1.0


def test_generation_is_deterministic_and_records_heavy_sets() -> None:
    params = QVParams(widths=[2], circuits_per_width=5)
    first = QV.generate(params, seed=21)
    second = QV.generate(params, seed=21)
    assert [c.qasm3 for c in first] == [c.qasm3 for c in second]
    for circuit in first:
        heavy = circuit.metadata["heavy_outputs"]
        assert isinstance(heavy, list)
        assert all(len(bitstring) == 2 for bitstring in heavy)
        # Random SU(4) circuits asymptotically give ~0.85 ideal heavy prob;
        # anything valid must beat the 0.5 of a uniform sampler.
        assert circuit.metadata["ideal_heavy_probability"] > 0.5


def test_width_validation() -> None:
    with pytest.raises(pydantic.ValidationError, match="refused"):
        QVParams(widths=[13])
    with pytest.raises(pydantic.ValidationError):
        QVParams(widths=[1])
    with pytest.raises(pydantic.ValidationError):
        QVParams(widths=[2, 2])
    assert QVParams(widths=[4, 2]).widths == [2, 4]


def synthetic_qv_inputs(
    hop: float, *, width: int = 2, n_circuits: int = 50, shots: int = 1000
) -> tuple[QVParams, list[GeneratedCircuit], list[dict[str, int]]]:
    """Synthetic counts hitting an exact heavy-output probability."""
    params = QVParams(widths=[width], circuits_per_width=n_circuits)
    circuits: list[GeneratedCircuit] = []
    counts: list[dict[str, int]] = []
    heavy_hits = round(hop * shots)
    for sample in range(n_circuits):
        circuits.append(
            GeneratedCircuit(
                name=f"qv_w{width}_s{sample}",
                qasm3="OPENQASM 3.0;",
                metadata={"width": width, "sample": sample, "heavy_outputs": ["00", "01"]},
            )
        )
        counts.append({"00": heavy_hits, "11": shots - heavy_hits})
    return params, circuits, counts


def test_high_hop_passes_and_sets_qv() -> None:
    params, circuits, counts = synthetic_qv_inputs(0.85)
    result = QV.analyze(circuits, counts, 1000, params)
    by_name = {metric.name: metric for metric in result.metrics}
    assert result.metrics[0].name == "quantum_volume"  # primary metric first
    assert by_name["qv_pass.width_2"].value == 1.0
    assert by_name["quantum_volume"].value == 4.0
    assert abs(by_name["heavy_output_probability.width_2"].value - 0.85) < 1e-9


def test_borderline_hop_fails_the_two_sigma_rule() -> None:
    # 0.668 > 2/3 but not by 2 aggregate-binomial sigma over 50*1000
    # samples (sigma ~ 0.0021) — must fail, honestly reported.
    params, circuits, counts = synthetic_qv_inputs(0.668)
    result = QV.analyze(circuits, counts, 1000, params)
    by_name = {metric.name: metric for metric in result.metrics}
    assert by_name["qv_pass.width_2"].value == 0.0
    assert by_name["quantum_volume"].value == 1.0
    quality = by_name["quantum_volume"].quality
    assert quality is not None
    assert "qv.no_width_passed" in (quality.issues or [])


def test_failed_width_is_present_not_omitted() -> None:
    params, circuits, counts = synthetic_qv_inputs(0.40)
    result = QV.analyze(circuits, counts, 1000, params)
    names = {metric.name for metric in result.metrics}
    assert "qv_pass.width_2" in names
    assert "heavy_output_probability.width_2" in names


def test_insufficient_circuits_marks_unreliable() -> None:
    params, circuits, counts = synthetic_qv_inputs(0.85, n_circuits=10)
    result = QV.analyze(circuits, counts, 1000, params)
    quality = result.metrics[0].quality
    assert quality is not None
    assert not quality.reliable
    assert "qv.insufficient_circuits_for_confidence" in (quality.issues or [])


def test_below_publication_grade_is_flagged_but_reliable() -> None:
    params, circuits, counts = synthetic_qv_inputs(0.85, n_circuits=50)
    result = QV.analyze(circuits, counts, 1000, params)
    quality = result.metrics[0].quality
    assert quality is not None
    assert quality.reliable
    assert "qv.circuit_count_below_publication_grade" in (quality.issues or [])


def test_non_monotonic_pass_pattern_is_flagged() -> None:
    params = QVParams(widths=[2, 3], circuits_per_width=50)
    circuits: list[GeneratedCircuit] = []
    counts: list[dict[str, int]] = []
    for width, hop in ((2, 0.40), (3, 0.90)):  # small width fails, larger passes
        heavy = ["0" * width, "0" * (width - 1) + "1"]
        for sample in range(50):
            circuits.append(
                GeneratedCircuit(
                    name=f"qv_w{width}_s{sample}",
                    qasm3="OPENQASM 3.0;",
                    metadata={"width": width, "sample": sample, "heavy_outputs": heavy},
                )
            )
            hits = round(hop * 1000)
            counts.append({heavy[0]: hits, "1" * width: 1000 - hits})
    result = QV.analyze(circuits, counts, 1000, params)
    quality = result.metrics[0].quality
    assert quality is not None
    assert "qv.non_monotonic_pass_pattern" in (quality.issues or [])
    assert result.metrics[0].value == 8.0  # 2^3: largest passing width, flagged
