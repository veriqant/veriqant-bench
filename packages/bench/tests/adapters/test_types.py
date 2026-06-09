from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pydantic
import pytest

from veriqore_bench.adapters import (
    VALID_TRANSITIONS,
    CalibrationSnapshot,
    CostEstimate,
    DeviceCapabilities,
    JobStatus,
    NoiseSpec,
)
from veriqore_bench.qpr._generated import Device


def make_capabilities(**overrides: object) -> DeviceCapabilities:
    base: dict[str, object] = {
        "device_name": "test_device",
        "num_qubits": 5,
        "native_gates": ["rz", "sx", "x", "cx"],
        "is_simulator": True,
    }
    base.update(overrides)
    return DeviceCapabilities.model_validate(base)


def test_to_qpr_device_validates_against_schema_model() -> None:
    capabilities = make_capabilities(coupling_map=[(0, 1), (1, 2)], device_version="1.2.3")
    snapshot = CalibrationSnapshot(
        source="noise_spec",
        retrieved_at=datetime(2026, 6, 9, tzinfo=UTC),
        data={"noise_spec": {"depolarizing_1q": 0.01}},
    )
    device = capabilities.to_qpr_device(snapshot)
    revalidated = Device.model_validate(device.model_dump(mode="json", exclude_none=True))
    assert revalidated.num_qubits == 5
    assert revalidated.calibration_snapshot == {"noise_spec": {"depolarizing_1q": 0.01}}
    assert revalidated.coupling_map is not None


def test_to_qpr_device_all_to_all_and_no_calibration() -> None:
    device = make_capabilities().to_qpr_device(None)
    assert device.coupling_map is None
    assert device.calibration_snapshot is None
    assert device.calibration_snapshot_at is None


def test_cost_estimate_free_and_currency_validation() -> None:
    free = CostEstimate.free()
    assert free.amount == Decimal(0)
    assert free.confidence == "exact"
    with pytest.raises(pydantic.ValidationError):
        CostEstimate(amount=Decimal(1), currency="euro", confidence="exact")
    with pytest.raises(pydantic.ValidationError):
        CostEstimate(amount=Decimal(-1), currency="USD", confidence="exact")


def test_job_status_terminality_and_transitions() -> None:
    assert not JobStatus.QUEUED.is_terminal
    assert not JobStatus.RUNNING.is_terminal
    for terminal in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        assert terminal.is_terminal
        assert VALID_TRANSITIONS[terminal] == frozenset()
    assert JobStatus.RUNNING in VALID_TRANSITIONS[JobStatus.QUEUED]
    assert JobStatus.COMPLETED not in VALID_TRANSITIONS[JobStatus.QUEUED]


def test_noise_spec_requires_t1_t2_together() -> None:
    with pytest.raises(pydantic.ValidationError, match="together"):
        NoiseSpec(t1_us=100.0)
    with pytest.raises(pydantic.ValidationError, match="together"):
        NoiseSpec(t2_us=100.0)


def test_noise_spec_rejects_unphysical_t2() -> None:
    with pytest.raises(pydantic.ValidationError, match="2 \\* t1_us"):
        NoiseSpec(t1_us=10.0, t2_us=25.0)
    NoiseSpec(t1_us=10.0, t2_us=20.0)  # boundary is allowed


def test_noise_spec_bounds() -> None:
    with pytest.raises(pydantic.ValidationError):
        NoiseSpec(depolarizing_1q=1.0)
    with pytest.raises(pydantic.ValidationError):
        NoiseSpec(readout_error_0to1=1.5)


def test_noise_spec_is_ideal() -> None:
    assert NoiseSpec().is_ideal
    assert not NoiseSpec(depolarizing_2q=0.01).is_ideal
    assert not NoiseSpec(t1_us=50.0, t2_us=70.0).is_ideal
