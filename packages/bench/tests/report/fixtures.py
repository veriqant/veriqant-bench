"""Deterministic QPR fixtures for report tests.

rb/mirror/qv records come from real benchmark runs on the StaticAdapter,
then get normalized: identity fields, provenance, and transpilation
metadata are pinned and the record resealed, making content bit-stable
across machines and Python patch versions (the golden-file test depends on
this). The throughput record is handcrafted because real wall-clock timing
can never be deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from conftest import StaticAdapter

from veriqore_bench.benchmarks import run_benchmark
from veriqore_bench.benchmarks.mirror import MirrorCircuits, MirrorParams
from veriqore_bench.benchmarks.qec.memory import RepetitionMemory, RepetitionParams
from veriqore_bench.benchmarks.qv import QuantumVolume, QVParams
from veriqore_bench.benchmarks.rb import RandomizedBenchmarking, RBParams
from veriqore_bench.benchmarks.throughput import SIMULATOR_TIMING_ISSUE
from veriqore_bench.qpr import QuantumPerformanceRecord, seal, sha256_hex
from veriqore_bench.qpr._generated import (
    Benchmark,
    Circuit,
    Device,
    Execution,
    Integrity,
    Metric,
    MetricQuality,
    MetricStatistics,
    Provenance,
    Provider,
    RawResult,
    Results,
    Transpilation,
)

FIXED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

_FIXED_PROVENANCE = Provenance(
    veriqore_bench_version="0.1.0",
    python_version="3.12.0",
    platform="golden-fixture",
    sdk_versions={"veriqore-bench": "0.1.0"},
)
_FIXED_TRANSPILATION = Transpilation(sdk="fixed", sdk_version="0.0.0", settings={})


def normalize(record: QuantumPerformanceRecord, index: int) -> QuantumPerformanceRecord:
    """Pin all machine/run-dependent fields and reseal."""
    return seal(
        record.model_copy(
            update={
                "record_id": UUID(int=index),
                "created_at": FIXED_AT,
                "execution": record.execution.model_copy(
                    update={
                        "submitted_at": FIXED_AT,
                        "completed_at": FIXED_AT,
                        "transpilation": _FIXED_TRANSPILATION,
                    }
                ),
                "provenance": _FIXED_PROVENANCE,
            }
        )
    )


async def rb_record() -> QuantumPerformanceRecord:
    record = await run_benchmark(
        RandomizedBenchmarking(),
        StaticAdapter(),
        RBParams(qubits=[0], lengths=[1, 2, 4], samples_per_length=2),
        seed=7,
        shots=64,
    )
    return normalize(record, 1)


async def mirror_record() -> QuantumPerformanceRecord:
    record = await run_benchmark(
        MirrorCircuits(),
        StaticAdapter(),
        MirrorParams(qubits=[0, 1], depths=[1, 2], samples_per_depth=2),
        seed=7,
        shots=64,
    )
    return normalize(record, 2)


async def qv_record() -> QuantumPerformanceRecord:
    record = await run_benchmark(
        QuantumVolume(),
        StaticAdapter(),
        QVParams(widths=[2], circuits_per_width=5),
        seed=7,
        shots=64,
    )
    return normalize(record, 3)


async def qec_record() -> QuantumPerformanceRecord:
    """StaticAdapter returns all-zeros: zero logical errors everywhere,
    unresolved Lambda, not_evaluable breakeven — a deterministic scorecard
    exercising the grey/na rendering paths plus the simulator watermark."""
    record = await run_benchmark(
        RepetitionMemory(),
        StaticAdapter(),
        RepetitionParams(distances=[3, 5], rounds=5, criteria="ab-lq-2026"),
        seed=7,
        shots=200,
    )
    return normalize(record, 5)


def throughput_record() -> QuantumPerformanceRecord:
    def metric(name: str, value: float, unit: str) -> Metric:
        return Metric(
            name=name,
            value=value,
            unit=unit,
            statistics=MetricStatistics(
                sample_size=4,
                confidence_level=0.95,
                ci_lower=value * 0.9,
                ci_upper=value * 1.1,
                estimator="median_over_batches_bootstrap",
            ),
            quality=MetricQuality(reliable=False, issues=[SIMULATOR_TIMING_ISSUE]),
        )

    qasm = 'OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[2] q;\nbit[2] c;\nc = measure q;\n'
    record = QuantumPerformanceRecord(
        qpr_version="0.2.0",
        record_id=UUID(int=4),
        created_at=FIXED_AT,
        benchmark=Benchmark(
            id="throughput",
            display_name="sequential batch throughput (fixture)",
            suite_version="0.1.0",
            parameters={"batches": 4, "batch_size": 3, "width": 2, "depth": 2},
        ),
        provider=Provider(name="local", adapter="static_test"),
        device=Device(name="static_device", num_qubits=4, simulator=True),
        execution=Execution(
            seed=7,
            shots=64,
            live=False,
            transpilation=_FIXED_TRANSPILATION,
            submitted_at=FIXED_AT,
            completed_at=FIXED_AT,
        ),
        circuits=[Circuit(index=0, name="t0", qasm3=qasm, qasm3_sha256=sha256_hex(qasm))],
        results=Results(
            raw=[RawResult(circuit_index=0, shots=64, counts={"00": 64})],
            metrics=[
                metric("job_round_trip_seconds", 0.0421, "seconds"),
                metric("sustained_shots_per_second", 4561.0, "shots/s"),
                metric("sequential_layers_per_second", 356.0, "layers/s"),
            ],
            analysis={
                "batches": [
                    {"batch": 0, "round_trip_seconds": 0.0418},
                    {"batch": 1, "round_trip_seconds": 0.0421},
                    {"batch": 2, "round_trip_seconds": 0.0436},
                    {"batch": 3, "round_trip_seconds": 0.0419},
                ],
                "round_trip_seconds": {
                    "median": 0.042,
                    "iqr": [0.0419, 0.0425],
                    "min": 0.0418,
                    "max": 0.0436,
                },
                "timing_source": "client_wall_clock_per_batch",
            },
        ),
        provenance=_FIXED_PROVENANCE,
        integrity=Integrity(content_sha256="0" * 64),
    )
    return seal(record)
