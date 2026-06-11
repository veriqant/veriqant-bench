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


def test_degrade_zero_width_ci() -> None:
    """Zero-width intervals are degenerate evidence: never reliable,
    machine-readably flagged, with existing issues preserved."""
    from veriqant_bench.benchmarks.stats import ZERO_WIDTH_CI_ISSUE, degrade_zero_width_ci
    from veriqant_bench.qpr._generated import MetricQuality

    # Non-degenerate intervals pass through untouched (including None).
    assert degrade_zero_width_ci(None, 0.1, 0.2) is None
    keep = MetricQuality(reliable=True, issues=None)
    assert degrade_zero_width_ci(keep, 0.1, 0.2) is keep

    # Degenerate: created when absent...
    created = degrade_zero_width_ci(None, 0.5, 0.5)
    assert created is not None and not created.reliable
    assert created.issues == [ZERO_WIDTH_CI_ISSUE]
    # ...and appended when present, never dropping prior issues.
    prior = MetricQuality(reliable=False, issues=["fit.did_not_converge"])
    merged = degrade_zero_width_ci(prior, 0.5, 0.5)
    assert merged is not None and not merged.reliable
    assert merged.issues == ["fit.did_not_converge", ZERO_WIDTH_CI_ISSUE]


def test_flag_point_outside_ci() -> None:
    """The published interval is the true percentile interval; a point
    estimate outside it is flagged, never swallowed by silent widening."""
    from veriqant_bench.benchmarks.stats import POINT_OUTSIDE_CI_ISSUE, flag_point_outside_ci
    from veriqant_bench.qpr._generated import MetricQuality

    keep = MetricQuality(reliable=True, issues=None)
    assert flag_point_outside_ci(keep, 0.15, 0.1, 0.2) is keep
    assert flag_point_outside_ci(None, 0.1, 0.1, 0.2) is None  # edge counts as inside

    flagged = flag_point_outside_ci(keep, 0.25, 0.1, 0.2)
    assert flagged is not None and not flagged.reliable
    assert flagged.issues == [POINT_OUTSIDE_CI_ISSUE]


def test_bootstrap_resamples_default_is_1000() -> None:
    """Percentile tails of a 95% CI need more than 200 resamples to be
    stable; 1000 is the revised package-wide default."""
    import inspect

    from veriqant_bench.benchmarks.mirror import MirrorParams
    from veriqant_bench.benchmarks.qv import QVParams
    from veriqant_bench.benchmarks.rb import RBParams
    from veriqant_bench.benchmarks.stats import bootstrap_mean_ci
    from veriqant_bench.benchmarks.throughput import ThroughputParams

    assert inspect.signature(bootstrap_mean_ci).parameters["n_resamples"].default == 1000
    assert RBParams.model_fields["bootstrap_resamples"].default == 1000
    assert MirrorParams.model_fields["bootstrap_resamples"].default == 1000
    assert QVParams.model_fields["bootstrap_resamples"].default == 1000
    assert ThroughputParams.model_fields["bootstrap_resamples"].default == 1000
