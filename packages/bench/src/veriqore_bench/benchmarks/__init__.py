"""Benchmark suites: the framework plus built-in implementations.

Concrete benchmarks (rb, mirror) are not imported here — their circuit
generation needs the [local] extra (qiskit). Reach them through the registry
(`get`, `list_benchmarks`) or import their modules directly.
"""

from .base import AnalysisResult, Benchmark, GeneratedCircuit
from .registry import (
    ENTRY_POINT_GROUP,
    BenchmarkInfo,
    BenchmarkUnavailableError,
    get,
    list_benchmarks,
)
from .runner import QprVerificationError, run_benchmark, write_verified_qpr
from .stats import BOOTSTRAP_SEED, bootstrap_mean_ci, bootstrap_rng, percentile_ci

__all__ = [
    "BOOTSTRAP_SEED",
    "ENTRY_POINT_GROUP",
    "AnalysisResult",
    "Benchmark",
    "BenchmarkInfo",
    "BenchmarkUnavailableError",
    "GeneratedCircuit",
    "QprVerificationError",
    "bootstrap_mean_ci",
    "bootstrap_rng",
    "get",
    "list_benchmarks",
    "percentile_ci",
    "run_benchmark",
    "write_verified_qpr",
]
