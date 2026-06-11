"""Benchmark suites: the framework plus built-in implementations.

Concrete benchmarks (rb, mirror) are not imported here — their circuit
generation needs the [local] extra (qiskit). Reach them through the registry
(`get`, `list_benchmarks`) or import their modules directly.
"""

from .base import (
    AnalysisResult,
    Benchmark,
    ExecutedCircuitCounts,
    ExecutionOutcome,
    GeneratedCircuit,
)
from .registry import (
    ENTRY_POINT_GROUP,
    BenchmarkInfo,
    BenchmarkUnavailableError,
    get,
    list_benchmarks,
)
from .runner import (
    QprVerificationError,
    ResumeError,
    resume_run,
    run_benchmark,
    write_verified_qpr,
)
from .stats import (
    BOOTSTRAP_SEED,
    ZERO_WIDTH_CI_ISSUE,
    bootstrap_mean_ci,
    bootstrap_rng,
    degrade_zero_width_ci,
    percentile_ci,
)

__all__ = [
    "BOOTSTRAP_SEED",
    "ENTRY_POINT_GROUP",
    "ZERO_WIDTH_CI_ISSUE",
    "AnalysisResult",
    "Benchmark",
    "BenchmarkInfo",
    "BenchmarkUnavailableError",
    "ExecutedCircuitCounts",
    "ExecutionOutcome",
    "GeneratedCircuit",
    "QprVerificationError",
    "ResumeError",
    "bootstrap_mean_ci",
    "bootstrap_rng",
    "degrade_zero_width_ci",
    "get",
    "list_benchmarks",
    "percentile_ci",
    "resume_run",
    "run_benchmark",
    "write_verified_qpr",
]
