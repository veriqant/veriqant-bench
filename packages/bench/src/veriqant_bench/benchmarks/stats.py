"""Statistical helpers shared by benchmarks.

Bootstrap RNGs are seeded with a fixed constant so that analyze() stays a
deterministic function of its inputs — a requirement for reproducible sealed
QPRs (same seed + params => same content hash).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from statistics import NormalDist

import numpy as np

from veriqant_bench.qpr._generated import MetricQuality

BOOTSTRAP_SEED = 271828
ZERO_WIDTH_CI_ISSUE = "statistics.zero_width_ci"
POINT_OUTSIDE_CI_ISSUE = "statistics.point_outside_bootstrap_ci"


def degrade_zero_width_ci(
    quality: MetricQuality | None, ci_lower: float, ci_upper: float
) -> MetricQuality | None:
    """A zero-width interval from a resampling estimator is degenerate
    evidence, not certainty: on an exactly-noiseless run every resample is
    identical and the bootstrap collapses, saying nothing about the
    estimator's real spread. Such a metric is never published reliable.
    (Deliberately non-statistical metrics — pass flags, 2^m quantum-volume
    values — do not go through this helper; their degenerate intervals are
    by construction.)"""
    if ci_lower != ci_upper:
        return quality
    issues = list(quality.issues or []) if quality is not None else []
    issues.append(ZERO_WIDTH_CI_ISSUE)
    return MetricQuality(reliable=False, issues=issues)


def flag_point_outside_ci(
    quality: MetricQuality | None, value: float, ci_lower: float, ci_upper: float
) -> MetricQuality | None:
    """The published interval is the bootstrap percentile interval as
    computed — never silently widened to swallow the point estimate. A
    point estimate outside its own resampling interval is an estimator-bias
    signal: the metric is published with the true interval, reliable=false,
    and this machine-readable issue (the verifier additionally warns)."""
    if ci_lower <= value <= ci_upper:
        return quality
    issues = list(quality.issues or []) if quality is not None else []
    issues.append(POINT_OUTSIDE_CI_ISSUE)
    return MetricQuality(reliable=False, issues=issues)


def wilson_interval(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if trials <= 0:
        raise ValueError("trials must be positive")
    z = NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)
    p = successes / trials
    denominator = 1.0 + z**2 / trials
    center = (p + z**2 / (2 * trials)) / denominator
    half_width = z * ((p * (1.0 - p) / trials + z**2 / (4 * trials**2)) ** 0.5) / denominator
    return max(0.0, center - half_width), min(1.0, center + half_width)


def bootstrap_rng() -> np.random.Generator:
    """The deterministic RNG used for bootstrap resampling."""
    return np.random.default_rng(BOOTSTRAP_SEED)


def percentile_ci(samples: Sequence[float], confidence: float) -> tuple[float, float]:
    """Percentile confidence interval of a bootstrap sample distribution."""
    tail = (1.0 - confidence) / 2.0
    lower, upper = np.quantile(np.asarray(samples, dtype=float), [tail, 1.0 - tail])
    return float(lower), float(upper)


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    rng: np.random.Generator | None = None,
    transform: Callable[[float], float] | None = None,
) -> tuple[float, float, float]:
    """Bootstrap CI of the mean of *values* (optionally transformed).

    Returns (ci_lower, ci_upper, std_error). With a single value the interval
    degenerates to that value.
    """
    rng = rng or bootstrap_rng()
    data = np.asarray(values, dtype=float)
    apply = transform or (lambda x: x)
    if data.size == 1:
        point = apply(float(data[0]))
        return point, point, 0.0
    means = [
        apply(float(np.mean(rng.choice(data, size=data.size, replace=True))))
        for _ in range(n_resamples)
    ]
    lower, upper = percentile_ci(means, confidence)
    return lower, upper, float(np.std(means))
