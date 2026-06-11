"""End-to-end live runs and resume: a sealed QPR with structural cost and
timing from the fake IBM path, resume from the handle file (including from
a fresh process-equivalent), and the refusal cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import make_ibm_adapter

from veriqant_bench.benchmarks import ResumeError, resume_run, run_benchmark, write_verified_qpr
from veriqant_bench.benchmarks.rb import RandomizedBenchmarking, RBParams
from veriqant_bench.qpr import verify_qpr_file

PARAMS = RBParams(qubits=[0], lengths=[1, 2, 4], samples_per_length=2)
SEED = 424242
SHOTS = 64


async def run_live_rb(tmp_path: Path) -> tuple[Any, Any, Any]:
    adapter, service = make_ibm_adapter(tmp_path)
    record = await run_benchmark(
        RandomizedBenchmarking(), adapter, PARAMS, seed=SEED, shots=SHOTS, timeout=60.0
    )
    return record, adapter, service


async def test_live_run_seals_a_qpr_with_cost_and_timing(tmp_path: Path) -> None:
    record, adapter, _ = await run_live_rb(tmp_path)
    assert record.execution.live is True
    assert record.provider.name == "ibm"
    assert record.device.simulator is False
    assert record.device.calibration_snapshot is not None  # verbatim properties
    cost = record.execution.cost
    assert cost is not None
    assert cost.currency == "USD"
    assert cost.estimated_qpu_seconds > 0
    # The QPR cross-references the exact committed ledger entry.
    assert cost.ledger_entry_id in adapter._ledger.path.read_text()
    timing = record.execution.timing
    assert timing is not None and timing.source == "provider_job_metrics"
    assert record.execution.job_ids  # provider ids, no ledger smuggling
    assert all("ledger" not in job_id for job_id in record.execution.job_ids)
    # And the file self-verifies like any other QPR.
    path = write_verified_qpr(record, tmp_path / "results")
    assert verify_qpr_file(path).ok


async def test_resume_produces_the_same_record_shape(tmp_path: Path) -> None:
    submitter, service = make_ibm_adapter(tmp_path)
    benchmark = RandomizedBenchmarking()
    generated = benchmark.generate(PARAMS, SEED)
    outcome_spec = {
        "benchmark": "rb",
        "params": PARAMS.model_dump(mode="json"),
        "seed": SEED,
        "shots": SHOTS,
    }
    from veriqant_bench.adapters.types import JobSpec

    handle = await submitter.submit(
        JobSpec(
            circuits=[circuit.qasm3 for circuit in generated],
            shots=SHOTS,
            seed=SEED,
            metadata=outcome_spec,
        )
    )
    service.jobs_store[handle.job_id] = submitter._jobs[handle.job_id]
    handle_file = submitter.handle_file(handle)

    # A fresh adapter (new process equivalent): no live flag needed —
    # resuming has no code path to a provider submit.
    fresh, _ = make_ibm_adapter(
        tmp_path,
        allow_live=False,
        service=service,
        backend=submitter._backend,
        ledger=submitter._ledger,
    )
    record = await resume_run(handle_file, fresh, timeout=60.0)
    assert record.execution.live is True
    assert record.benchmark.id == "rb_1q"
    assert record.execution.seed == SEED
    cost = record.execution.cost
    assert cost is not None
    assert cost.ledger_entry_id == json.loads(handle_file.read_text())["ledger_entry_id"]
    # Calibration comes from the submit-time snapshot in the handle file.
    persisted = json.loads(handle_file.read_text())["calibration_at_submit"]
    assert record.device.calibration_snapshot == persisted["data"]
    path = write_verified_qpr(record, tmp_path / "results")
    assert verify_qpr_file(path).ok


async def test_resume_refuses_on_circuit_drift(tmp_path: Path) -> None:
    submitter, _service = make_ibm_adapter(tmp_path)
    benchmark = RandomizedBenchmarking()
    record = await run_benchmark(benchmark, submitter, PARAMS, seed=SEED, shots=SHOTS, timeout=60.0)
    assert record is not None
    handle_files = list((tmp_path / "jobs").glob("*.json"))
    assert len(handle_files) == 1
    document = json.loads(handle_files[0].read_text(encoding="utf-8"))
    document["spec"]["circuits"][0] += "// tampered\n"
    handle_files[0].write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ResumeError, match="do not match the submitted sources"):
        await resume_run(handle_files[0], submitter, timeout=10.0)


async def test_resume_refuses_missing_benchmark_context(tmp_path: Path) -> None:
    submitter, _service = make_ibm_adapter(tmp_path)
    from veriqant_bench.adapters.types import JobSpec

    qasm = (
        "OPENQASM 3.0;\n"
        'include "stdgates.inc";\n'
        "qubit[1] q;\n"
        "bit[1] c;\n"
        "h q[0];\n"
        "c[0] = measure q[0];\n"
    )
    handle = await submitter.submit(JobSpec(circuits=[qasm], shots=8, seed=1))
    with pytest.raises(ResumeError, match="not resumable"):
        await resume_run(submitter.handle_file(handle), submitter, timeout=10.0)


async def test_resume_refuses_an_unparseable_handle_file(tmp_path: Path) -> None:
    # Library path symmetry with the CLI: a file that is not JSON (or not a
    # JSON object) is a typed ResumeError, not a raw json traceback.
    submitter, _ = make_ibm_adapter(tmp_path)
    garbage = tmp_path / "garbage.json"
    garbage.write_text("{truncated", encoding="utf-8")
    with pytest.raises(ResumeError, match="not a veriqant-bench handle file"):
        await resume_run(garbage, submitter, timeout=10.0)
    garbage.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ResumeError, match="not a veriqant-bench handle file"):
        await resume_run(garbage, submitter, timeout=10.0)


async def test_resume_refuses_adapter_mismatch(tmp_path: Path) -> None:
    submitter, _ = make_ibm_adapter(tmp_path)
    benchmark = RandomizedBenchmarking()
    await run_benchmark(benchmark, submitter, PARAMS, seed=SEED, shots=SHOTS, timeout=60.0)
    handle_file = next((tmp_path / "jobs").glob("*.json"))

    class OtherAdapter:
        name = "someone_else"

    with pytest.raises(ResumeError, match="belongs to adapter 'ibm_runtime'"):
        await resume_run(handle_file, OtherAdapter(), timeout=10.0)  # type: ignore[arg-type]
