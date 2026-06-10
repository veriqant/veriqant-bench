"""Throughput benchmark: batched timed execution, distributional stats, and
the simulator-honesty quality flag."""

from __future__ import annotations

import pytest
from conftest import StaticAdapter

from veriqant_bench.benchmarks import run_benchmark
from veriqant_bench.benchmarks.throughput import (
    SIMULATOR_TIMING_ISSUE,
    Throughput,
    ThroughputParams,
)
from veriqant_bench.qpr import QuantumPerformanceRecord, verify_qpr_document

THROUGHPUT = Throughput()
PARAMS = ThroughputParams(width=2, depth=2, batch_size=3, batches=4)


@pytest.fixture
async def record() -> QuantumPerformanceRecord:
    return await run_benchmark(THROUGHPUT, StaticAdapter(), PARAMS, seed=11, shots=64)


def test_template_generation_is_deterministic_and_sized() -> None:
    first = THROUGHPUT.generate(PARAMS, seed=5)
    second = THROUGHPUT.generate(PARAMS, seed=5)
    assert [c.qasm3 for c in first] == [c.qasm3 for c in second]
    assert len(first) == PARAMS.batch_size
    assert ThroughputParams(batch_size=1).batch_size == 1
    assert len(THROUGHPUT.generate(ThroughputParams(batch_size=1), seed=5)) == 1


async def test_each_batch_is_timed_and_recorded(record: QuantumPerformanceRecord) -> None:
    assert record.results.analysis is not None
    batches = record.results.analysis["batches"]
    assert len(batches) == PARAMS.batches
    for index, batch in enumerate(batches):
        assert batch["batch"] == index
        assert batch["round_trip_seconds"] > 0
        assert batch["seed"] == 11 + index
        # The local adapter reports its queue/execution split.
        assert batch["adapter_timing"]["source"] == "local_state_transitions"
    # One raw entry per circuit per batch, indices referencing the template.
    assert len(record.results.raw) == PARAMS.batches * PARAMS.batch_size
    assert {raw.circuit_index for raw in record.results.raw} == set(range(PARAMS.batch_size))
    assert len(record.circuits) == PARAMS.batch_size
    # The whole repeated-execution record still passes independent verification.
    import json

    assert verify_qpr_document(json.loads(record.model_dump_json(exclude_none=True))).ok


async def test_distributional_stats_are_sane(record: QuantumPerformanceRecord) -> None:
    assert record.results.analysis is not None
    spread = record.results.analysis["round_trip_seconds"]
    q1, q3 = spread["iqr"]
    assert spread["min"] <= q1 <= spread["median"] <= q3 <= spread["max"]

    by_name = {metric.name: metric for metric in record.results.metrics}
    rtt = by_name["job_round_trip_seconds"]
    assert rtt.value > 0
    assert rtt.statistics.sample_size == PARAMS.batches
    assert rtt.unit == "seconds"
    shots_rate = by_name["sustained_shots_per_second"]
    expected_rate = PARAMS.batch_size * 64 / rtt.value
    assert shots_rate.value == pytest.approx(expected_rate, rel=0.5)
    layers = by_name["sequential_layers_per_second"]
    assert layers.value > 0
    assert PARAMS.template_layers == 2 * PARAMS.depth + 1


async def test_simulator_metrics_carry_the_honesty_flag(
    record: QuantumPerformanceRecord,
) -> None:
    for metric in record.results.metrics:
        assert metric.quality is not None
        assert not metric.quality.reliable
        assert SIMULATOR_TIMING_ISSUE in (metric.quality.issues or [])


def test_analyze_requires_batch_timing() -> None:
    circuits = THROUGHPUT.generate(PARAMS, seed=1)
    counts = [{"00": 64} for _ in circuits]
    with pytest.raises(ValueError, match="batch timing"):
        THROUGHPUT.analyze(circuits, counts, 64, PARAMS, None)
