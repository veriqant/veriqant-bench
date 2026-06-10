"""Standard Clifford randomized benchmarking (1Q and 2Q).

Methodology (suite_version 0.1.0):
- For each sequence length m: sample m uniformly random Cliffords (Qiskit's
  Clifford utilities), append the inverse of their product, measure. Ideal
  outcome is |0...0>.
- Survival probability p(m) is fit to A·alpha^m + B (scipy curve_fit,
  bounded to [0,1]^3).
- Error per Clifford: EPC = (d-1)/d · (1-alpha), d = 2^n.
- Uncertainty: nonparametric bootstrap over sequences — resample the
  per-sequence survival values within each length, refit, percentile CI.
- Fit quality gates (R², convergence, amplitude/decay-rate sanity) mark the
  metric quality.reliable=false instead of hiding a bad fit.

Everything crossing the adapter boundary is OpenQASM 3; Qiskit objects never
leak out of generate().
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator
from qiskit import QuantumCircuit, qasm3
from qiskit.quantum_info import Clifford, random_clifford
from scipy.optimize import curve_fit

from veriqant_bench.qpr._generated import Metric, MetricQuality, MetricStatistics

from .base import AnalysisResult, Benchmark, GeneratedCircuit
from .stats import bootstrap_rng, percentile_ci

CONFIDENCE = 0.95
R_SQUARED_THRESHOLD = 0.9
AMPLITUDE_THRESHOLD = 0.1
DECAY_LOWER_BOUND = 1e-3
MIN_BOOTSTRAP_SUCCESSES = 50
CEILING_BASELINE = 0.95


class RBParams(BaseModel):
    """Parameters of a randomized benchmarking run."""

    model_config = ConfigDict(extra="forbid")

    qubits: list[int] = Field(default=[0], min_length=1, max_length=2)
    """Target qubits: one for 1Q RB, two for 2Q RB."""
    lengths: list[int] = Field(default=[1, 2, 4, 8, 16, 32], min_length=3)
    """Clifford sequence lengths (need >=3 points to identify A, alpha, B)."""
    samples_per_length: int = Field(default=10, ge=2)
    """Independent random sequences per length (bootstrap needs >=2)."""
    bootstrap_resamples: int = Field(default=200, ge=50)

    @field_validator("qubits")
    @classmethod
    def _distinct_qubits(cls, qubits: list[int]) -> list[int]:
        if len(set(qubits)) != len(qubits):
            raise ValueError("qubits must be distinct")
        if any(qubit < 0 for qubit in qubits):
            raise ValueError("qubit indices must be >= 0")
        return qubits

    @field_validator("lengths")
    @classmethod
    def _valid_lengths(cls, lengths: list[int]) -> list[int]:
        if any(length < 1 for length in lengths):
            raise ValueError("sequence lengths must be >= 1")
        if len(set(lengths)) != len(lengths):
            raise ValueError("sequence lengths must be distinct")
        return sorted(lengths)


def _decay_model(m: np.ndarray, amplitude: float, alpha: float, baseline: float) -> np.ndarray:
    result: np.ndarray = amplitude * np.power(alpha, m) + baseline
    return result


class FitResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amplitude: float
    alpha: float
    baseline: float
    r_squared: float
    converged: bool


def fit_rb_decay(lengths: list[int], mean_survivals: list[float], dim: int) -> FitResult:
    """Fit A·alpha^m + B to per-length mean survival probabilities."""
    m = np.asarray(lengths, dtype=float)
    y = np.asarray(mean_survivals, dtype=float)
    baseline_guess = 1.0 / dim
    p0 = (max(float(y[0]) - baseline_guess, 0.1), 0.95, baseline_guess)
    try:
        popt, _ = curve_fit(
            _decay_model,
            m,
            y,
            p0=p0,
            bounds=([0.0, 0.0, 0.0], [1.0, 1.0, 1.0]),
            maxfev=10_000,
        )
    except (RuntimeError, ValueError):
        return FitResult(
            amplitude=0.0, alpha=0.0, baseline=baseline_guess, r_squared=0.0, converged=False
        )
    residuals = y - _decay_model(m, *popt)
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    # A flat curve (ss_tot ~ 0) that the model reproduces is a perfect fit,
    # not an undefined one.
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else (1.0 if ss_res < 1e-9 else 0.0)
    return FitResult(
        amplitude=float(popt[0]),
        alpha=float(popt[1]),
        baseline=float(popt[2]),
        r_squared=r_squared,
        converged=True,
    )


def _fit_quality(fit: FitResult) -> MetricQuality:
    issues: list[str] = []
    if not fit.converged:
        issues.append("fit.did_not_converge")
    else:
        if fit.r_squared < R_SQUARED_THRESHOLD:
            issues.append("fit.r_squared_below_threshold")
        if fit.amplitude < AMPLITUDE_THRESHOLD and fit.amplitude + fit.baseline < CEILING_BASELINE:
            # Amplitude collapse means alpha is unidentifiable — unless the
            # whole curve sits at the ceiling (A+B ~ 1, i.e. no measurable
            # decay): a near-perfect device is a result, not a bad fit. On
            # the alpha~1 ridge the optimizer splits A+B=1 arbitrarily, so
            # the level A+B is the meaningful quantity, not A or B alone.
            issues.append("fit.amplitude_collapsed")
        if fit.alpha <= DECAY_LOWER_BOUND:
            issues.append("fit.decay_rate_at_lower_bound")
    return MetricQuality(reliable=not issues, issues=issues or None)


def epc_from_alpha(alpha: float, dim: int) -> float:
    """Error per Clifford from the RB decay rate."""
    return (dim - 1) / dim * (1.0 - alpha)


class RandomizedBenchmarking(Benchmark[RBParams]):
    """Clifford randomized benchmarking (1Q/2Q): error per Clifford with
    bootstrap confidence intervals."""

    name = "rb"
    version = "0.1.0"
    params_model = RBParams

    def qpr_benchmark_id(self, params: RBParams) -> str:
        return f"rb_{len(params.qubits)}q"

    def display_name(self, params: RBParams) -> str:
        return f"{len(params.qubits)}-qubit Clifford randomized benchmarking"

    def generate(self, params: RBParams, seed: int) -> list[GeneratedCircuit]:
        rng = np.random.default_rng(seed)
        n = len(params.qubits)
        circuits: list[GeneratedCircuit] = []
        for length in params.lengths:
            for sample in range(params.samples_per_length):
                circuit = QuantumCircuit(n, n)
                composed = Clifford(QuantumCircuit(n))
                for _ in range(length):
                    clifford = random_clifford(n, seed=rng)
                    circuit.compose(clifford.to_circuit(), inplace=True)
                    composed = composed.compose(clifford)
                circuit.compose(composed.adjoint().to_circuit(), inplace=True)
                circuit.measure(range(n), range(n))
                circuits.append(
                    GeneratedCircuit(
                        name=f"rb_{n}q_len{length}_s{sample}",
                        qasm3=qasm3.dumps(circuit),
                        metadata={"length": length, "sample": sample},
                    )
                )
        return circuits

    def analyze(
        self,
        circuits: list[GeneratedCircuit],
        counts: list[dict[str, int]],
        shots: int,
        params: RBParams,
        execution_metadata: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        n = len(params.qubits)
        dim = 2**n
        target = "0" * n

        survivals: dict[int, list[float]] = {length: [] for length in params.lengths}
        for circuit, circuit_counts in zip(circuits, counts, strict=True):
            length = int(circuit.metadata["length"])
            total = sum(circuit_counts.values())
            survivals[length].append(circuit_counts.get(target, 0) / total)

        lengths = sorted(survivals)
        means = [float(np.mean(survivals[length])) for length in lengths]
        fit = fit_rb_decay(lengths, means, dim)
        epc = epc_from_alpha(fit.alpha, dim)

        epc_samples, alpha_samples = self._bootstrap(survivals, dim, params)
        quality = _fit_quality(fit)
        if fit.converged and len(epc_samples) < MIN_BOOTSTRAP_SUCCESSES:
            quality = MetricQuality(
                reliable=False,
                issues=[*(quality.issues or []), "bootstrap.insufficient_refit_successes"],
            )

        if epc_samples:
            epc_ci = percentile_ci(epc_samples, CONFIDENCE)
            alpha_ci = percentile_ci(alpha_samples, CONFIDENCE)
            epc_std = float(np.std(epc_samples))
            alpha_std = float(np.std(alpha_samples))
        else:
            # No usable refits: publish the maximally honest interval.
            epc_ci, alpha_ci = (0.0, 1.0), (0.0, 1.0)
            epc_std = alpha_std = 0.0

        sample_size = len(params.lengths) * params.samples_per_length * shots
        estimator = "rb_exponential_fit_bootstrap"

        def metric(name: str, value: float, ci: tuple[float, float], std: float) -> Metric:
            return Metric(
                name=name,
                value=value,
                unit="probability",
                qubits=params.qubits,
                statistics=MetricStatistics(
                    sample_size=sample_size,
                    confidence_level=CONFIDENCE,
                    ci_lower=min(ci[0], value),
                    ci_upper=max(ci[1], value),
                    std_error=std,
                    estimator=estimator,
                ),
                quality=quality,
            )

        return AnalysisResult(
            metrics=[
                metric("error_per_clifford", epc, epc_ci, epc_std),
                metric("rb_decay_rate", fit.alpha, alpha_ci, alpha_std),
            ],
            analysis={
                "survival_means": {
                    str(length): mean for length, mean in zip(lengths, means, strict=True)
                },
                "fit": fit.model_dump(),
                "bootstrap": {
                    "requested_resamples": params.bootstrap_resamples,
                    "successful_refits": len(epc_samples),
                },
            },
        )

    def _bootstrap(
        self, survivals: dict[int, list[float]], dim: int, params: RBParams
    ) -> tuple[list[float], list[float]]:
        """Resample sequences within each length, refit, collect estimates."""
        rng = bootstrap_rng()
        lengths = sorted(survivals)
        epc_samples: list[float] = []
        alpha_samples: list[float] = []
        for _ in range(params.bootstrap_resamples):
            resampled_means = [
                float(np.mean(rng.choice(survivals[length], size=len(survivals[length]))))
                for length in lengths
            ]
            refit = fit_rb_decay(lengths, resampled_means, dim)
            if not refit.converged:
                continue
            epc_samples.append(epc_from_alpha(refit.alpha, dim))
            alpha_samples.append(refit.alpha)
        return epc_samples, alpha_samples
