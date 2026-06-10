"""Runner tests: QPR assembly with a fake adapter, the post-write self-check,
and the benchmark registry."""

from __future__ import annotations

import json
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any

import pytest
from conftest import StaticAdapter
from pydantic import BaseModel, ConfigDict

from veriqant_bench.benchmarks import (
    AnalysisResult,
    Benchmark,
    BenchmarkUnavailableError,
    GeneratedCircuit,
    QprVerificationError,
    get,
    list_benchmarks,
    run_benchmark,
    write_verified_qpr,
)
from veriqant_bench.benchmarks import registry as registry_module
from veriqant_bench.benchmarks.rb import RandomizedBenchmarking
from veriqant_bench.qpr import verify_qpr_document, verify_qpr_file
from veriqant_bench.qpr._generated import Metric, MetricStatistics


class TinyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TinyBenchmark(Benchmark[TinyParams]):
    """A minimal benchmark for driver tests."""

    name = "tiny"
    version = "0.0.1"
    params_model = TinyParams

    def generate(self, params: TinyParams, seed: int) -> list[GeneratedCircuit]:
        return [
            GeneratedCircuit(
                name="tiny_0",
                qasm3='OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[1] q;\nbit[1] c;\n'
                "c[0] = measure q[0];\n",
                metadata={"k": 1},
            )
        ]

    def analyze(
        self,
        circuits: list[GeneratedCircuit],
        counts: list[dict[str, int]],
        shots: int,
        params: TinyParams,
        execution_metadata: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        value = counts[0].get("0", 0) / shots
        return AnalysisResult(
            metrics=[
                Metric(
                    name="ground_state_fraction",
                    value=value,
                    statistics=MetricStatistics(
                        sample_size=shots,
                        confidence_level=0.95,
                        ci_lower=value,
                        ci_upper=value,
                        estimator="exact",
                    ),
                )
            ],
            analysis={"note": "tiny"},
        )


async def test_runner_assembles_a_valid_sealed_qpr() -> None:
    record = await run_benchmark(TinyBenchmark(), StaticAdapter(), TinyParams(), seed=7, shots=50)
    assert record.benchmark.id == "tiny"
    assert record.benchmark.suite_version == "0.0.1"
    assert record.provider.name == "local"
    assert record.provider.adapter == "static_test"
    assert record.device.simulator is True
    assert record.execution.seed == 7
    assert record.execution.live is False
    # Adapter reported no transpilation metadata: recorded honestly.
    assert record.execution.transpilation.sdk == "static_test"
    assert record.provenance.sdk_versions["static"] == "1.2.3"
    assert record.results.raw[0].counts == {"0": 50}
    report = verify_qpr_document(json.loads(record.model_dump_json(exclude_none=True)))
    assert report.ok


async def test_write_verified_qpr_roundtrip(tmp_path: Path) -> None:
    record = await run_benchmark(TinyBenchmark(), StaticAdapter(), TinyParams(), seed=1, shots=10)
    path = write_verified_qpr(record, tmp_path / "results")
    assert path.name.startswith("tiny_static_test_")
    assert path.name.endswith(".qpr.json")
    assert verify_qpr_file(path).ok


async def test_write_verified_qpr_rejects_corrupted_records(tmp_path: Path) -> None:
    record = await run_benchmark(TinyBenchmark(), StaticAdapter(), TinyParams(), seed=1, shots=10)
    record.circuits[0].qasm3_sha256 = "0" * 64
    with pytest.raises(QprVerificationError, match="self-check failed"):
        write_verified_qpr(record, tmp_path)


def test_builtin_benchmarks_are_registered() -> None:
    by_name = {info.name: info for info in list_benchmarks()}
    assert by_name["rb"].available
    assert by_name["mirror"].available
    assert isinstance(get("rb"), RandomizedBenchmarking)


def test_unknown_benchmark_lists_known_names() -> None:
    with pytest.raises(BenchmarkUnavailableError, match="mirror"):
        get("does_not_exist")


def test_missing_dependency_reports_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = EntryPoint(
        name="rb", value="nonexistent_package:RB", group=registry_module.ENTRY_POINT_GROUP
    )
    monkeypatch.setattr(registry_module, "_discover", lambda: [broken])
    info = registry_module.list_benchmarks()[0]
    assert not info.available
    assert info.install_hint == "pip install 'veriqant-bench[local]'"
    with pytest.raises(BenchmarkUnavailableError, match="not installed"):
        get("rb")


def test_non_benchmark_factory_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    impostor = EntryPoint(
        name="impostor", value="builtins:dict", group=registry_module.ENTRY_POINT_GROUP
    )
    monkeypatch.setattr(registry_module, "_discover", lambda: [impostor])
    with pytest.raises(BenchmarkUnavailableError, match="does not implement"):
        get("impostor")
