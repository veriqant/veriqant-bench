"""Regenerate the committed report fixtures (manual, deliberate act).

    uv run python tests/report/make_fixtures.py
    UPDATE_GOLDEN=1 uv run pytest tests/report   # then refresh the HTML

Why these are committed files rather than computed at test time: benchmark
analysis runs through scipy/numpy, whose float results differ in the last
ulps across platforms/BLAS builds (confirmed: rb_1q and quantum_volume
fixtures sealed to different content hashes on macOS vs Linux CI while
every displayed value was identical). The QPR canonicalization ties the
seal to exact float bits — the documented v0.x limitation in
docs/QPR-SPEC.md §Canonicalization — so platform-portable golden tests
need platform-independent input bytes.

Therefore: records are built once, all float values are quantized to
QUANTIZE_SIG_DIGITS significant digits (far below display precision, far
above platform noise) BEFORE sealing, and the sealed JSON is committed.
Parsing a committed decimal literal yields bit-identical doubles on every
platform, so seals and the golden HTML are stable everywhere.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).parents[1]))  # tests/ for conftest
from conftest import StaticAdapter

from veriqant_bench.benchmarks import run_benchmark
from veriqant_bench.benchmarks.mirror import MirrorCircuits, MirrorParams
from veriqant_bench.benchmarks.qec.memory import (
    RepetitionMemory,
    RepetitionParams,
)
from veriqant_bench.benchmarks.qv import QuantumVolume, QVParams
from veriqant_bench.benchmarks.rb import RandomizedBenchmarking, RBParams
from veriqant_bench.benchmarks.throughput import SIMULATOR_TIMING_ISSUE
from veriqant_bench.qpr import (
    QuantumPerformanceRecord,
    dump_qpr,
    seal,
    sha256_hex,
    to_json_dict,
    verify_qpr_file,
)
from veriqant_bench.qpr._generated import (
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

FIXTURE_DIR = Path(__file__).parent / "data"
FIXED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
QUANTIZE_SIG_DIGITS = 9

_FIXED_PROVENANCE = Provenance(
    veriqant_bench_version="0.1.0",
    python_version="3.12.0",
    platform="golden-fixture",
    sdk_versions={"veriqant-bench": "0.1.0"},
)
_FIXED_TRANSPILATION = Transpilation(sdk="fixed", sdk_version="0.0.0", settings={})


def quantize_floats(value: Any) -> Any:
    """Round every float to QUANTIZE_SIG_DIGITS significant digits.

    Monotonic per-value, so ordered quantities (ci_lower <= value <=
    ci_upper) keep their ordering and the verifier's checks still hold."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return float(f"{value:.{QUANTIZE_SIG_DIGITS}g}")
    if isinstance(value, dict):
        return {key: quantize_floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [quantize_floats(item) for item in value]
    return value


def normalize(record: QuantumPerformanceRecord, index: int) -> QuantumPerformanceRecord:
    """Pin machine/run-dependent fields, quantize floats, reseal."""
    pinned = record.model_copy(
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
    document = quantize_floats(to_json_dict(pinned))
    return seal(QuantumPerformanceRecord.model_validate(document))


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
    record = await run_benchmark(
        RepetitionMemory(),
        StaticAdapter(),
        RepetitionParams(distances=[3, 5], rounds=5, criteria="ab-lq-2026"),
        seed=7,
        shots=200,
    )
    return normalize(record, 5)


def throughput_record() -> QuantumPerformanceRecord:
    """Handcrafted: real wall-clock timing can never be deterministic."""

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
    return normalize(record, 4)


async def build_all() -> dict[str, QuantumPerformanceRecord]:
    return {
        "rb": await rb_record(),
        "mirror": await mirror_record(),
        "qv": await qv_record(),
        "qec": await qec_record(),
        "throughput": throughput_record(),
    }


def main() -> None:
    FIXTURE_DIR.mkdir(exist_ok=True)
    records = asyncio.run(build_all())
    for name, record in records.items():
        path = FIXTURE_DIR / f"{name}.qpr.json"
        dump_qpr(record, path)
        report = verify_qpr_file(path)
        if not report.ok:
            raise SystemExit(f"fixture failed verification: {path}: {report.issues}")
        print(f"wrote {path} ({record.integrity.content_sha256[:12]})")
    print("now refresh the golden HTML: UPDATE_GOLDEN=1 uv run pytest tests/report")


if __name__ == "__main__":
    main()
