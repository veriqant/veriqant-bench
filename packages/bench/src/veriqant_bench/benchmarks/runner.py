"""The shared benchmark driver: generate → execute → analyze → sealed QPR.

Live runs are resumable: the live adapter persists every accepted submission
(JobHandle + full JobSpec, which carries the benchmark context) to a handle
file, and resume_run() reconstructs the benchmark deterministically from
that file, fetches the provider result, and assembles the same sealed QPR a
finished run would have produced. Resuming never submits anything — there is
no code path from here to a provider submit call.
"""

from __future__ import annotations

import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pydantic
from pydantic import BaseModel

from veriqant_bench import __version__
from veriqant_bench.adapters import CalibrationSnapshot, JobHandle, QPUAdapter
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
from veriqant_bench.qpr._generated import (
    Execution,
    ExecutionCost,
    ExecutionTiming,
    Transpilation,
)

from .base import Benchmark, ExecutedCircuitCounts, ExecutionOutcome, GeneratedCircuit


class QprVerificationError(RuntimeError):
    """A freshly produced QPR failed its own verification self-check."""


class ResumeError(RuntimeError):
    """A handle file cannot be resumed into a sealed QPR."""


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

    Captures the adapter's capabilities and calibration snapshot before
    submission; everything that crosses the adapter boundary is OpenQASM 3.
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
    return _assemble_record(
        benchmark,
        validated_params,
        adapter,
        generated,
        outcome,
        seed=seed,
        shots=shots,
        capabilities=capabilities,
        calibration=calibration,
    )


async def resume_run(
    handle_file: Path,
    adapter: QPUAdapter,
    *,
    timeout: float = 14_400.0,
) -> QuantumPerformanceRecord:
    """Resume an interrupted live run from its persisted handle file.

    Works because generation is deterministic: the benchmark context stored
    in the JobSpec metadata reconstructs the exact circuit batch, which is
    verified source-for-source against the submitted circuits before
    anything is assembled. The device calibration recorded in the QPR is the
    snapshot persisted at submit time — a resume-time re-fetch would
    describe a machine state the job may not have run under.
    """
    from .registry import get as get_benchmark

    document = json.loads(handle_file.read_text(encoding="utf-8"))
    spec = document.get("spec", {})
    context = spec.get("metadata", {})
    if "benchmark" not in context:
        raise ResumeError(
            f"{handle_file}: no benchmark context in the job spec — runs of benchmarks "
            "with custom execution protocols (e.g. throughput's timed batches) are "
            "not resumable; their semantics do not survive interruption"
        )
    benchmark = get_benchmark(str(context["benchmark"]))
    validated_params = benchmark.params_model.model_validate(context["params"])
    seed = int(context["seed"])
    shots = int(context["shots"])

    generated = benchmark.generate(validated_params, seed)
    if [circuit.qasm3 for circuit in generated] != spec.get("circuits"):
        raise ResumeError(
            f"{handle_file}: regenerated circuits do not match the submitted sources "
            "(SDK or benchmark version drift since submission); refusing to assemble "
            "a record that misdescribes the executed job"
        )

    handle = JobHandle.model_validate(document["handle"])
    if handle.adapter != adapter.name:
        raise ResumeError(
            f"{handle_file}: handle belongs to adapter '{handle.adapter}', got '{adapter.name}'"
        )

    result = await adapter.await_result(handle, timeout=timeout)
    metadata = dict(result.metadata)
    submit_metadata = document.get("submit_metadata", {})
    if "transpilation" not in metadata and "transpilation" in submit_metadata:
        metadata["transpilation"] = submit_metadata["transpilation"]
    outcome = ExecutionOutcome(
        results=[
            ExecutedCircuitCounts(circuit_index=index, counts=counts)
            for index, counts in enumerate(result.counts)
        ],
        submitted_at=handle.submitted_at,
        completed_at=result.completed_at,
        metadata=metadata,
    )

    persisted = document.get("calibration_at_submit")
    calibration = None if persisted is None else CalibrationSnapshot.model_validate(persisted)
    return _assemble_record(
        benchmark,
        validated_params,
        adapter,
        generated,
        outcome,
        seed=seed,
        shots=shots,
        capabilities=adapter.capabilities(),
        calibration=calibration,
    )


def _assemble_record(
    benchmark: Benchmark[Any],
    validated_params: BaseModel,
    adapter: QPUAdapter,
    generated: list[GeneratedCircuit],
    outcome: ExecutionOutcome,
    *,
    seed: int,
    shots: int,
    capabilities: Any,
    calibration: CalibrationSnapshot | None,
) -> QuantumPerformanceRecord:
    analysis = benchmark.analyze(
        generated,
        [executed.counts for executed in outcome.results],
        shots,
        validated_params,
        outcome.metadata,
    )

    job_ids = [str(job_id) for job_id in outcome.metadata.get("job_ids", [])]
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
            job_ids=job_ids or None,
            timing=_execution_timing(outcome.metadata),
            cost=_execution_cost(outcome.metadata),
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


def _execution_timing(metadata: dict[str, Any]) -> ExecutionTiming | None:
    """Promote adapter-reported queue/execution timing into the structural
    execution.timing block. Only the documented shape is promoted; timing
    dicts without a source (e.g. throughput's batch timings) stay in the
    benchmark's own analysis."""
    timing = metadata.get("timing")
    if not isinstance(timing, dict) or "source" not in timing:
        return None
    fields = {
        key: timing[key]
        for key in ("queue_seconds", "execution_seconds", "source")
        if key in timing
    }
    return ExecutionTiming.model_validate(fields)


def _execution_cost(metadata: dict[str, Any]) -> ExecutionCost | None:
    """Promote the live adapter's spend block (ledger cross-reference and
    gated estimate) into the structural execution.cost block."""
    cost = metadata.get("cost")
    if cost is None:
        return None
    try:
        return ExecutionCost.model_validate(cost)
    except pydantic.ValidationError as exc:
        raise QprVerificationError(f"adapter reported a malformed cost block: {exc}") from exc


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


__all__ = [
    "QprVerificationError",
    "ResumeError",
    "resume_run",
    "run_benchmark",
    "write_verified_qpr",
]
