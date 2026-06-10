"""QEC closed-loop validation.

Stim is the ground-truth oracle: the Aer product path (QASM3 generation ->
execution -> decoding) and a Stim mirror of the *same schedule* are decoded
through the same pipeline; their logical error rates must agree
statistically. Suppression is asserted in both directions: sub-threshold
noise gives Lambda significantly > 1 (scalable_parameters passes),
super-threshold noise does not (it fails).

CI-grade shot counts keep this in seconds; publication-grade QEC runs need
orders of magnitude more shots (documented in BENCHMARKS.md).
"""

from __future__ import annotations

import json
import math

import pytest

from veriqant_bench.adapters import NoiseSpec
from veriqant_bench.adapters.aer import AerSimulatorAdapter
from veriqant_bench.benchmarks import run_benchmark
from veriqant_bench.benchmarks.qec.decoding import decode_bits, decode_counts
from veriqant_bench.benchmarks.qec.memory import (
    RepetitionMemory,
    RepetitionParams,
    SurfaceMemory,
    SurfaceParams,
)
from veriqant_bench.benchmarks.qec.schedule import repetition_memory, surface3_memory
from veriqant_bench.benchmarks.qec.validation import sample_stim_bits
from veriqant_bench.qpr import QuantumPerformanceRecord, verify_qpr_document

pytestmark = pytest.mark.slow

SEED = 42
SHOTS = 2000
SUB_THRESHOLD = NoiseSpec(depolarizing_1q=0.005, depolarizing_2q=0.01)
# Strong enough that every distance shows measurable errors at CI-grade
# shot counts (so Lambda is resolved), still clearly below threshold.
MODERATE_SUB_THRESHOLD = NoiseSpec(depolarizing_1q=0.01, depolarizing_2q=0.04)
SUPER_THRESHOLD = NoiseSpec(depolarizing_1q=0.10, depolarizing_2q=0.30)


def assert_rates_agree(p_a: float, p_b: float, shots: int, label: str) -> None:
    """Two independent binomial estimates of the same rate must agree
    within 4 combined standard errors (plus a small absolute slack)."""
    combined_se = math.sqrt((p_a * (1 - p_a) + p_b * (1 - p_b)) / shots)
    tolerance = 4.0 * combined_se + 0.005
    assert abs(p_a - p_b) <= tolerance, (
        f"{label}: aer={p_a:.4f} vs stim={p_b:.4f}, tolerance {tolerance:.4f}"
    )


def assert_verifies(record: QuantumPerformanceRecord) -> None:
    report = verify_qpr_document(json.loads(record.model_dump_json(exclude_none=True)))
    assert report.ok, [str(issue) for issue in report.issues]


@pytest.mark.timeout(180)
async def test_repetition_aer_agrees_with_stim_oracle() -> None:
    params = RepetitionParams(distances=[3, 5], rounds=5)
    record = await run_benchmark(
        RepetitionMemory(),
        AerSimulatorAdapter(noise=SUB_THRESHOLD),
        params,
        seed=SEED,
        shots=SHOTS,
    )
    assert_verifies(record)
    assert record.results.analysis is not None
    per_distance = record.results.analysis["per_distance"]

    for distance in (3, 5):
        schedule = repetition_memory(distance, 5)
        stim_bits = sample_stim_bits(schedule, SUB_THRESHOLD, SHOTS, seed=SEED)
        stim_errors = int(decode_bits(schedule, stim_bits).sum())
        aer_detail = per_distance[str(distance)]
        assert_rates_agree(
            aer_detail["p_total"], stim_errors / SHOTS, SHOTS, f"repetition d={distance}"
        )


@pytest.mark.timeout(180)
async def test_suppression_passes_sub_threshold_and_fails_super_threshold() -> None:
    params = RepetitionParams(distances=[3, 5, 7], rounds=7, criteria="ab-lq-2026")

    good = await run_benchmark(
        RepetitionMemory(),
        AerSimulatorAdapter(noise=MODERATE_SUB_THRESHOLD),
        params,
        seed=SEED,
        shots=SHOTS,
    )
    assert_verifies(good)
    good_metrics = {m.name: m for m in good.results.metrics}
    assert good_metrics["criteria.ab-lq-2026.scalable_parameters"].value == 1.0
    assert good_metrics["criteria.ab-lq-2026.breakeven"].value == 1.0
    # Simulator honesty: every verdict carries the machine-readable flag.
    for name, metric in good_metrics.items():
        if name.startswith("criteria."):
            assert metric.quality is not None
            assert "simulated_noise_model_not_hardware" in (metric.quality.issues or [])
            assert not metric.quality.reliable

    bad = await run_benchmark(
        RepetitionMemory(),
        AerSimulatorAdapter(noise=SUPER_THRESHOLD),
        params,
        seed=SEED,
        shots=SHOTS,
    )
    assert_verifies(bad)
    bad_metrics = {m.name: m for m in bad.results.metrics}
    assert bad_metrics["criteria.ab-lq-2026.scalable_parameters"].value == 0.0
    assert bad.results.analysis is not None
    lambda_steps = bad.results.analysis["lambda_steps"]
    assert any(step["ci_lower"] <= 1.0 for step in lambda_steps)


@pytest.mark.timeout(180)
async def test_accounting_is_exact() -> None:
    params = RepetitionParams(distances=[3], rounds=3)
    record = await run_benchmark(
        RepetitionMemory(),
        AerSimulatorAdapter(noise=SUB_THRESHOLD),
        params,
        seed=SEED,
        shots=500,
    )
    accounting = {m.name: m for m in record.results.metrics}["post_selection_fraction"]
    assert accounting.value == 0.0
    assert record.results.analysis is not None
    post_selection = record.results.analysis["post_selection"]
    assert post_selection["shots_submitted"] == post_selection["shots_analyzed"] == 500


@pytest.mark.timeout(300)
async def test_surface_d3_ideal_and_noisy_vs_stim() -> None:
    ideal_params = SurfaceParams(rounds=3, criteria="ab-lq-2026")
    ideal = await run_benchmark(
        SurfaceMemory(), AerSimulatorAdapter(), ideal_params, seed=SEED, shots=500
    )
    assert_verifies(ideal)
    for metric in ideal.results.metrics:
        if metric.name.startswith("logical_error_per_round"):
            assert metric.value < 0.01, metric.name
    verdicts = {m.name: m for m in ideal.results.metrics}
    breakeven = verdicts["criteria.ab-lq-2026.breakeven"]
    assert breakeven.quality is not None
    assert "verdict.not_evaluable" in (breakeven.quality.issues or [])

    noise = NoiseSpec(depolarizing_1q=0.002, depolarizing_2q=0.01)
    noisy = await run_benchmark(
        SurfaceMemory(),
        AerSimulatorAdapter(noise=noise),
        SurfaceParams(rounds=3),
        seed=SEED,
        shots=1000,
    )
    assert_verifies(noisy)
    assert noisy.results.analysis is not None
    per_basis = noisy.results.analysis["per_basis"]

    for basis in ("z", "x"):
        schedule = surface3_memory(3, basis)
        stim_bits = sample_stim_bits(schedule, noise, 1000, seed=SEED)
        stim_errors = int(decode_bits(schedule, stim_bits).sum())
        assert_rates_agree(
            per_basis[basis]["p_total"], stim_errors / 1000, 1000, f"surface basis={basis}"
        )


@pytest.mark.timeout(120)
async def test_stim_oracle_decodes_d5_surface_beyond_product_path() -> None:
    """d=5 surface stays out of the Aer product path; demonstrate the
    repetition pipeline at scale through the Stim oracle instead (d=9)."""
    schedule = repetition_memory(9, 9)
    bits = sample_stim_bits(schedule, SUB_THRESHOLD, 1000, seed=SEED)
    errors = int(decode_bits(schedule, bits).sum())
    smaller = repetition_memory(3, 9)
    smaller_bits = sample_stim_bits(smaller, SUB_THRESHOLD, 1000, seed=SEED)
    smaller_errors = int(decode_bits(smaller, smaller_bits).sum())
    assert errors <= smaller_errors  # larger distance suppresses harder


def test_counts_and_bits_paths_agree() -> None:
    schedule = repetition_memory(3, 3)
    bits = sample_stim_bits(schedule, SUB_THRESHOLD, 400, seed=7)
    direct = int(decode_bits(schedule, bits).sum())
    counts: dict[str, int] = {}
    for row in bits:
        bitstring = "".join(str(int(b)) for b in row[::-1])
        counts[bitstring] = counts.get(bitstring, 0) + 1
    via_counts, total = decode_counts(schedule, counts)
    assert (via_counts, total) == (direct, 400)


@pytest.mark.timeout(180)
async def test_unresolved_suppression_is_not_evaluable_not_fail() -> None:
    """Noise so weak that larger distances see zero errors: Lambda is
    unresolved and scalable_parameters must say so instead of guessing."""
    params = RepetitionParams(distances=[3, 5, 7], rounds=7, criteria="ab-lq-2026")
    record = await run_benchmark(
        RepetitionMemory(),
        AerSimulatorAdapter(noise=SUB_THRESHOLD),
        params,
        seed=SEED,
        shots=SHOTS,
    )
    assert_verifies(record)
    assert record.results.analysis is not None
    criteria = record.results.analysis["criteria"]
    verdicts = {v["criterion"]: v for v in criteria["verdicts"]}
    scalable = verdicts["scalable_parameters"]
    assert scalable["status"] == "not_evaluable"
    assert "unresolved" in scalable["reason"]
    steps = record.results.analysis["lambda_steps"]
    assert any(not step["resolved"] for step in steps)
