from __future__ import annotations

import pytest

from veriqant_bench.benchmarks import bootstrap_mean_ci, bootstrap_rng, percentile_ci


def test_bootstrap_is_deterministic() -> None:
    values = [0.9, 0.85, 0.95, 0.8, 0.92]
    assert bootstrap_mean_ci(values) == bootstrap_mean_ci(values)


def test_bootstrap_ci_brackets_the_mean() -> None:
    values = [0.4, 0.5, 0.6, 0.45, 0.55, 0.5]
    lower, upper, std_error = bootstrap_mean_ci(values)
    assert lower <= 0.5 <= upper
    assert std_error > 0


def test_constant_values_give_degenerate_interval() -> None:
    lower, upper, std_error = bootstrap_mean_ci([0.7, 0.7, 0.7])
    assert lower == pytest.approx(0.7)
    assert upper == pytest.approx(lower)
    assert std_error == pytest.approx(0.0, abs=1e-12)


def test_single_value_degenerates() -> None:
    assert bootstrap_mean_ci([0.3]) == (0.3, 0.3, 0.0)


def test_transform_is_applied() -> None:
    lower, upper, _ = bootstrap_mean_ci([0.5, 0.5], transform=lambda x: 2 * x)
    assert lower == upper == 1.0


def test_percentile_ci_orders_bounds() -> None:
    lower, upper = percentile_ci([1.0, 2.0, 3.0, 4.0, 5.0], 0.9)
    assert lower < upper


def test_bootstrap_rng_is_fixed_seeded() -> None:
    assert bootstrap_rng().integers(0, 1_000_000) == bootstrap_rng().integers(0, 1_000_000)
