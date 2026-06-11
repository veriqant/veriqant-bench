"""Quantum Volume (Cross et al., Phys. Rev. A 100, 032328, 2019).

Methodology (suite_version 0.1.0):
- For each width m: random square circuits (depth = m layers), each layer a
  uniformly random qubit permutation with a Haar-random SU(4) on each
  resulting pair. Circuits are synthesized to the rz/sx/x/cx basis at
  generation time, deterministically from (params, seed); only OpenQASM 3
  crosses the adapter boundary.
- Heavy outputs are computed classically at generation time by exact
  statevector simulation and recorded in circuit metadata (the set of
  bitstrings whose ideal probability exceeds the median). This is
  exponential in width by definition; widths above HARD_WIDTH_LIMIT are
  refused outright rather than pretended at.
- Pass criterion per width (the standard 2-sigma rule, one-sided ~97.7%):
  mean heavy-output probability h passes iff
  h - 2*sqrt(h(1-h)/(n_circuits*shots)) > 2/3 — the aggregate binomial
  sigma of Cross et al. (Note: this is the standard's own formula; it does
  not model circuit-to-circuit variance.)
- Quantum Volume = 2^m for the largest passing width. Failing widths are
  reported as failed metrics, never omitted.

Defaults are honest-but-cheap for CI; publication-grade runs need >= 100
circuits per width (see docs/BENCHMARKS.md). Circuit count is a recorded
parameter, and quality issues mark sub-publication-grade and
sub-confidence-grade counts.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator
from qiskit import QuantumCircuit, qasm3, transpile
from qiskit.circuit.library import UnitaryGate
from qiskit.quantum_info import Statevector, random_unitary

from veriqant_bench.qpr._generated import Metric, MetricQuality, MetricStatistics

from .base import AnalysisResult, Benchmark, GeneratedCircuit
from .stats import bootstrap_mean_ci, degrade_zero_width_ci

CONFIDENCE = 0.95
HEAVY_THRESHOLD = 2.0 / 3.0
QV_BASIS = ["rz", "sx", "x", "cx"]
HARD_WIDTH_LIMIT = 12
"""Exact ideal simulation is exponential in width; above this we refuse."""
MIN_CIRCUITS_FOR_CONFIDENCE = 30
PUBLICATION_GRADE_CIRCUITS = 100


class QVParams(BaseModel):
    """Parameters of a quantum volume run."""

    model_config = ConfigDict(extra="forbid")

    widths: list[int] = Field(default=[2, 3, 4], min_length=1)
    """Circuit widths m (qubits = depth = m) to test."""
    circuits_per_width: int = Field(default=50, ge=5)
    """Random circuits per width. >= 100 for publication-grade claims."""
    bootstrap_resamples: int = Field(default=200, ge=50)

    @field_validator("widths")
    @classmethod
    def _valid_widths(cls, widths: list[int]) -> list[int]:
        if any(width < 2 for width in widths):
            raise ValueError("QV widths must be >= 2")
        if any(width > HARD_WIDTH_LIMIT for width in widths):
            raise ValueError(
                f"QV widths above {HARD_WIDTH_LIMIT} are refused: heavy-output "
                "computation requires exact ideal simulation, which is "
                "exponential in width"
            )
        if len(set(widths)) != len(widths):
            raise ValueError("widths must be distinct")
        return sorted(widths)


def heavy_outputs(probabilities: np.ndarray, width: int) -> tuple[list[str], float, float]:
    """Heavy-output set of an ideal distribution.

    Returns (sorted bitstrings with probability strictly above the median,
    median probability, total ideal heavy probability). Bitstrings follow
    the QPR convention (bit 0 rightmost), which matches the statevector
    index binary representation.
    """
    median = float(np.median(probabilities))
    heavy = sorted(
        format(index, f"0{width}b")
        for index, probability in enumerate(probabilities)
        if probability > median
    )
    ideal_heavy_probability = float(sum(probabilities[int(bitstring, 2)] for bitstring in heavy))
    return heavy, median, ideal_heavy_probability


class QuantumVolume(Benchmark[QVParams]):
    """Quantum Volume: heavy-output probability per width with the standard
    2-sigma pass criterion."""

    name = "qv"
    version = "0.2.0"
    params_model = QVParams

    def qpr_benchmark_id(self, params: QVParams) -> str:
        return "quantum_volume"

    def display_name(self, params: QVParams) -> str:
        return f"quantum volume (widths {', '.join(str(w) for w in params.widths)})"

    def generate(self, params: QVParams, seed: int) -> list[GeneratedCircuit]:
        rng = np.random.default_rng(seed)
        circuits: list[GeneratedCircuit] = []
        for width in params.widths:
            for sample in range(params.circuits_per_width):
                model_circuit = QuantumCircuit(width)
                for _layer in range(width):
                    order = rng.permutation(width)
                    for first, second in zip(order[0::2], order[1::2], strict=False):
                        unitary = random_unitary(4, seed=rng)
                        model_circuit.append(UnitaryGate(unitary.data), [int(first), int(second)])
                synthesized = transpile(
                    model_circuit,
                    basis_gates=QV_BASIS,
                    optimization_level=0,
                    seed_transpiler=int(rng.integers(2**31)),
                )
                probabilities = Statevector.from_instruction(synthesized).probabilities()
                heavy, median, ideal_heavy = heavy_outputs(np.asarray(probabilities), width)

                measured = QuantumCircuit(width, width)
                measured.compose(synthesized, inplace=True)
                measured.measure(range(width), range(width))
                circuits.append(
                    GeneratedCircuit(
                        name=f"qv_w{width}_s{sample}",
                        qasm3=qasm3.dumps(measured),
                        metadata={
                            "width": width,
                            "sample": sample,
                            "heavy_outputs": heavy,
                            "median_probability": median,
                            "ideal_heavy_probability": ideal_heavy,
                        },
                    )
                )
        return circuits

    def analyze(
        self,
        circuits: list[GeneratedCircuit],
        counts: list[dict[str, int]],
        shots: int,
        params: QVParams,
        execution_metadata: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        per_width_values: dict[int, list[float]] = {width: [] for width in params.widths}
        for circuit, circuit_counts in zip(circuits, counts, strict=True):
            width = int(circuit.metadata["width"])
            heavy = set(circuit.metadata["heavy_outputs"])
            total = sum(circuit_counts.values())
            heavy_count = sum(
                count for bitstring, count in circuit_counts.items() if bitstring in heavy
            )
            per_width_values[width].append(heavy_count / total)

        count_issues = self._circuit_count_issues(params.circuits_per_width)
        width_metrics: list[Metric] = []
        per_width_analysis: dict[str, dict[str, Any]] = {}
        passed_widths: list[int] = []
        for width in params.widths:
            values = per_width_values[width]
            n = len(values)
            mean = float(np.mean(values))
            # Aggregate binomial sigma over n_circuits * shots total samples,
            # per Cross et al. (their nh/(nc*ns) +- 2*sigma criterion).
            sigma = float(np.sqrt(max(mean * (1.0 - mean), 0.0) / (n * shots)))
            passed = mean - 2.0 * sigma > HEAVY_THRESHOLD
            if passed:
                passed_widths.append(width)
            lower, upper, std_error = bootstrap_mean_ci(
                values, n_resamples=params.bootstrap_resamples
            )
            quality = MetricQuality(
                reliable=not any(issue.startswith("qv.insufficient") for issue in count_issues),
                issues=list(count_issues) or None,
            )
            sample_size = n * shots
            ci_lower, ci_upper = min(lower, mean), max(upper, mean)
            width_metrics.append(
                Metric(
                    name=f"heavy_output_probability.width_{width}",
                    value=mean,
                    unit="probability",
                    statistics=MetricStatistics(
                        sample_size=sample_size,
                        confidence_level=CONFIDENCE,
                        ci_lower=ci_lower,
                        ci_upper=ci_upper,
                        std_error=std_error,
                        estimator="mean_bootstrap_percentile",
                    ),
                    quality=degrade_zero_width_ci(quality, ci_lower, ci_upper),
                )
            )
            width_metrics.append(
                Metric(
                    name=f"qv_pass.width_{width}",
                    value=1.0 if passed else 0.0,
                    statistics=MetricStatistics(
                        sample_size=sample_size,
                        confidence_level=0.977,
                        ci_lower=1.0 if passed else 0.0,
                        ci_upper=1.0 if passed else 0.0,
                        estimator="qv_two_sigma_one_sided",
                    ),
                    quality=quality,
                )
            )
            per_width_analysis[str(width)] = {
                "n_circuits": n,
                "mean_heavy_output_probability": mean,
                "sigma": sigma,
                "two_sigma_lower_bound": mean - 2.0 * sigma,
                "threshold": HEAVY_THRESHOLD,
                "passed": passed,
            }

        qv_issues = list(count_issues)
        if not passed_widths:
            qv_issues.append("qv.no_width_passed")
        elif self._has_gap(params.widths, passed_widths):
            qv_issues.append("qv.non_monotonic_pass_pattern")
        qv_value = float(2 ** max(passed_widths)) if passed_widths else 1.0
        qv_metric = Metric(
            name="quantum_volume",
            value=qv_value,
            statistics=MetricStatistics(
                sample_size=len(params.widths) * params.circuits_per_width * shots,
                confidence_level=0.977,
                ci_lower=qv_value,
                ci_upper=qv_value,
                estimator="qv_definition_max_passing_width",
            ),
            quality=MetricQuality(
                reliable=not any(issue.startswith("qv.insufficient") for issue in qv_issues),
                issues=qv_issues or None,
            ),
        )

        return AnalysisResult(
            metrics=[qv_metric, *width_metrics],
            analysis={
                "per_width": per_width_analysis,
                "passed_widths": passed_widths,
                "pass_criterion": "mean - 2*sqrt(mean*(1-mean)/(n_circuits*shots)) > 2/3",
            },
        )

    @staticmethod
    def _circuit_count_issues(circuits_per_width: int) -> list[str]:
        if circuits_per_width < MIN_CIRCUITS_FOR_CONFIDENCE:
            return ["qv.insufficient_circuits_for_confidence"]
        if circuits_per_width < PUBLICATION_GRADE_CIRCUITS:
            return ["qv.circuit_count_below_publication_grade"]
        return []

    @staticmethod
    def _has_gap(widths: list[int], passed_widths: list[int]) -> bool:
        """True when a width failed while a larger one passed."""
        if not passed_widths:
            return False
        largest_passing = max(passed_widths)
        return any(width < largest_passing and width not in passed_widths for width in widths)
