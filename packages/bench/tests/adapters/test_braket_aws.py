"""BraketAdapter against stubbed AWS transports: conversion reuse, the
static price table with its staleness thresholds, availability warnings,
batch status aggregation, and bit-order reversal."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import make_braket_adapter

from veriqant_bench.adapters import braket_aws as braket_aws_module
from veriqant_bench.adapters.braket_aws import (
    PRICE_TABLE_SOURCE,
    BraketAdapter,
)
from veriqant_bench.adapters.errors import (
    CostGateError,
    SubmissionError,
    UnsupportedCircuitError,
)
from veriqant_bench.adapters.types import JobSpec, JobStatus

pytestmark = pytest.mark.usefixtures("fake_aws_credentials")

ASYMMETRIC_2Q = (
    "OPENQASM 3.0;\n"
    'include "stdgates.inc";\n'
    "qubit[2] q;\n"
    "bit[2] c;\n"
    "x q[0];\n"
    "c[0] = measure q[0];\n"
    "c[1] = measure q[1];\n"
)

DYNAMIC = (
    "OPENQASM 3.0;\n"
    'include "stdgates.inc";\n'
    "qubit[1] q;\n"
    "bit[1] c;\n"
    "reset q[0];\n"
    "c[0] = measure q[0];\n"
)


# ---- pricing (F2 + staleness amendment) -----------------------------------------


def test_known_family_prices_per_task_plus_per_shot(tmp_path: Path) -> None:
    adapter = make_braket_adapter(tmp_path)  # rigetti ARN
    spec = JobSpec(circuits=[ASYMMETRIC_2Q] * 2, shots=100, seed=1)
    estimate = adapter.estimate_cost(spec)
    assert estimate.amount == 2 * Decimal("0.30") + 2 * 100 * Decimal("0.00035")
    assert estimate.confidence == "estimate"
    assert estimate.currency == "USD"
    assert estimate.qpu_seconds == 2.0  # nominal, not a precision claim


def test_unknown_device_means_unknown_cost(tmp_path: Path) -> None:
    adapter = make_braket_adapter(
        tmp_path, device_arn="arn:aws:braket:::device/qpu/newvendor/shiny"
    )
    estimate = adapter.estimate_cost(JobSpec(circuits=[ASYMMETRIC_2Q], shots=10, seed=1))
    assert estimate.confidence == "unknown"


def test_stale_price_table_means_unknown_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Amendment: >180 days unverified == missing data == refused by the gate.
    monkeypatch.setattr(
        braket_aws_module, "PRICE_TABLE_VERIFIED_ON", date.today().replace(year=2020)
    )
    adapter = make_braket_adapter(tmp_path)
    estimate = adapter.estimate_cost(JobSpec(circuits=[ASYMMETRIC_2Q], shots=10, seed=1))
    assert estimate.confidence == "unknown"


async def test_unknown_cost_is_refused_at_the_gate(tmp_path: Path) -> None:
    adapter = make_braket_adapter(
        tmp_path, device_arn="arn:aws:braket:::device/qpu/newvendor/shiny"
    )
    with pytest.raises(CostGateError, match="allow_unknown_cost"):
        await adapter.submit(JobSpec(circuits=[ASYMMETRIC_2Q], shots=10, seed=1))


def test_warn_window_surfaces_in_submit_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    monkeypatch.setattr(
        braket_aws_module,
        "PRICE_TABLE_VERIFIED_ON",
        (datetime.now(tz=UTC) - timedelta(days=120)).date(),
    )
    adapter = make_braket_adapter(tmp_path)
    warnings = adapter.submit_warnings(JobSpec(circuits=[ASYMMETRIC_2Q], shots=10, seed=1))
    assert any(PRICE_TABLE_SOURCE in warning for warning in warnings)


# ---- availability windows ----------------------------------------------------------


def test_unavailable_device_warns_but_does_not_block(tmp_path: Path) -> None:
    adapter = make_braket_adapter(tmp_path, available=False)
    warnings = adapter.submit_warnings(JobSpec(circuits=[ASYMMETRIC_2Q], shots=10, seed=1))
    assert any("availability window" in warning for warning in warnings)


def test_capabilities_record_windows_and_price_provenance(tmp_path: Path) -> None:
    adapter = make_braket_adapter(tmp_path)
    capabilities = adapter.capabilities()
    assert capabilities.provider_name == "aws-braket"
    assert capabilities.is_simulator is False
    assert capabilities.raw["availability_windows"][0]["day"] == "Everyday"
    assert capabilities.raw["price_table_source"] == PRICE_TABLE_SOURCE
    assert "price_table_verified_on" in capabilities.raw


def test_calibration_snapshot_is_provider_properties_verbatim(tmp_path: Path) -> None:
    adapter = make_braket_adapter(tmp_path)
    snapshot = adapter.calibration_snapshot()
    assert snapshot is not None
    assert snapshot.data["device_properties"] == {"specs": {"fidelity": 0.99}}


# ---- conversion boundaries -----------------------------------------------------------


async def test_dynamic_circuit_is_refused_with_circuit_index(tmp_path: Path) -> None:
    adapter = make_braket_adapter(tmp_path)
    with pytest.raises(UnsupportedCircuitError, match="circuit 1"):
        await adapter.submit(JobSpec(circuits=[ASYMMETRIC_2Q, DYNAMIC], shots=10, seed=1))
    assert adapter._ledger.monthly_totals().monetary == Decimal("0")  # released


async def test_submitted_programs_measure_all_without_pragma(tmp_path: Path) -> None:
    adapter = make_braket_adapter(tmp_path)
    handle = await adapter.submit(JobSpec(circuits=[ASYMMETRIC_2Q], shots=16, seed=1))
    task = adapter._jobs[handle.job_id][0]
    assert "result_bits = measure q;" in task._source
    assert "#pragma" not in task._source
    assert "cnot" not in task._source  # renames only where gates exist


# ---- execution path ---------------------------------------------------------------------


async def test_counts_are_reversed_into_qpr_convention(tmp_path: Path) -> None:
    adapter = make_braket_adapter(tmp_path)
    spec = JobSpec(circuits=[ASYMMETRIC_2Q], shots=64, seed=3)
    handle = await adapter.submit(spec)
    result = await adapter.await_result(handle, timeout=10.0)
    counts = result.counts[0]
    assert sum(counts.values()) == 64
    # x q[0]: Braket reports '10' (qubit 0 leftmost); QPR wants '01'.
    assert max(counts, key=lambda key: counts[key]) == "01"
    assert result.metadata["timing"]["source"].startswith("braket_task_metadata")
    assert result.started_at == datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    assert result.completed_at == datetime(2026, 6, 15, 12, 5, tzinfo=UTC)


async def test_batch_job_id_joins_task_arns_and_reattaches(tmp_path: Path) -> None:
    adapter = make_braket_adapter(tmp_path)
    spec = JobSpec(circuits=[ASYMMETRIC_2Q, ASYMMETRIC_2Q], shots=8, seed=2)
    handle = await adapter.submit(spec)
    assert ";" in handle.job_id
    fresh = make_braket_adapter(tmp_path, ledger=adapter._ledger)
    result = await fresh.await_result(handle, timeout=10.0)
    assert len(result.counts) == 2
    assert len(result.metadata["job_ids"]) == 2


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["COMPLETED", "COMPLETED"], JobStatus.COMPLETED),
        (["QUEUED", "QUEUED"], JobStatus.QUEUED),
        (["COMPLETED", "QUEUED"], JobStatus.RUNNING),
        (["RUNNING", "COMPLETED"], JobStatus.RUNNING),
        (["FAILED", "COMPLETED"], JobStatus.FAILED),
        (["CANCELLED", "RUNNING"], JobStatus.CANCELLED),
        (["CANCELLING", "COMPLETED"], JobStatus.RUNNING),
    ],
)
async def test_batch_status_aggregation(
    tmp_path: Path, states: list[str], expected: JobStatus
) -> None:
    adapter = make_braket_adapter(tmp_path)
    tasks = [SimpleNamespace(state=lambda state=state: state) for state in states]
    assert await adapter._provider_status(tasks) is expected


async def test_unrecognized_task_state_is_typed(tmp_path: Path) -> None:
    adapter = make_braket_adapter(tmp_path)
    with pytest.raises(SubmissionError, match="unrecognized Braket task state"):
        await adapter._provider_status([SimpleNamespace(state=lambda: "DAYDREAMING")])


def test_lazy_construction_is_offline() -> None:
    adapter = BraketAdapter("arn:aws:braket:::device/qpu/rigetti/whatever")
    assert adapter._device is None
    assert adapter._device_name() == "arn:aws:braket:::device/qpu/rigetti/whatever"


def _unused(*args: Any, **kwargs: Any) -> None:  # keep imports honest
    raise AssertionError
