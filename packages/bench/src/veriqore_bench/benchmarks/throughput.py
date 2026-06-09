"""Throughput benchmark — Veriqore's own metric set, deliberately NOT CLOPS.

CLOPS (Wack et al., arXiv:2110.14108) is IBM's defined metric with a
specific protocol built on parameterized template circuits with runtime
parameter updates. This benchmark measures something related but different
— sequential round-trips of static circuit batches — and is therefore named
and reported under its own metric names. See docs/BENCHMARKS.md for the
precise definitions and the CLOPS relationship statement.

Protocol (suite_version 0.1.0):
- A template batch of B mirror circuits at fixed width/depth (seeded,
  deterministic) is executed R times sequentially, S shots each, with a
  per-batch derived seed (master seed + batch index).
- Per batch, wall-clock submit -> result ("round trip") is measured on the
  client. Where the adapter reports a queue/execution split it is recorded
  verbatim; where it can't, that inability is recorded too.

Metrics (distributional: median over batches, bootstrap percentile CIs):
- job_round_trip_seconds   — median wall-clock seconds per batch round trip.
- sustained_shots_per_second — median of (B*S)/round_trip per batch.
- sequential_layers_per_second — median of (B*L)/round_trip per batch,
  where L = 2*depth + 1 is the template circuit's layer count.

Honesty rule: on simulators these numbers measure the harness and host
machine, not a QPU. Every metric then carries quality.reliable=false with
issue 'timing.simulator_not_comparable_to_hardware'. The real consumer of
this benchmark is the live-hardware adapters.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from veriqore_bench.adapters import JobSpec, QPUAdapter
from veriqore_bench.qpr._generated import Metric, MetricQuality, MetricStatistics

from .base import (
    AnalysisResult,
    Benchmark,
    ExecutedCircuitCounts,
    ExecutionOutcome,
    GeneratedCircuit,
)
from .mirror import MirrorCircuits, MirrorParams
from .stats import bootstrap_rng, percentile_ci

CONFIDENCE = 0.95
SIMULATOR_TIMING_ISSUE = "timing.simulator_not_comparable_to_hardware"


class ThroughputParams(BaseModel):
    """Parameters of a throughput run."""

    model_config = ConfigDict(extra="forbid")

    width: int = Field(default=2, ge=1, le=6)
    """Template circuit width (qubits)."""
    depth: int = Field(default=4, ge=1)
    """Template mirror half-depth; layer count L = 2*depth + 1."""
    batch_size: int = Field(default=10, ge=1)
    """Circuits per batch (B)."""
    batches: int = Field(default=5, ge=3)
    """Sequential batch repetitions (R); >=3 for meaningful spread stats."""
    bootstrap_resamples: int = Field(default=200, ge=50)

    @property
    def template_layers(self) -> int:
        return 2 * self.depth + 1


class Throughput(Benchmark[ThroughputParams]):
    """Sequential batch throughput: client-side round-trip timing of static
    circuit batches (not CLOPS — see module docstring)."""

    name = "throughput"
    version = "0.1.0"
    params_model = ThroughputParams

    def display_name(self, params: ThroughputParams) -> str:
        return (
            f"sequential batch throughput ({params.batches} batches x "
            f"{params.batch_size} circuits, width {params.width})"
        )

    def generate(self, params: ThroughputParams, seed: int) -> list[GeneratedCircuit]:
        """The template batch: B mirror circuits at fixed width/depth."""
        template = MirrorCircuits().generate(
            MirrorParams(
                qubits=list(range(params.width)),
                depths=[params.depth],
                # MirrorParams floors samples at 2; trim afterwards for B=1.
                samples_per_depth=max(params.batch_size, 2),
            ),
            seed,
        )
        circuits = template[: params.batch_size]
        return [
            GeneratedCircuit(
                name=f"throughput_template_{index}",
                qasm3=circuit.qasm3,
                metadata={**circuit.metadata, "template_index": index},
            )
            for index, circuit in enumerate(circuits)
        ]

    async def execute(
        self,
        adapter: QPUAdapter,
        circuits: list[GeneratedCircuit],
        params: ThroughputParams,
        *,
        seed: int,
        shots: int,
        timeout: float = 600.0,
    ) -> ExecutionOutcome:
        """R sequential timed batches of the template circuits."""
        sources = [circuit.qasm3 for circuit in circuits]
        results: list[ExecutedCircuitCounts] = []
        batch_records: list[dict[str, Any]] = []
        first_submitted = None
        last_completed = None
        last_metadata: dict[str, Any] = {}
        for batch in range(params.batches):
            spec = JobSpec(circuits=sources, shots=shots, seed=seed + batch)
            start = time.perf_counter()
            handle = await adapter.submit(spec)
            job_result = await adapter.await_result(handle, timeout=timeout)
            round_trip = time.perf_counter() - start
            first_submitted = first_submitted or handle.submitted_at
            last_completed = job_result.completed_at
            last_metadata = job_result.metadata
            adapter_timing = job_result.metadata.get("timing") or {
                "available": False,
                "note": "adapter reports no queue/execution split",
            }
            batch_records.append(
                {
                    "batch": batch,
                    "seed": seed + batch,
                    "round_trip_seconds": round_trip,
                    "adapter_timing": adapter_timing,
                }
            )
            results.extend(
                ExecutedCircuitCounts(circuit_index=index, counts=counts)
                for index, counts in enumerate(job_result.counts)
            )
        assert first_submitted is not None and last_completed is not None
        return ExecutionOutcome(
            results=results,
            submitted_at=first_submitted,
            completed_at=last_completed,
            metadata={
                **last_metadata,
                "batches": batch_records,
                "timing_source": "client_wall_clock_per_batch",
                "is_simulator": adapter.capabilities().is_simulator,
            },
        )

    def analyze(
        self,
        circuits: list[GeneratedCircuit],
        counts: list[dict[str, int]],
        shots: int,
        params: ThroughputParams,
        execution_metadata: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        if not execution_metadata or "batches" not in execution_metadata:
            raise ValueError("throughput analysis requires batch timing from execute()")
        batches = execution_metadata["batches"]
        round_trips = [float(record["round_trip_seconds"]) for record in batches]
        shots_rates = [params.batch_size * shots / rt for rt in round_trips]
        layer_rates = [params.batch_size * params.template_layers / rt for rt in round_trips]

        is_simulator = bool(execution_metadata.get("is_simulator", False))
        quality = (
            MetricQuality(reliable=False, issues=[SIMULATOR_TIMING_ISSUE])
            if is_simulator
            else MetricQuality(reliable=True, issues=None)
        )

        def metric(name: str, values: list[float], unit: str) -> Metric:
            value = float(np.median(values))
            lower, upper, std_error = self._bootstrap_median(values, params)
            return Metric(
                name=name,
                value=value,
                unit=unit,
                statistics=MetricStatistics(
                    sample_size=len(values),
                    confidence_level=CONFIDENCE,
                    ci_lower=min(lower, value),
                    ci_upper=max(upper, value),
                    std_error=std_error,
                    estimator="median_over_batches_bootstrap",
                ),
                quality=quality,
            )

        quartile_1, quartile_3 = np.quantile(round_trips, [0.25, 0.75])
        return AnalysisResult(
            metrics=[
                metric("job_round_trip_seconds", round_trips, "seconds"),
                metric("sustained_shots_per_second", shots_rates, "shots/s"),
                metric("sequential_layers_per_second", layer_rates, "layers/s"),
            ],
            analysis={
                "batches": batches,
                "round_trip_seconds": {
                    "median": float(np.median(round_trips)),
                    "iqr": [float(quartile_1), float(quartile_3)],
                    "min": min(round_trips),
                    "max": max(round_trips),
                },
                "template": {
                    "width": params.width,
                    "depth": params.depth,
                    "layers": params.template_layers,
                    "batch_size": params.batch_size,
                },
                "timing_source": execution_metadata.get("timing_source"),
            },
        )

    @staticmethod
    def _bootstrap_median(
        values: list[float], params: ThroughputParams
    ) -> tuple[float, float, float]:
        rng = bootstrap_rng()
        data = np.asarray(values, dtype=float)
        medians = [
            float(np.median(rng.choice(data, size=data.size, replace=True)))
            for _ in range(params.bootstrap_resamples)
        ]
        lower, upper = percentile_ci(medians, CONFIDENCE)
        return lower, upper, float(np.std(medians))
