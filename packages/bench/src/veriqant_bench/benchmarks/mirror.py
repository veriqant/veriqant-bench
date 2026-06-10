"""Randomized mirror circuit benchmark (in the spirit of Proctor et al.,
Nat. Phys. 18, 75-79, 2022).

Methodology (suite_version 0.1.0):
- Each circuit: `depth` random layers (random 1Q Cliffords on every qubit,
  then CX gates on randomly paired qubits with configurable density), a
  central uniformly random Pauli layer, then the exact inverse of the random
  half. The whole circuit is Clifford, so the ideal outcome is a single
  computational basis state, computed classically at generation time and
  recorded in circuit metadata.
- Metrics per depth, each with bootstrap CIs over the sampled circuits:
  success probability (frequency of the target bitstring) and polarization
  (success rescaled for the 1/2^n random-guessing floor:
  (p - 1/2^n) / (1 - 1/2^n)).
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator
from qiskit import QuantumCircuit, qasm3
from qiskit.quantum_info import Statevector

from veriqant_bench.qpr._generated import Metric, MetricStatistics

from .base import AnalysisResult, Benchmark, GeneratedCircuit
from .stats import bootstrap_mean_ci

CONFIDENCE = 0.95

ONE_QUBIT_GATES = ["id", "x", "y", "z", "h", "s", "sdg", "sx"]
PAULI_GATES = ["id", "x", "y", "z"]
MAX_WIDTH = 12  # target state is computed via statevector simulation


class MirrorParams(BaseModel):
    """Parameters of a mirror-circuit run."""

    model_config = ConfigDict(extra="forbid")

    qubits: list[int] = Field(default=[0, 1, 2], min_length=1, max_length=MAX_WIDTH)
    depths: list[int] = Field(default=[2, 4, 8, 16], min_length=1)
    """Number of random layers in the first half (total depth is ~2x + 1)."""
    samples_per_depth: int = Field(default=10, ge=2)
    two_qubit_density: float = Field(default=0.5, ge=0.0, le=1.0)
    """Probability that an available qubit pair gets a CX in a given layer."""
    bootstrap_resamples: int = Field(default=200, ge=50)

    @field_validator("qubits")
    @classmethod
    def _distinct_qubits(cls, qubits: list[int]) -> list[int]:
        if len(set(qubits)) != len(qubits):
            raise ValueError("qubits must be distinct")
        if any(qubit < 0 for qubit in qubits):
            raise ValueError("qubit indices must be >= 0")
        return qubits

    @field_validator("depths")
    @classmethod
    def _valid_depths(cls, depths: list[int]) -> list[int]:
        if any(depth < 1 for depth in depths):
            raise ValueError("depths must be >= 1")
        if len(set(depths)) != len(depths):
            raise ValueError("depths must be distinct")
        return sorted(depths)


def polarization(success: float, num_qubits: int) -> float:
    """Success probability rescaled for the 1/2^n guessing floor."""
    floor = 1.0 / float(2**num_qubits)
    return (success - floor) / (1.0 - floor)


class MirrorCircuits(Benchmark[MirrorParams]):
    """Randomized mirror circuits: success probability and polarization vs.
    depth, with bootstrap confidence intervals."""

    name = "mirror"
    version = "0.1.0"
    params_model = MirrorParams

    def qpr_benchmark_id(self, params: MirrorParams) -> str:
        return "mirror_circuits"

    def display_name(self, params: MirrorParams) -> str:
        return f"{len(params.qubits)}-qubit randomized mirror circuits"

    def generate(self, params: MirrorParams, seed: int) -> list[GeneratedCircuit]:
        rng = np.random.default_rng(seed)
        n = len(params.qubits)
        circuits: list[GeneratedCircuit] = []
        for depth in params.depths:
            for sample in range(params.samples_per_depth):
                half = self._random_half(n, depth, params.two_qubit_density, rng)
                pauli = QuantumCircuit(n)
                for qubit in range(n):
                    getattr(pauli, str(rng.choice(PAULI_GATES)))(qubit)

                full = QuantumCircuit(n, n)
                full.compose(half, inplace=True)
                full.compose(pauli, inplace=True)
                full.compose(half.inverse(), inplace=True)

                target = self._ideal_outcome(full)
                full.measure(range(n), range(n))
                circuits.append(
                    GeneratedCircuit(
                        name=f"mirror_{n}q_d{depth}_s{sample}",
                        qasm3=qasm3.dumps(full),
                        metadata={"depth": depth, "sample": sample, "target": target},
                    )
                )
        return circuits

    @staticmethod
    def _random_half(
        n: int, depth: int, density: float, rng: np.random.Generator
    ) -> QuantumCircuit:
        circuit = QuantumCircuit(n)
        for _ in range(depth):
            for qubit in range(n):
                getattr(circuit, str(rng.choice(ONE_QUBIT_GATES)))(qubit)
            if n >= 2:
                order = rng.permutation(n)
                # With odd n the trailing qubit sits this layer out.
                for first, second in zip(order[0::2], order[1::2], strict=False):
                    if rng.random() < density:
                        circuit.cx(int(first), int(second))
        return circuit

    @staticmethod
    def _ideal_outcome(circuit_without_measure: QuantumCircuit) -> str:
        """The single basis state a noiseless device must return, in QPR
        bitstring convention (bit 0 rightmost — Qiskit's own ordering)."""
        probabilities = Statevector.from_instruction(circuit_without_measure).probabilities()
        index = int(np.argmax(probabilities))
        peak = float(probabilities[index])
        if abs(peak - 1.0) > 1e-9:  # pragma: no cover - guards a methodology bug
            raise RuntimeError(f"mirror circuit is not deterministic (peak prob {peak})")
        return format(index, f"0{circuit_without_measure.num_qubits}b")

    def analyze(
        self,
        circuits: list[GeneratedCircuit],
        counts: list[dict[str, int]],
        shots: int,
        params: MirrorParams,
        execution_metadata: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        n = len(params.qubits)
        successes: dict[int, list[float]] = {depth: [] for depth in params.depths}
        for circuit, circuit_counts in zip(circuits, counts, strict=True):
            depth = int(circuit.metadata["depth"])
            target = str(circuit.metadata["target"])
            total = sum(circuit_counts.values())
            successes[depth].append(circuit_counts.get(target, 0) / total)

        metrics: list[Metric] = []
        per_depth: dict[str, dict[str, float]] = {}
        for depth in sorted(successes):
            values = successes[depth]
            sample_size = len(values) * shots
            mean_success = float(np.mean(values))
            success_ci = bootstrap_mean_ci(values, n_resamples=params.bootstrap_resamples)
            pol_ci = bootstrap_mean_ci(
                values,
                n_resamples=params.bootstrap_resamples,
                transform=lambda value: polarization(value, n),
            )
            metrics.append(
                self._metric(
                    f"success_probability.depth_{depth}",
                    mean_success,
                    success_ci,
                    sample_size,
                    params,
                )
            )
            metrics.append(
                self._metric(
                    f"mirror_polarization.depth_{depth}",
                    polarization(mean_success, n),
                    pol_ci,
                    sample_size,
                    params,
                )
            )
            per_depth[str(depth)] = {
                "mean_success": mean_success,
                "mean_polarization": polarization(mean_success, n),
            }

        return AnalysisResult(
            metrics=metrics,
            analysis={
                "per_depth": per_depth,
                "bootstrap": {"resamples": params.bootstrap_resamples},
            },
        )

    @staticmethod
    def _metric(
        name: str,
        value: float,
        ci: tuple[float, float, float],
        sample_size: int,
        params: MirrorParams,
    ) -> Metric:
        lower, upper, std_error = ci
        return Metric(
            name=name,
            value=value,
            unit="probability",
            qubits=params.qubits,
            statistics=MetricStatistics(
                sample_size=sample_size,
                confidence_level=CONFIDENCE,
                ci_lower=min(lower, value),
                ci_upper=max(upper, value),
                std_error=std_error,
                estimator="mean_bootstrap_percentile",
            ),
        )
