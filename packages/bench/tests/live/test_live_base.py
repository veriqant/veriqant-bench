"""LiveAdapterBase against a scripted dummy provider: opt-in layers, gate
wiring, ledger amendments, handle persistence, retry/auth classification,
and backoff polling."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from veriqant_bench.adapters.errors import (
    CostGateError,
    CredentialError,
    ExecutionError,
    LiveRefusedError,
    SubmissionError,
    TimeoutError,
    UnknownJobError,
)
from veriqant_bench.adapters.types import (
    CalibrationSnapshot,
    CostEstimate,
    DeviceCapabilities,
    JobHandle,
    JobResult,
    JobSpec,
    JobStatus,
)
from veriqant_bench.live import LiveAdapterBase, SpendLedger, SpendLimits

SPEC = JobSpec(circuits=["OPENQASM 3.0;\nqubit[1] q;\n"], shots=10, seed=7)


class DummyAuthError(Exception):
    """Stands in for a provider's invalid/expired-credentials exception."""


class DummyLiveAdapter(LiveAdapterBase):
    """Scripted live adapter: no network, fully deterministic."""

    name = "dummy_live"
    adapter_version = "0.0-test"

    def __init__(
        self,
        *,
        statuses: list[JobStatus | Exception] | None = None,
        missing_credentials: str | None = None,
        prevalidate_error: Exception | None = None,
        submit_error: Exception | None = None,
        result_metadata: dict[str, Any] | None = None,
        **live_kwargs: Any,
    ) -> None:
        super().__init__(poll_initial=0.001, poll_max=0.002, retry_base=0.001, **live_kwargs)
        self._statuses = statuses if statuses is not None else [JobStatus.COMPLETED]
        self._missing = missing_credentials
        self._prevalidate_error = prevalidate_error
        self._submit_error = submit_error
        self._result_metadata = result_metadata or {}
        self.submitted_specs: list[JobSpec] = []

    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            device_name="dummy-device",
            provider_name="dummy",
            num_qubits=2,
            native_gates=["x"],
            is_simulator=False,
        )

    def calibration_snapshot(self) -> CalibrationSnapshot | None:
        return CalibrationSnapshot(
            source="test", retrieved_at=datetime.now(tz=UTC), data={"t1_us": 100.0}
        )

    def estimate_cost(self, spec: JobSpec) -> CostEstimate:
        return CostEstimate(
            amount=Decimal("0"),
            currency="USD",
            confidence="estimate",
            qpu_seconds=10.0,
            heuristic="dummy_constant",
        )

    def _missing_credentials(self) -> str | None:
        return self._missing

    def _device_name(self) -> str:
        return "dummy-device"

    def _resume_kwargs(self) -> dict[str, Any]:
        # Real adapters must stay within RESUME_KWARG_ALLOWLIST; this dummy
        # does too, so the persisted handle files honor the same contract.
        return {"backend_name": "dummy-device"}

    def _auth_exception_types(self) -> tuple[type[BaseException], ...]:
        return (DummyAuthError,)

    async def _prevalidate(self, spec: JobSpec) -> Any:
        if self._prevalidate_error is not None:
            raise self._prevalidate_error
        return "prepared-artifacts"

    async def _do_submit(self, spec: JobSpec, prepared: Any) -> tuple[str, dict[str, Any]]:
        assert prepared == "prepared-artifacts"
        if self._submit_error is not None:
            raise self._submit_error
        self.submitted_specs.append(spec)
        return "dummy-job-1", {"transpilation": {"sdk": "dummy", "sdk_version": "0"}}

    async def _provider_status(self, job: Any) -> JobStatus:
        step = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        if isinstance(step, Exception):
            raise step
        return step

    async def _provider_result(self, job: Any, handle_document: dict[str, Any]) -> JobResult:
        now = datetime.now(tz=UTC)
        return JobResult(
            counts=[{"0": 10}],
            shots=10,
            started_at=now,
            completed_at=now,
            metadata=dict(self._result_metadata),
        )

    def _attach(self, job_id: str) -> Any:
        if job_id != "dummy-job-1":
            raise KeyError(job_id)
        return {"job_id": job_id}


@pytest.fixture
def permissive_limits() -> SpendLimits:
    return SpendLimits(
        monthly_monetary_cap=Decimal("100"),
        monthly_qpu_seconds_cap=100_000.0,
        source="test-permissive",
    )


@pytest.fixture
def ledger(tmp_path: Path) -> SpendLedger:
    return SpendLedger(tmp_path / "ledger.jsonl", lock_timeout=2.0)


def make_adapter(
    tmp_path: Path,
    permissive_limits: SpendLimits,
    ledger: SpendLedger,
    **kwargs: Any,
) -> DummyLiveAdapter:
    return DummyLiveAdapter(
        allow_live=kwargs.pop("allow_live", True),
        limits=kwargs.pop("limits", permissive_limits),
        ledger=ledger,
        jobs_dir=tmp_path / "jobs",
        **kwargs,
    )


# ---- the opt-in layers -------------------------------------------------------


async def test_all_missing_layers_reported_at_once(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(
        tmp_path,
        permissive_limits,
        ledger,
        allow_live=False,
        missing_credentials="TOKEN not set",
    )
    with pytest.raises(LiveRefusedError) as excinfo:
        await adapter.submit(SPEC)
    message = str(excinfo.value)
    assert "--live" in message
    assert "TOKEN not set" in message
    assert adapter.submitted_specs == []


async def test_flag_alone_is_not_enough(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(tmp_path, permissive_limits, ledger, missing_credentials="TOKEN not set")
    with pytest.raises(LiveRefusedError, match="credentials"):
        await adapter.submit(SPEC)


async def test_default_limits_refuse_before_any_provider_contact(
    tmp_path: Path, ledger: SpendLedger
) -> None:
    adapter = DummyLiveAdapter(
        allow_live=True, limits=SpendLimits(), ledger=ledger, jobs_dir=tmp_path / "jobs"
    )
    with pytest.raises(CostGateError):
        await adapter.submit(SPEC)
    assert adapter.submitted_specs == []
    assert ledger.monthly_totals().entries == 0


# ---- the happy path ----------------------------------------------------------


async def test_submit_persists_a_resumable_handle_file(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(tmp_path, permissive_limits, ledger)
    handle = await adapter.submit(SPEC)
    document = json.loads(adapter.handle_file(handle).read_text(encoding="utf-8"))
    assert document["adapter"] == "dummy_live"
    assert document["adapter_kwargs"] == {"backend_name": "dummy-device"}
    assert document["spec"]["circuits"] == SPEC.circuits
    assert document["ledger_entry_id"]
    assert document["calibration_at_submit"]["data"] == {"t1_us": 100.0}
    assert document["submit_metadata"]["transpilation"]["sdk"] == "dummy"
    # The gate committed exactly one estimate, named by the handle file.
    assert document["ledger_entry_id"] in ledger.path.read_text()


async def test_result_amends_ledger_from_a_fresh_instance(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    # Submit with one instance, fetch with another (the resume situation):
    # the ledger cross-reference must come from the handle file, not from
    # adapter instance state.
    submitter = make_adapter(tmp_path, permissive_limits, ledger)
    handle = await submitter.submit(SPEC)
    fetcher = make_adapter(
        tmp_path, permissive_limits, ledger, result_metadata={"actual_qpu_seconds": 4.5}
    )
    result = await fetcher.await_result(handle, timeout=5.0)
    assert result.counts == [{"0": 10}]
    entries = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    actuals = [entry for entry in entries if entry["kind"] == "actuals"]
    assert len(actuals) == 1
    assert actuals[0]["qpu_seconds"] == 4.5
    assert actuals[0]["ref"] == entries[0]["id"]


async def test_await_result_walks_states_with_backoff(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(
        tmp_path,
        permissive_limits,
        ledger,
        statuses=[JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.COMPLETED],
    )
    handle = await adapter.submit(SPEC)
    result = await adapter.await_result(handle, timeout=5.0)
    assert result.shots == 10


# ---- ordering: validation -> gate -> submit -----------------------------------


async def test_prevalidation_failure_never_touches_the_ledger(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    # Rule: budget is never reserved for a run that validation refuses —
    # not even as an estimate+released pair.
    adapter = make_adapter(
        tmp_path,
        permissive_limits,
        ledger,
        prevalidate_error=SubmissionError("unparseable circuit"),
    )
    with pytest.raises(SubmissionError, match="unparseable circuit"):
        await adapter.submit(SPEC)
    assert not ledger.path.exists()
    assert ledger.monthly_totals().entries == 0


async def test_validation_error_wins_over_a_gate_refusal(
    tmp_path: Path, ledger: SpendLedger
) -> None:
    # Zero-trust limits AND an invalid circuit: the user is told about the
    # circuit (validation runs before the gate), and nothing is charged.
    adapter = DummyLiveAdapter(
        allow_live=True,
        limits=SpendLimits(),
        ledger=ledger,
        jobs_dir=tmp_path / "jobs",
        prevalidate_error=SubmissionError("unparseable circuit"),
    )
    with pytest.raises(SubmissionError, match="unparseable circuit"):
        await adapter.submit(SPEC)
    assert ledger.monthly_totals().entries == 0


# ---- submit failure classification -------------------------------------------


async def test_definite_rejection_releases_the_charge(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(
        tmp_path,
        permissive_limits,
        ledger,
        submit_error=SubmissionError("malformed circuit"),
    )
    with pytest.raises(SubmissionError, match="malformed circuit"):
        await adapter.submit(SPEC)
    totals = ledger.monthly_totals()
    assert totals.qpu_seconds == 0.0  # charge returned
    entries = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert [entry["kind"] for entry in entries] == ["estimate", "released"]


async def test_auth_failure_at_submit_is_credentials_and_releases(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(
        tmp_path, permissive_limits, ledger, submit_error=DummyAuthError("token expired")
    )
    with pytest.raises(CredentialError, match="token expired"):
        await adapter.submit(SPEC)
    assert ledger.monthly_totals().qpu_seconds == 0.0


async def test_ambiguous_failure_keeps_the_charge(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(
        tmp_path,
        permissive_limits,
        ledger,
        submit_error=ConnectionError("socket dropped mid-request"),
    )
    with pytest.raises(SubmissionError, match="ambiguous submit failure"):
        await adapter.submit(SPEC)
    # The job MAY exist: conservatively, the estimate stays committed.
    assert ledger.monthly_totals().qpu_seconds == 10.0


# ---- polling resilience --------------------------------------------------------


async def test_transient_errors_are_retried(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(
        tmp_path,
        permissive_limits,
        ledger,
        statuses=[
            ConnectionError("blip"),
            ConnectionError("blip"),
            JobStatus.COMPLETED,
        ],
    )
    handle = await adapter.submit(SPEC)
    assert await adapter.poll(handle) is JobStatus.COMPLETED


async def test_transient_exhaustion_is_an_execution_error(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(
        tmp_path,
        permissive_limits,
        ledger,
        statuses=[
            ConnectionError("down"),
            ConnectionError("down"),
            ConnectionError("down"),
            ConnectionError("down"),
        ],
    )
    handle = await adapter.submit(SPEC)
    with pytest.raises(ExecutionError, match="unreachable"):
        await adapter.poll(handle)


async def test_credential_expiry_mid_poll_names_the_resume_path(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(
        tmp_path,
        permissive_limits,
        ledger,
        statuses=[DummyAuthError("token expired"), JobStatus.COMPLETED],
    )
    handle = await adapter.submit(SPEC)
    with pytest.raises(CredentialError, match="jobs resume"):
        await adapter.poll(handle)
    # Not retried away — and the handle file survives for the resume.
    assert adapter.handle_file(handle).is_file()


async def test_failed_job_surfaces_as_execution_error(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(tmp_path, permissive_limits, ledger, statuses=[JobStatus.FAILED])
    handle = await adapter.submit(SPEC)
    with pytest.raises(ExecutionError, match="ended failed"):
        await adapter.await_result(handle, timeout=5.0)


async def test_timeout_names_the_handle_file_and_resume_command(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(tmp_path, permissive_limits, ledger, statuses=[JobStatus.QUEUED])
    handle = await adapter.submit(SPEC)
    with pytest.raises(TimeoutError) as excinfo:
        await adapter.await_result(handle, timeout=0.05)
    message = str(excinfo.value)
    assert "veriqant-bench jobs resume" in message
    assert str(adapter.handle_file(handle)) in message


async def test_foreign_handle_is_unknown(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(tmp_path, permissive_limits, ledger)
    foreign = JobHandle(job_id="not-ours", adapter="dummy_live", submitted_at=datetime.now(tz=UTC))
    with pytest.raises(UnknownJobError):
        await adapter.poll(foreign)
    wrong_adapter = JobHandle(
        job_id="dummy-job-1", adapter="other", submitted_at=datetime.now(tz=UTC)
    )
    with pytest.raises(UnknownJobError):
        await adapter.poll(wrong_adapter)


def test_handle_filenames_survive_arn_job_ids(
    tmp_path: Path, permissive_limits: SpendLimits, ledger: SpendLedger
) -> None:
    adapter = make_adapter(tmp_path, permissive_limits, ledger)
    arn = "arn:aws:braket:eu-west-2:123456789012:quantum-task/abc-def"
    path = adapter.handle_file_for_job_id(arn)
    assert path.suffix == ".json"
    assert "/" not in path.name and ":" not in path.name
    # Distinct ids that sanitize identically still get distinct files.
    other = adapter.handle_file_for_job_id(arn.replace(":quantum-task/", ":quantum:task/"))
    assert other != path
