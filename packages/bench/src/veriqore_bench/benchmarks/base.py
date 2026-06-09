"""The Benchmark abstraction.

A benchmark is two pure functions around an adapter execution:
generate(params, seed) -> circuits (deterministic from seed) and
analyze(circuits, counts, shots, params) -> metrics. The shared runner
(runner.py) wires them to a QPUAdapter and assembles a sealed QPR.

Benchmark implementations are versioned independently of the package: any
change to the circuit family, sampling procedure, or estimator bumps
`version`, which lands in the QPR's benchmark.suite_version.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from veriqore_bench.qpr._generated import Metric


class GeneratedCircuit(BaseModel):
    """One circuit produced by generate(): OpenQASM 3 plus benchmark-private
    metadata (e.g. sequence length) that analyze() and the QPR record keep."""

    model_config = ConfigDict(extra="forbid")

    name: str
    qasm3: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    """Output of analyze(): QPR metrics plus free-form analysis artifacts."""

    model_config = ConfigDict(extra="forbid")

    metrics: list[Metric] = Field(min_length=1)
    analysis: dict[str, Any] = Field(default_factory=dict)


class Benchmark[ParamsT: BaseModel](ABC):
    """Base class for benchmark implementations."""

    name: ClassVar[str]
    """Registry name, e.g. 'rb', 'mirror'."""
    version: ClassVar[str]
    """Semver of this benchmark implementation (methodology version)."""
    params_model: type[ParamsT]
    """Pydantic model class for this benchmark's parameters."""

    def qpr_benchmark_id(self, params: ParamsT) -> str:
        """Identifier recorded as QPR benchmark.id (may depend on params,
        e.g. rb_1q vs rb_2q)."""
        return self.name

    def display_name(self, params: ParamsT) -> str:
        """Human-readable name for QPR benchmark.display_name."""
        return self.name

    @abstractmethod
    def generate(self, params: ParamsT, seed: int) -> list[GeneratedCircuit]:
        """Deterministically generate the circuit batch from (params, seed)."""

    @abstractmethod
    def analyze(
        self,
        circuits: list[GeneratedCircuit],
        counts: list[dict[str, int]],
        shots: int,
        params: ParamsT,
    ) -> AnalysisResult:
        """Pure function from measured counts to metrics. Must not execute
        anything; unit-testable on synthetic counts."""
