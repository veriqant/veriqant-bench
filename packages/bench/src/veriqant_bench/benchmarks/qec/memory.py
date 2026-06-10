"""QEC memory benchmarks: repetition code and rotated d=3 surface code.

Both run through the standard adapter path as OpenQASM 3 (mid-circuit
measurement + reset), are decoded with PyMatching inside analyze() (pure
function: detection events -> logical error counts), and record the decoder
identity verbatim. Post-selection is impossible by construction: every
measured shot enters the decoder, and the accounting (shots submitted ==
shots analyzed) is part of the record.

Caveats, stated rather than hidden:
- The repetition code corrects one error species only (bit flips). Its
  criteria scorecard demonstrates the machinery; a bit-flip code is not a
  full logical qubit.
- Surface d=5 (49 qubits) is out of the Aer product path (too heavy); it
  exists only in Stim-based validation tests. Distance 3 is the supported
  product configuration.
- CI-grade defaults keep runs in seconds. Publication-grade QEC claims need
  far higher shot counts (see docs/BENCHMARKS.md).
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from veriqant_bench.adapters import NoiseSpec, QPUAdapter
from veriqant_bench.qpr._generated import Metric, MetricQuality, MetricStatistics

from ..base import AnalysisResult, Benchmark, ExecutionOutcome, GeneratedCircuit
from ..stats import bootstrap_rng, percentile_ci, wilson_interval
from .baseline import PhysicalBaseline, analytic_baseline
from .criteria.framework import (
    SIMULATED_ISSUE,
    DistanceEvidence,
    LambdaStep,
    PostSelectionAccounting,
    QECEvidence,
    RateWithCI,
    Verdict,
    get_profile,
)
from .decoding import build_matching, decode_counts, decoder_info
from .schedule import MemorySchedule, repetition_memory, surface3_memory

CONFIDENCE = 0.95
SURFACE_PRODUCT_DISTANCE = 3


def error_per_round(p_total: float, rounds: int) -> float:
    """Convert a total logical error probability over R rounds into a
    per-round rate via p_total = (1 - (1-2*eps)^R) / 2."""
    capped = min(p_total, 0.4999999)
    return float(0.5 * (1.0 - (1.0 - 2.0 * capped) ** (1.0 / rounds)))


class _DistanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distance: int
    rounds: int
    basis: str
    shots: int
    logical_errors: int
    p_total: float
    eps: RateWithCI


class RepetitionParams(BaseModel):
    """Parameters of a repetition-code memory run."""

    model_config = ConfigDict(extra="forbid")

    distances: list[int] = Field(default=[3, 5, 7], min_length=1)
    rounds: int = Field(default=7, ge=1)
    criteria: str | None = None
    """Criteria profile id (e.g. 'ab-lq-2026'); omitted -> metrics only."""
    bootstrap_resamples: int = Field(default=200, ge=50)

    @field_validator("distances")
    @classmethod
    def _valid_distances(cls, distances: list[int]) -> list[int]:
        if any(d < 3 or d % 2 == 0 for d in distances):
            raise ValueError("distances must be odd integers >= 3")
        if len(set(distances)) != len(distances):
            raise ValueError("distances must be distinct")
        return sorted(distances)

    @model_validator(mode="after")
    def _sufficient_rounds(self) -> RepetitionParams:
        if self.rounds < max(self.distances):
            raise ValueError(
                f"rounds ({self.rounds}) < max distance ({max(self.distances)}): "
                "refused — fewer syndrome rounds than the code distance "
                "invalidates the error-rate claim (ab-lq-2026 criterion 3)"
            )
        return self


class SurfaceParams(BaseModel):
    """Parameters of a rotated d=3 surface-code memory run."""

    model_config = ConfigDict(extra="forbid")

    distance: int = Field(default=SURFACE_PRODUCT_DISTANCE)
    rounds: int = Field(default=3, ge=1)
    bases: list[Literal["z", "x"]] = Field(default=["z", "x"], min_length=1)
    criteria: str | None = None
    bootstrap_resamples: int = Field(default=200, ge=50)

    @field_validator("distance")
    @classmethod
    def _product_distance_only(cls, distance: int) -> int:
        if distance != SURFACE_PRODUCT_DISTANCE:
            raise ValueError(
                "the Aer product path supports surface distance 3 only; "
                "d=5 (49 qubits) exists solely in Stim-based validation tests"
            )
        return distance

    @field_validator("bases")
    @classmethod
    def _distinct_bases(cls, bases: list[str]) -> list[str]:
        if len(set(bases)) != len(bases):
            raise ValueError("bases must be distinct")
        return bases

    @model_validator(mode="after")
    def _sufficient_rounds(self) -> SurfaceParams:
        if self.rounds < self.distance:
            raise ValueError(
                f"rounds ({self.rounds}) < distance ({self.distance}): refused "
                "(ab-lq-2026 criterion 3)"
            )
        return self


class _QECMemoryBase[ParamsT: BaseModel](Benchmark[ParamsT]):
    """Shared execution enrichment + analysis machinery."""

    async def execute(
        self,
        adapter: QPUAdapter,
        circuits: list[GeneratedCircuit],
        params: ParamsT,
        *,
        seed: int,
        shots: int,
        timeout: float = 600.0,
    ) -> ExecutionOutcome:
        outcome = await super().execute(
            adapter, circuits, params, seed=seed, shots=shots, timeout=timeout
        )
        calibration = adapter.calibration_snapshot()
        outcome.metadata.update(
            {
                "is_simulator": adapter.capabilities().is_simulator,
                "noise_spec": None if calibration is None else calibration.data.get("noise_spec"),
            }
        )
        return outcome

    @staticmethod
    def _schedule_for(circuit: GeneratedCircuit) -> MemorySchedule:
        metadata = circuit.metadata
        if metadata["code"] == "repetition":
            return repetition_memory(int(metadata["distance"]), int(metadata["rounds"]))
        return surface3_memory(int(metadata["rounds"]), metadata["basis"])

    def _decode_circuit(self, circuit: GeneratedCircuit, counts: dict[str, int]) -> _DistanceResult:
        schedule = self._schedule_for(circuit)
        build_matching(schedule)  # validated here; decode_counts builds its own
        errors, shots = decode_counts(schedule, counts)
        p_total = errors / shots
        ci_lower_p, ci_upper_p = wilson_interval(errors, shots, CONFIDENCE)
        return _DistanceResult(
            distance=schedule.distance,
            rounds=schedule.rounds,
            basis=schedule.basis,
            shots=shots,
            logical_errors=errors,
            p_total=p_total,
            eps=RateWithCI(
                value=error_per_round(p_total, schedule.rounds),
                ci_lower=error_per_round(ci_lower_p, schedule.rounds),
                ci_upper=error_per_round(ci_upper_p, schedule.rounds),
                confidence_level=CONFIDENCE,
            ),
        )

    @staticmethod
    def _rate_metric(name: str, result: _DistanceResult) -> Metric:
        return Metric(
            name=name,
            value=result.eps.value,
            unit="probability",
            statistics=MetricStatistics(
                sample_size=result.shots,
                confidence_level=CONFIDENCE,
                ci_lower=result.eps.ci_lower,
                ci_upper=result.eps.ci_upper,
                estimator="mwpm_decode_wilson_per_round",
            ),
            quality=MetricQuality(reliable=True, issues=None),
        )

    @staticmethod
    def _accounting_metric(accounting: PostSelectionAccounting) -> Metric:
        return Metric(
            name="post_selection_fraction",
            value=accounting.fraction_discarded,
            unit="probability",
            statistics=MetricStatistics(
                sample_size=accounting.shots_submitted,
                confidence_level=CONFIDENCE,
                ci_lower=accounting.fraction_discarded,
                ci_upper=accounting.fraction_discarded,
                estimator="exact_accounting",
            ),
            quality=MetricQuality(reliable=True, issues=None),
        )

    @staticmethod
    def _verdict_metrics(profile_id: str, verdicts: list[Verdict], simulated: bool) -> list[Metric]:
        metrics: list[Metric] = []
        for verdict in verdicts:
            issues: list[str] = []
            if verdict.status == "not_evaluable":
                issues.append("verdict.not_evaluable")
            if simulated:
                issues.append(SIMULATED_ISSUE)
            reliable = verdict.status != "not_evaluable" and not simulated
            value = 1.0 if verdict.status == "pass" else 0.0
            metrics.append(
                Metric(
                    name=f"criteria.{profile_id}.{verdict.criterion}",
                    value=value,
                    statistics=MetricStatistics(
                        sample_size=1,
                        confidence_level=CONFIDENCE,
                        ci_lower=value,
                        ci_upper=value,
                        estimator="criterion_verdict",
                    ),
                    quality=MetricQuality(reliable=reliable, issues=issues or None),
                )
            )
        return metrics

    @staticmethod
    def _noise_from_metadata(execution_metadata: dict[str, Any] | None) -> NoiseSpec | None:
        if not execution_metadata:
            return None
        raw = execution_metadata.get("noise_spec")
        return None if raw is None else NoiseSpec.model_validate(raw)


class RepetitionMemory(_QECMemoryBase[RepetitionParams]):
    """Bit-flip repetition code memory: logical error rate vs. distance with
    Lambda suppression factors and optional criteria scorecard."""

    name = "qec_repetition"
    version = "0.1.0"
    params_model = RepetitionParams

    def qpr_benchmark_id(self, params: RepetitionParams) -> str:
        return "qec_repetition_memory"

    def display_name(self, params: RepetitionParams) -> str:
        distances = ",".join(str(d) for d in params.distances)
        return f"repetition-code memory (d={distances}, {params.rounds} rounds)"

    def generate(self, params: RepetitionParams, seed: int) -> list[GeneratedCircuit]:
        circuits = []
        for distance in params.distances:
            schedule = repetition_memory(distance, params.rounds)
            circuits.append(
                GeneratedCircuit(
                    name=f"qec_rep_d{distance}_r{params.rounds}",
                    qasm3=schedule.to_qasm3(),
                    metadata={
                        "code": "repetition",
                        "basis": "z",
                        "distance": distance,
                        "rounds": params.rounds,
                    },
                )
            )
        return circuits

    def analyze(
        self,
        circuits: list[GeneratedCircuit],
        counts: list[dict[str, int]],
        shots: int,
        params: RepetitionParams,
        execution_metadata: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        results = [
            self._decode_circuit(circuit, circuit_counts)
            for circuit, circuit_counts in zip(circuits, counts, strict=True)
        ]
        accounting = PostSelectionAccounting(
            shots_submitted=len(circuits) * shots,
            shots_analyzed=sum(result.shots for result in results),
        )
        lambda_steps, lambda_issues = _lambda_steps(results, params.bootstrap_resamples)

        metrics = [
            self._rate_metric(f"logical_error_per_round.distance_{r.distance}", r) for r in results
        ]
        for step, issues in zip(lambda_steps, lambda_issues, strict=True):
            metrics.append(
                Metric(
                    name=f"lambda.d{step.from_distance}_to_d{step.to_distance}",
                    value=step.value,
                    statistics=MetricStatistics(
                        sample_size=min(r.shots for r in results),
                        confidence_level=CONFIDENCE,
                        ci_lower=min(step.ci_lower, step.value),
                        ci_upper=max(step.ci_upper, step.value),
                        estimator="parametric_bootstrap_rate_ratio",
                    ),
                    quality=MetricQuality(reliable=not issues, issues=issues or None),
                )
            )
        metrics.append(self._accounting_metric(accounting))

        simulated = bool((execution_metadata or {}).get("is_simulator", False))
        noise = self._noise_from_metadata(execution_metadata)
        baseline = analytic_baseline(noise, repetition_memory(params.distances[0], params.rounds))

        analysis: dict[str, Any] = {
            "decoder": decoder_info(),
            "per_distance": {str(r.distance): r.model_dump() for r in results},
            "lambda_steps": [step.model_dump() for step in lambda_steps],
            "post_selection": accounting.model_dump(),
            "physical_baseline": None if baseline is None else baseline.model_dump(),
            "code_caveat": "bit-flip repetition code corrects one error species only",
        }

        if params.criteria is not None:
            evidence = QECEvidence(
                code="repetition",
                basis="z",
                distances=[
                    DistanceEvidence(
                        distance=r.distance,
                        rounds=r.rounds,
                        shots=r.shots,
                        logical_errors=r.logical_errors,
                        logical_error_per_round=r.eps,
                    )
                    for r in results
                ],
                lambda_steps=lambda_steps,
                physical_baseline=None if baseline is None else baseline.model_dump(),
                post_selection=accounting,
                simulated=simulated,
                noise_summary=None if noise is None else noise.model_dump(exclude_none=True),
            )
            profile = get_profile(params.criteria)
            verdicts = profile.evaluate(evidence)
            metrics.extend(self._verdict_metrics(profile.id, verdicts, simulated))
            analysis["criteria"] = {
                "profile": profile.id,
                "version": profile.version,
                "citation": profile.citation,
                "simulated": simulated,
                "verdicts": [verdict.model_dump() for verdict in verdicts],
            }

        return AnalysisResult(metrics=metrics, analysis=analysis)


class SurfaceMemory(_QECMemoryBase[SurfaceParams]):
    """Rotated d=3 surface code memory in both bases, MWPM-decoded, with
    optional criteria scorecard (the binding rate is the worse basis)."""

    name = "qec_surface"
    version = "0.1.0"
    params_model = SurfaceParams

    def qpr_benchmark_id(self, params: SurfaceParams) -> str:
        return "qec_surface_memory"

    def display_name(self, params: SurfaceParams) -> str:
        return (
            f"rotated surface code d={params.distance} memory "
            f"(bases {','.join(params.bases)}, {params.rounds} rounds)"
        )

    def generate(self, params: SurfaceParams, seed: int) -> list[GeneratedCircuit]:
        return [
            GeneratedCircuit(
                name=f"qec_surface_d3_{basis}_r{params.rounds}",
                qasm3=surface3_memory(params.rounds, basis).to_qasm3(),
                metadata={
                    "code": "surface",
                    "basis": basis,
                    "distance": params.distance,
                    "rounds": params.rounds,
                },
            )
            for basis in params.bases
        ]

    def analyze(
        self,
        circuits: list[GeneratedCircuit],
        counts: list[dict[str, int]],
        shots: int,
        params: SurfaceParams,
        execution_metadata: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        results = [
            self._decode_circuit(circuit, circuit_counts)
            for circuit, circuit_counts in zip(circuits, counts, strict=True)
        ]
        accounting = PostSelectionAccounting(
            shots_submitted=len(circuits) * shots,
            shots_analyzed=sum(result.shots for result in results),
        )
        metrics = [
            self._rate_metric(f"logical_error_per_round.basis_{r.basis}", r) for r in results
        ]
        metrics.append(self._accounting_metric(accounting))

        simulated = bool((execution_metadata or {}).get("is_simulator", False))
        noise = self._noise_from_metadata(execution_metadata)
        baseline = analytic_baseline(noise, surface3_memory(params.rounds, params.bases[0]))

        worst = max(results, key=lambda r: r.eps.value)
        analysis: dict[str, Any] = {
            "decoder": decoder_info(),
            "per_basis": {r.basis: r.model_dump() for r in results},
            "binding_basis": worst.basis,
            "post_selection": accounting.model_dump(),
            "physical_baseline": None if baseline is None else baseline.model_dump(),
        }

        if params.criteria is not None:
            evidence = QECEvidence(
                code="surface",
                basis=f"worst_of_{'_'.join(params.bases)}",
                distances=[
                    DistanceEvidence(
                        distance=worst.distance,
                        rounds=worst.rounds,
                        shots=worst.shots,
                        logical_errors=worst.logical_errors,
                        logical_error_per_round=worst.eps,
                    )
                ],
                lambda_steps=[],
                physical_baseline=None if baseline is None else baseline.model_dump(),
                post_selection=accounting,
                simulated=simulated,
                noise_summary=None if noise is None else noise.model_dump(exclude_none=True),
            )
            profile = get_profile(params.criteria)
            verdicts = profile.evaluate(evidence)
            metrics.extend(self._verdict_metrics(profile.id, verdicts, simulated))
            analysis["criteria"] = {
                "profile": profile.id,
                "version": profile.version,
                "citation": profile.citation,
                "simulated": simulated,
                "verdicts": [verdict.model_dump() for verdict in verdicts],
            }

        return AnalysisResult(metrics=metrics, analysis=analysis)


def _lambda_steps(
    results: list[_DistanceResult], resamples: int
) -> tuple[list[LambdaStep], list[list[str]]]:
    """Suppression factors between consecutive distances with parametric
    bootstrap CIs (resample logical error counts binomially, re-derive the
    per-round rates, take the ratio)."""
    ordered = sorted(results, key=lambda r: r.distance)
    rng = bootstrap_rng()
    steps: list[LambdaStep] = []
    issues_per_step: list[list[str]] = []
    for smaller, larger in pairwise(ordered):
        issues: list[str] = []

        def eps_floor(result: _DistanceResult) -> float:
            return error_per_round(0.5 / result.shots, result.rounds)

        if smaller.logical_errors == 0 and larger.logical_errors == 0:
            # Zero errors at both distances: each rate is only bounded from
            # above, so their ratio is unresolved at this shot count. An
            # honest non-result, never a Lambda of 0 or infinity.
            issues.append("lambda.unresolved_zero_errors")
            steps.append(
                LambdaStep(
                    from_distance=smaller.distance,
                    to_distance=larger.distance,
                    value=1.0,
                    ci_lower=0.0,
                    ci_upper=float(2 * larger.shots),
                    resolved=False,
                )
            )
            issues_per_step.append(issues)
            continue

        larger_eps = larger.eps.value
        if larger.logical_errors == 0:
            larger_eps = eps_floor(larger)
            issues.append("lambda.denominator_zero_floored")
        value = smaller.eps.value / larger_eps if larger_eps > 0 else float(smaller.shots)

        samples: list[float] = []
        for _ in range(resamples):
            p_small = rng.binomial(smaller.shots, smaller.p_total) / smaller.shots
            p_large = rng.binomial(larger.shots, larger.p_total) / larger.shots
            eps_small = error_per_round(p_small, smaller.rounds)
            eps_large = max(error_per_round(p_large, larger.rounds), eps_floor(larger))
            if eps_large > 0:
                samples.append(eps_small / eps_large)
        if samples:
            ci_lower, ci_upper = percentile_ci(samples, CONFIDENCE)
        else:  # pragma: no cover - requires pathological inputs
            ci_lower, ci_upper = 0.0, float(2 * larger.shots)
            issues.append("lambda.bootstrap_failed")
        steps.append(
            LambdaStep(
                from_distance=smaller.distance,
                to_distance=larger.distance,
                value=float(value),
                ci_lower=float(ci_lower),
                ci_upper=float(ci_upper),
            )
        )
        issues_per_step.append(issues)
    return steps, issues_per_step


__all__ = [
    "PhysicalBaseline",
    "RepetitionMemory",
    "RepetitionParams",
    "SurfaceMemory",
    "SurfaceParams",
    "error_per_round",
]
