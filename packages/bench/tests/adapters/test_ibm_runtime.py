"""IBMRuntimeAdapter against qiskit-ibm-runtime fakes: the full
submit→transpile→execute→result path runs real qiskit code in SamplerV2
local mode; only the service is faked."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import make_ibm_adapter

from veriqant_bench.adapters.errors import LiveRefusedError, SubmissionError
from veriqant_bench.adapters.ibm import (
    PER_CIRCUIT_OVERHEAD_S,
    PER_SHOT_S,
    QUOTA_HEURISTIC,
    IBMRuntimeAdapter,
)
from veriqant_bench.adapters.types import JobSpec, JobStatus

ASYMMETRIC_2Q = (
    "OPENQASM 3.0;\n"
    'include "stdgates.inc";\n'
    "qubit[2] q;\n"
    "bit[2] c;\n"
    "x q[0];\n"
    "c[0] = measure q[0];\n"
    "c[1] = measure q[1];\n"
)


# ---- discovery / mapping -------------------------------------------------------


def test_capabilities_map_the_fake_backend(tmp_path: Path) -> None:
    adapter, _ = make_ibm_adapter(tmp_path)
    capabilities = adapter.capabilities()
    assert capabilities.provider_name == "ibm"
    assert capabilities.device_name == "fake_manila"
    assert capabilities.num_qubits == 5
    assert capabilities.is_simulator is False
    assert capabilities.coupling_map and (0, 1) in capabilities.coupling_map
    assert "cx" in capabilities.native_gates
    # Q4: FakeManilaV2 (like many BackendV2) exposes no max_shots.
    assert capabilities.max_shots is None
    assert capabilities.raw["dynamic_circuits"] is True
    assert capabilities.supports_midcircuit_measurement is True


def test_calibration_snapshot_is_verbatim_backend_properties(tmp_path: Path) -> None:
    adapter, _ = make_ibm_adapter(tmp_path)
    snapshot = adapter.calibration_snapshot()
    assert snapshot is not None
    assert snapshot.source == "provider_api"
    properties = snapshot.data["backend_properties"]
    # Raw, not summarized: per-qubit T1/T2 entries and per-gate errors.
    assert "qubits" in properties
    assert "gates" in properties
    assert "last_update_date" in properties


def test_estimate_is_offline_conservative_and_named(tmp_path: Path) -> None:
    adapter = IBMRuntimeAdapter()  # no service, no backend: construction is offline
    spec = JobSpec(circuits=[ASYMMETRIC_2Q] * 6, shots=256, seed=1)
    estimate = adapter.estimate_cost(spec)
    assert estimate.amount == 0
    assert estimate.confidence == "estimate"
    assert estimate.qpu_seconds == 6 * PER_CIRCUIT_OVERHEAD_S + 6 * 256 * PER_SHOT_S
    assert estimate.heuristic == QUOTA_HEURISTIC


# ---- open-plan gating (fail closed) ----------------------------------------------


async def test_premium_plan_is_refused_before_the_gate(tmp_path: Path) -> None:
    adapter, _ = make_ibm_adapter(tmp_path, plan="premium")
    with pytest.raises(LiveRefusedError, match="premium billing not supported"):
        await adapter.submit(JobSpec(circuits=[ASYMMETRIC_2Q], shots=16, seed=1))
    # Plan classification is validation: it runs before the cost gate, so
    # the ledger is never touched (no estimate, no released pair).
    assert adapter._ledger.monthly_totals().entries == 0
    assert not adapter._ledger.path.exists()


async def test_undeterminable_plan_is_refused(tmp_path: Path) -> None:
    adapter, _ = make_ibm_adapter(tmp_path, plan=None)
    with pytest.raises(LiveRefusedError, match="cannot determine the IBM plan"):
        await adapter.submit(JobSpec(circuits=[ASYMMETRIC_2Q], shots=16, seed=1))


# ---- the full local-mode path ------------------------------------------------------


async def test_submit_to_result_full_path(tmp_path: Path) -> None:
    adapter, _ = make_ibm_adapter(tmp_path)
    spec = JobSpec(circuits=[ASYMMETRIC_2Q], shots=64, seed=5)
    handle = await adapter.submit(spec)
    result = await adapter.await_result(handle, timeout=30.0)
    assert await adapter.poll(handle) is JobStatus.COMPLETED  # terminal is stable
    counts = result.counts[0]
    assert sum(counts.values()) == 64
    # x on q0 -> '01' in QPR convention (bit 0 rightmost).
    assert max(counts, key=lambda key: counts[key]) == "01"
    assert result.metadata["timing"]["source"] == "provider_job_metrics"
    cost = result.metadata["cost"]
    assert cost["estimated_qpu_seconds"] == pytest.approx(1.064)
    assert cost["currency"] == "USD"
    # Transpilation record persisted at submit, ISA + seeded.
    document = json.loads(adapter.handle_file(handle).read_text(encoding="utf-8"))
    transpilation = document["submit_metadata"]["transpilation"]
    assert transpilation["sdk"] == "qiskit"
    assert transpilation["settings"]["seed_transpiler"] == 5
    assert document["calibration_at_submit"]["source"] == "provider_api"


async def test_invalid_qasm_is_a_typed_rejection(tmp_path: Path) -> None:
    adapter, _ = make_ibm_adapter(tmp_path)
    with pytest.raises(SubmissionError, match="invalid OpenQASM 3"):
        await adapter.submit(
            JobSpec(circuits=["OPENQASM 3.0;\nnot a circuit ;;;\n"], shots=16, seed=0)
        )
    # Parsing is validation: pre-gate, so the ledger is never touched.
    assert adapter._ledger.monthly_totals().entries == 0
    assert not adapter._ledger.path.exists()


# ---- result-shape and status drift (Q2) ----------------------------------------------


def _pub(register_names: list[str]) -> Any:
    class BitArrayLike:
        def get_counts(self) -> dict[str, int]:
            return {"0 1": 3, "11": 5}

    data = SimpleNamespace(**{name: BitArrayLike() for name in register_names})
    return SimpleNamespace(data=data)


def test_pub_counts_reads_any_single_register_name(tmp_path: Path) -> None:
    adapter, _ = make_ibm_adapter(tmp_path)
    for register in ("c", "meas", "creg0"):
        counts = adapter._pub_counts(_pub([register]))
        assert counts == {"01": 3, "11": 5}  # spaces stripped


def test_pub_counts_refuses_ambiguous_register_layout(tmp_path: Path) -> None:
    adapter, _ = make_ibm_adapter(tmp_path)
    with pytest.raises(SubmissionError, match="unrecognized sampler result shape"):
        adapter._pub_counts(_pub(["c0", "c1"]))
    with pytest.raises(SubmissionError, match="unrecognized sampler result shape"):
        adapter._pub_counts(_pub([]))


async def test_unrecognized_status_is_a_typed_error(tmp_path: Path) -> None:
    adapter, _ = make_ibm_adapter(tmp_path)
    with pytest.raises(SubmissionError, match="unrecognized IBM job status"):
        await adapter._provider_status(SimpleNamespace(status=lambda: "PONDERING"))


def test_status_map_covers_enum_and_string_forms(tmp_path: Path) -> None:
    import asyncio

    adapter, _ = make_ibm_adapter(tmp_path)
    enum_like = SimpleNamespace(name="DONE")
    assert asyncio.run(adapter._provider_status(SimpleNamespace(status=lambda: enum_like))) is (
        JobStatus.COMPLETED
    )
    assert asyncio.run(adapter._provider_status(SimpleNamespace(status=lambda: "queued"))) is (
        JobStatus.QUEUED
    )


# ---- job metrics extraction -------------------------------------------------------------


def test_job_metrics_extracts_timing_split_and_usage(tmp_path: Path) -> None:
    adapter, _ = make_ibm_adapter(tmp_path)
    job = SimpleNamespace(
        metrics=lambda: {
            "timestamps": {
                "created": "2026-06-15T10:00:00+00:00",
                "running": "2026-06-15T11:30:00+00:00",
                "finished": "2026-06-15T11:30:12+00:00",
            },
            "usage": {"seconds": 4.2},
        }
    )
    payload = adapter._job_metrics(job)
    assert payload["timing"]["queue_seconds"] == pytest.approx(5400.0)
    assert payload["timing"]["execution_seconds"] == pytest.approx(12.0)
    assert payload["timing"]["source"] == "provider_job_metrics"
    assert payload["actual_qpu_seconds"] == 4.2
    assert payload["timing"]["_started_at"] == datetime(2026, 6, 15, 11, 30, tzinfo=UTC)


def test_job_metrics_absent_is_honest(tmp_path: Path) -> None:
    adapter, _ = make_ibm_adapter(tmp_path)
    payload = adapter._job_metrics(SimpleNamespace())
    assert payload["timing"]["source"] == "unavailable_no_job_metrics"


# ---- resume attachment -------------------------------------------------------------------


async def test_result_via_fresh_adapter_reattaches_through_the_service(tmp_path: Path) -> None:
    submitter, service = make_ibm_adapter(tmp_path)
    spec = JobSpec(circuits=[ASYMMETRIC_2Q], shots=32, seed=9)
    handle = await submitter.submit(spec)
    service.jobs_store[handle.job_id] = submitter._jobs[handle.job_id]

    fresh = IBMRuntimeAdapter(
        allow_live=False,  # resume needs no live flag: it cannot submit
        service=service,
        backend=submitter._backend,
        limits=submitter._limits,
        ledger=submitter._ledger,
        jobs_dir=tmp_path / "jobs",
    )
    result = await fresh.await_result(handle, timeout=30.0)
    assert sum(result.counts[0].values()) == 32
    # The ledger cross-reference came from the handle file, not from state.
    assert result.metadata["cost"]["ledger_entry_id"]


def test_lazy_construction_is_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # No service, no backend, no env token: constructing must not raise and
    # must not touch the network; the credential layer reports the problem.
    monkeypatch.delenv("QISKIT_IBM_TOKEN", raising=False)
    adapter = IBMRuntimeAdapter()
    problem = adapter._missing_credentials()
    assert problem is None or "QISKIT_IBM_TOKEN" in problem  # saved account may exist locally


def test_device_name_placeholder_avoids_network(tmp_path: Path) -> None:
    adapter = IBMRuntimeAdapter()
    assert adapter._device_name() == "ibm:least-busy"
    named = IBMRuntimeAdapter("ibm_torino")
    assert named._device_name() == "ibm_torino"
