"""The shared benchmark driver: generate → execute → analyze → sealed QPR."""

from __future__ import annotations

import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from veriqant_bench import __version__
from veriqant_bench.adapters import QPUAdapter
from veriqant_bench.qpr import (
    QPR_VERSION,
    Circuit,
    Integrity,
    Provenance,
    Provider,
    QuantumPerformanceRecord,
    RawResult,
    Results,
    dump_qpr,
    seal,
    sha256_hex,
    verify_qpr_file,
)
from veriqant_bench.qpr import Benchmark as QprBenchmark
from veriqant_bench.qpr._generated import Execution, Transpilation

from .base import Benchmark, GeneratedCircuit


class QprVerificationError(RuntimeError):
    """A freshly produced QPR failed its own verification self-check."""


async def run_benchmark(
    benchmark: Benchmark[Any],
    adapter: QPUAdapter,
    params: BaseModel | dict[str, Any],
    *,
    seed: int,
    shots: int,
    timeout: float = 600.0,
) -> QuantumPerformanceRecord:
    """Run *benchmark* on *adapter* and return a sealed QPR.

    Captures the adapter's capabilities and calibration snapshot at execution
    time; everything that crosses the adapter boundary is OpenQASM 3.
    """
    validated_params = benchmark.params_model.model_validate(
        params if isinstance(params, dict) else params.model_dump()
    )
    generated = benchmark.generate(validated_params, seed)

    capabilities = adapter.capabilities()
    calibration = adapter.calibration_snapshot()

    outcome = await benchmark.execute(
        adapter, generated, validated_params, seed=seed, shots=shots, timeout=timeout
    )

    analysis = benchmark.analyze(
        generated,
        [executed.counts for executed in outcome.results],
        shots,
        validated_params,
        outcome.metadata,
    )

    record = QuantumPerformanceRecord(
        qpr_version=QPR_VERSION,
        record_id=uuid4(),
        created_at=datetime.now(tz=UTC),
        benchmark=QprBenchmark(
            id=benchmark.qpr_benchmark_id(validated_params),
            display_name=benchmark.display_name(validated_params),
            suite_version=benchmark.version,
            parameters=validated_params.model_dump(mode="json", exclude_none=True),
        ),
        provider=Provider(name=capabilities.provider_name, adapter=adapter.name),
        device=capabilities.to_qpr_device(calibration),
        execution=Execution(
            seed=seed,
            shots=shots,
            live=not capabilities.is_simulator,
            transpilation=_transpilation(adapter, outcome.metadata),
            submitted_at=outcome.submitted_at,
            completed_at=outcome.completed_at,
        ),
        circuits=_qpr_circuits(generated),
        results=Results(
            raw=[
                RawResult(
                    circuit_index=executed.circuit_index,
                    shots=sum(executed.counts.values()),
                    counts=executed.counts,
                )
                for executed in outcome.results
            ],
            metrics=analysis.metrics,
            analysis=analysis.analysis or None,
        ),
        provenance=Provenance(
            veriqant_bench_version=__version__,
            python_version=platform.python_version(),
            platform=platform.platform(),
            sdk_versions={
                **outcome.metadata.get("sdk_versions", {}),
                "veriqant-bench": __version__,
            },
        ),
        integrity=Integrity(content_sha256="0" * 64),
    )
    return seal(record)


def write_verified_qpr(record: QuantumPerformanceRecord, out_dir: Path) -> Path:
    """Write a QPR and immediately re-verify the file on disk (self-check).

    Raises QprVerificationError if the written record does not pass the
    independent verifier — a freshly produced record must never ship broken.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = record.created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = (
        f"{record.benchmark.id}_{record.provider.adapter}_{timestamp}"
        f"_{record.record_id.hex[:8]}.qpr.json"
    )
    path = out_dir / filename
    dump_qpr(record, path)
    report = verify_qpr_file(path)
    if not report.ok:
        errors = "; ".join(str(issue) for issue in report.issues if issue.severity == "error")
        raise QprVerificationError(f"self-check failed for {path}: {errors}")
    return path


def _transpilation(adapter: QPUAdapter, metadata: dict[str, Any]) -> Transpilation:
    reported = metadata.get("transpilation")
    if isinstance(reported, dict):
        cleaned = {key: value for key, value in reported.items() if value is not None}
        return Transpilation.model_validate(cleaned)
    return Transpilation(
        sdk=adapter.name,
        sdk_version=adapter.adapter_version,
        settings={"note": "adapter reported no transpilation metadata"},
    )


def _qpr_circuits(generated: list[GeneratedCircuit]) -> list[Circuit]:
    return [
        Circuit(
            index=index,
            name=circuit.name,
            qasm3=circuit.qasm3,
            qasm3_sha256=sha256_hex(circuit.qasm3),
            metadata=circuit.metadata or None,
        )
        for index, circuit in enumerate(generated)
    ]


__all__ = ["QprVerificationError", "run_benchmark", "write_verified_qpr"]
