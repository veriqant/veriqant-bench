"""Statistical helpers shared by benchmarks.

Bootstrap RNGs are seeded with a fixed constant so that analyze() stays a
deterministic function of its inputs — a requirement for reproducible sealed
QPRs (same seed + params => same content hash).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

BOOTSTRAP_SEED = 271828


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
    n_resamples: int = 200,
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
