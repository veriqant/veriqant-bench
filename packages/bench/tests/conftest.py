"""Shared fixtures: the deterministic reference QPR, a fake adapter, and
fake live-provider transports for the IBM/Braket plumbing tests."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest

from veriqant_bench.adapters import (
    CalibrationSnapshot,
    CostEstimate,
    DeviceCapabilities,
    JobSpec,
    LocalAdapterBase,
)
from veriqant_bench.live import SpendLedger, SpendLimits
from veriqant_bench.qpr import QuantumPerformanceRecord, content_sha256
from veriqant_bench.qpr.example import example_record


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live-conformance",
        action="store_true",
        default=False,
        help="run the adapter conformance suite against REAL devices "
        "(manual, consumes provider quota/money; see docs/LIVE.md)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live-conformance"):
        return
    skip = pytest.mark.skip(reason="needs --live-conformance (manual, quota-consuming)")
    for item in items:
        if "live_conformance" in item.keywords:
            item.add_marker(skip)


def permissive_limits() -> SpendLimits:
    """High caps for plumbing tests — fake transports spend nothing real."""
    return SpendLimits(
        monthly_monetary_cap=Decimal("1000"),
        monthly_qpu_seconds_cap=100_000.0,
        source="test-permissive",
    )


# ---- IBM fakes ----------------------------------------------------------------


class FakeRuntimeService:
    """Stand-in for QiskitRuntimeService: one backend, a structured plan, and
    a job store so re-attachment (resume) can be exercised."""

    def __init__(self, backend: Any, plan: str | None = "open") -> None:
        self._backend = backend
        self._plan = plan
        self.jobs_store: dict[str, Any] = {}

    def active_account(self) -> dict[str, Any]:
        account: dict[str, Any] = {"channel": "ibm_quantum_platform"}
        if self._plan is not None:
            account["plan"] = self._plan
        return account

    def backend(self, name: str) -> Any:
        return self._backend

    def least_busy(self, **_kwargs: Any) -> Any:
        return self._backend

    def job(self, job_id: str) -> Any:
        return self.jobs_store[job_id]


def make_ibm_adapter(tmp_path: Path, **overrides: Any) -> tuple[Any, FakeRuntimeService]:
    """An IBMRuntimeAdapter wired to FakeManilaV2 + FakeRuntimeService with
    permissive limits and a tmp ledger. SamplerV2 runs in local mode, so the
    full submit→transpile→execute→result path is real qiskit code."""
    from qiskit_ibm_runtime.fake_provider import FakeManilaV2

    from veriqant_bench.adapters.ibm import IBMRuntimeAdapter

    backend = overrides.pop("backend", None) or FakeManilaV2()
    service = overrides.pop("service", None) or FakeRuntimeService(
        backend, plan=overrides.pop("plan", "open")
    )
    adapter = IBMRuntimeAdapter(
        allow_live=overrides.pop("allow_live", True),
        service=service,
        backend=backend,
        limits=overrides.pop("limits", permissive_limits()),
        ledger=overrides.pop("ledger", SpendLedger(tmp_path / "ledger.jsonl")),
        jobs_dir=tmp_path / "jobs",
        **overrides,
    )
    return adapter, service


# ---- Braket fakes ----------------------------------------------------------------


class FakeBraketTask:
    """One stub quantum task: samples the submitted program's exact
    distribution via braket_local's conversion machinery, then reports
    counts in Braket's qubit-0-leftmost key order."""

    _store: ClassVar[dict[str, FakeBraketTask]] = {}

    def __init__(self, arn: str, program_source: str | None = None, shots: int = 0) -> None:
        self.id = arn
        if program_source is not None:
            self._source = program_source
            self._shots = shots
            FakeBraketTask._store[arn] = self
        else:  # re-attachment path
            existing = FakeBraketTask._store[arn]
            self._source = existing._source
            self._shots = existing._shots
        self._state = "COMPLETED"

    def state(self) -> str:
        return self._state

    def result(self) -> Any:
        from datetime import UTC, datetime
        from types import SimpleNamespace

        from braket.devices import LocalSimulator
        from braket.ir.openqasm import Program

        # The live conversion emits measure-all; rebuild the probability
        # form so LocalSimulator can evaluate the exact distribution.
        lines = [
            line
            for line in self._source.splitlines()
            if "result_bits" not in line and not line.startswith("#pragma")
        ]
        lines.append("#pragma braket result probability")
        sim_result = LocalSimulator().run(Program(source="\n".join(lines)), shots=0).result()
        probabilities = np.asarray(sim_result.values[0], dtype=float).clip(min=0.0)
        probabilities /= probabilities.sum()
        # LocalSimulator contracts unused qubits; pad to the declared
        # register width exactly as braket_local's sampling path does.
        declared = re.search(r"qubit\[(\d+)\]", self._source)
        assert declared is not None
        num_qubits = int(declared.group(1))
        import zlib

        rng = np.random.default_rng(zlib.crc32(self.id.encode()))
        draws = rng.multinomial(self._shots, probabilities)
        counts: dict[str, int] = {}
        for index, count in enumerate(draws):
            if count:
                # Probability index is little-endian (q0 = LSB), i.e. the
                # binary form is the QPR string; Braket keys are reversed.
                counts[format(index, f"0{num_qubits}b")[::-1]] = int(count)
        self._metadata = {
            "createdAt": datetime(2026, 6, 15, 12, 0, tzinfo=UTC),
            "endedAt": datetime(2026, 6, 15, 12, 5, tzinfo=UTC),
        }
        return SimpleNamespace(measurement_counts=counts)

    def metadata(self) -> dict[str, Any]:
        from datetime import UTC, datetime

        return {
            "createdAt": datetime(2026, 6, 15, 12, 0, tzinfo=UTC),
            "endedAt": datetime(2026, 6, 15, 12, 5, tzinfo=UTC),
        }


class FakeBraketBatch:
    def __init__(self, tasks: list[FakeBraketTask]) -> None:
        self.tasks = tasks


class FakeAwsDevice:
    """Stub AwsDevice: properties + run_batch backed by FakeBraketTask."""

    def __init__(self, arn: str, *, available: bool = True) -> None:
        from types import SimpleNamespace

        self._arn = arn
        self.name = f"fake-{arn.rsplit('/', 1)[-1]}"
        self.is_available = available
        self.properties = SimpleNamespace(
            paradigm=SimpleNamespace(qubitCount=8),
            action={"openqasm": SimpleNamespace(supportedOperations=["cnot", "h", "x"])},
            service=SimpleNamespace(
                executionWindows=[
                    SimpleNamespace(
                        executionDay="Everyday", windowStartHour="09:00", windowEndHour="17:00"
                    )
                ]
            ),
            provider=SimpleNamespace(dict=lambda: {"specs": {"fidelity": 0.99}}),
            standardized=None,
        )
        self._task_counter = 0

    def run_batch(self, programs: list[Any], *, shots: int, **_kwargs: Any) -> FakeBraketBatch:
        tasks = []
        for program in programs:
            self._task_counter += 1
            arn = f"{self._arn}:task/{self._task_counter}"
            tasks.append(FakeBraketTask(arn, program_source=program.source, shots=shots))
        return FakeBraketBatch(tasks)


def make_braket_adapter(tmp_path: Path, **overrides: Any) -> Any:
    from veriqant_bench.adapters.braket_aws import BraketAdapter

    arn = overrides.pop("device_arn", "arn:aws:braket:::device/qpu/rigetti/fake-device")
    device = overrides.pop("device", None) or FakeAwsDevice(
        arn, available=overrides.pop("available", True)
    )
    return BraketAdapter(
        arn,
        allow_live=overrides.pop("allow_live", True),
        device_factory=lambda _arn: device,
        task_factory=FakeBraketTask,
        limits=overrides.pop("limits", permissive_limits()),
        ledger=overrides.pop("ledger", SpendLedger(tmp_path / "ledger.jsonl")),
        jobs_dir=tmp_path / "jobs",
        **overrides,
    )


def reseal_document(document: dict[str, Any]) -> dict[str, Any]:
    """Recompute a raw document's content hash after a deliberate mutation,
    so tests can isolate non-integrity verification checks."""
    document["integrity"]["content_sha256"] = content_sha256(document)
    return document


class StaticAdapter(LocalAdapterBase):
    """Deterministic fake backend: every circuit always measures all-zeros."""

    name = "static_test"
    adapter_version = "1.2.3"

    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            device_name="static_device",
            num_qubits=4,
            native_gates=["h", "cx"],
            is_simulator=True,
        )

    def calibration_snapshot(self) -> CalibrationSnapshot | None:
        return None

    def estimate_cost(self, spec: JobSpec) -> CostEstimate:
        return CostEstimate.free()

    def _prepare(self, spec: JobSpec) -> Any:
        return spec.circuits

    def _execute(self, prepared: Any, spec: JobSpec) -> tuple[list[dict[str, int]], dict[str, Any]]:
        counts = []
        for source in prepared:
            match = re.search(r"qubit\[(\d+)\]", source)
            width = int(match.group(1)) if match else 1
            counts.append({"0" * width: spec.shots})
        return counts, {"sdk_versions": {"static": "1.2.3"}}


@pytest.fixture
def record() -> QuantumPerformanceRecord:
    return example_record()


@pytest.fixture
def fake_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Static dummy credentials so the Braket layer check passes against
    stub transports; nothing real can be reached or spent."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
