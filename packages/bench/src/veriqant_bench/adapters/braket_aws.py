"""Amazon Braket live adapter (pip install 'veriqant-bench[braket]').

- Region and credentials come from standard AWS configuration (environment,
  ~/.aws, instance roles); results land in the account's default Braket S3
  location unless s3_folder is given. This adapter never writes credentials.
- Cost model: a static price table per device family (per-task fee +
  per-shot fee), verified PRICE_TABLE_VERIFIED_ON against
  PRICE_TABLE_SOURCE. Devices absent from the table get confidence
  "unknown", which the cost gate refuses unless allow_unknown_cost is set
  in the limits file (dangerous; documented). A table older than 90 days
  warns loudly; older than 180 days the price counts as unknown and the
  gate refuses — stale data gets the same treatment as missing data.
- Circuits reuse the QASM3 -> Braket dialect conversion from braket_local,
  including its UnsupportedCircuitError boundaries, but with an explicit
  final measure-all instead of the probability pragma. Live measurement
  counts use Braket's qubit-0-leftmost key order and are reversed into the
  QPR convention. NOTE: no Braket-sourced QPR may be published until this
  bit order has been confirmed once on real hardware via
  `pytest --live-conformance` (see docs/LIVE.md); first light is IBM-only
  and live Braket validation is a separate, deliberate, budgeted decision.
- Braket devices have operating windows: submitting outside them queues
  until the next window, so submit_warnings() surfaces availability at
  submit time and the warning is recorded in the submit metadata.
"""

from __future__ import annotations

import platform
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.metadata import version
from typing import Any

from veriqant_bench import __version__
from veriqant_bench.live.base import LiveAdapterBase
from veriqant_bench.live.gate import classify_price_table_age

from .braket_local import GATE_RENAMES, convert_qasm3_to_braket
from .errors import SubmissionError
from .types import (
    CalibrationSnapshot,
    CostEstimate,
    DeviceCapabilities,
    JobResult,
    JobSpec,
    JobStatus,
)

PRICE_TABLE_VERIFIED_ON = date(2026, 6, 10)
PRICE_TABLE_SOURCE = "https://aws.amazon.com/braket/pricing/"
# (per_task USD, per_shot USD) by device-family substring of the ARN.
# Re-verify against PRICE_TABLE_SOURCE before raising a monetary cap; the
# verification date above drives the staleness rule in the module docstring.
PRICE_TABLE: dict[str, tuple[Decimal, Decimal]] = {
    "ionq": (Decimal("0.30"), Decimal("0.01")),
    "rigetti": (Decimal("0.30"), Decimal("0.00035")),
    "iqm": (Decimal("0.30"), Decimal("0.00145")),
    "quera": (Decimal("0.30"), Decimal("0.01")),
}
PRICE_HEURISTIC = f"static_price_table_{PRICE_TABLE_VERIFIED_ON.isoformat()}"
# Braket bills per task + per shot, not per QPU-second; a nominal one
# second per circuit keeps the quota ledger populated without pretending
# precision. The monetary budget is the binding one on Braket.
NOMINAL_SECONDS_PER_CIRCUIT = 1.0

_STATUS_MAP = {
    "CREATED": JobStatus.QUEUED,
    "QUEUED": JobStatus.QUEUED,
    "RUNNING": JobStatus.RUNNING,
    "COMPLETED": JobStatus.COMPLETED,
    "FAILED": JobStatus.FAILED,
    "CANCELLED": JobStatus.CANCELLED,
    "CANCELLING": JobStatus.RUNNING,
}


class BraketAdapter(LiveAdapterBase):
    """Live execution on AWS Braket devices with a static-price cost gate."""

    name = "braket_aws"
    adapter_version = version("amazon-braket-sdk")

    def __init__(
        self,
        device_arn: str,
        *,
        allow_live: bool = False,
        s3_folder: tuple[str, str] | None = None,
        device_factory: Any | None = None,
        task_factory: Any | None = None,
        **live_kwargs: Any,
    ) -> None:
        super().__init__(allow_live=allow_live, **live_kwargs)
        self._device_arn = device_arn
        self._s3_folder = s3_folder
        self._device_factory = device_factory
        self._task_factory = task_factory
        self._device: Any | None = None  # resolved lazily; construction is offline

    # ---- credentials / identity ----------------------------------------------

    def _resolve_device(self) -> Any:
        if self._device is None:
            factory = self._device_factory
            if factory is None:  # pragma: no cover - network path
                from braket.aws import AwsDevice

                factory = AwsDevice
            self._device = factory(self._device_arn)
        return self._device

    def _missing_credentials(self) -> str | None:
        try:
            import boto3

            session = boto3.session.Session()
            if session.get_credentials() is None:
                return "no AWS credentials found (environment, ~/.aws, or role)"
        except Exception as exc:  # pragma: no cover - boto3 ships with braket
            return f"boto3 unavailable: {exc}"
        return None

    def _auth_exception_types(self) -> tuple[type[BaseException], ...]:
        try:
            from botocore.exceptions import (
                NoCredentialsError,
                TokenRetrievalError,
                UnauthorizedSSOTokenError,
            )

            return (NoCredentialsError, TokenRetrievalError, UnauthorizedSSOTokenError)
        except ImportError:  # pragma: no cover - extras not installed
            return ()

    def _device_name(self) -> str:
        if self._device is not None:
            return str(getattr(self._device, "name", self._device_arn))
        return self._device_arn

    def _resume_kwargs(self) -> dict[str, Any]:
        return {"device_arn": self._device_arn}

    # ---- discovery --------------------------------------------------------------

    def capabilities(self) -> DeviceCapabilities:
        device = self._resolve_device()
        properties = device.properties
        paradigm = getattr(properties, "paradigm", None)
        qubit_count = int(getattr(paradigm, "qubitCount", 1) or 1)
        native_gates: list[str] = []
        action = getattr(properties, "action", {}) or {}
        for entry in action.values():
            operations = getattr(entry, "supportedOperations", None)
            if operations:
                native_gates = sorted(str(op) for op in operations)
                break
        windows = []
        service = getattr(properties, "service", None)
        for window in getattr(service, "executionWindows", []) or []:
            windows.append(
                {
                    "day": str(getattr(window, "executionDay", "")),
                    "start": str(getattr(window, "windowStartHour", "")),
                    "end": str(getattr(window, "windowEndHour", "")),
                }
            )
        return DeviceCapabilities(
            device_name=self._device_name(),
            provider_name="aws-braket",
            num_qubits=qubit_count,
            native_gates=native_gates,
            coupling_map=None,
            max_shots=None,
            supports_midcircuit_measurement=False,
            is_simulator=False,
            raw={
                "provider": "amazon-braket-sdk",
                "device_arn": self._device_arn,
                "availability_windows": windows,
                "is_available_now": bool(getattr(device, "is_available", False)),
                "price_table_verified_on": PRICE_TABLE_VERIFIED_ON.isoformat(),
                "price_table_source": PRICE_TABLE_SOURCE,
            },
        )

    def calibration_snapshot(self) -> CalibrationSnapshot | None:
        properties = self._resolve_device().properties
        payload = None
        for attribute in ("provider", "standardized"):
            candidate = getattr(properties, attribute, None)
            if candidate is not None:
                payload = candidate
                break
        if payload is None:
            return None
        raw = payload.dict() if hasattr(payload, "dict") else payload
        return CalibrationSnapshot(
            source="provider_api",
            retrieved_at=datetime.now(tz=UTC),
            data={"device_properties": _json_safe(raw)},
        )

    def estimate_cost(self, spec: JobSpec) -> CostEstimate:
        # Local-only by design: the static table, no device contact.
        staleness = classify_price_table_age(PRICE_TABLE_VERIFIED_ON)
        family = next((key for key in PRICE_TABLE if key in self._device_arn.lower()), None)
        if family is None or staleness == "stale":
            # Unknown device, or a table nobody has verified in >180 days:
            # both mean the cost cannot honestly be charged to a budget.
            return CostEstimate(
                amount=Decimal(0),
                currency="USD",
                confidence="unknown",
                heuristic=PRICE_HEURISTIC,
            )
        per_task, per_shot = PRICE_TABLE[family]
        circuits = len(spec.circuits)
        amount = circuits * per_task + circuits * spec.shots * per_shot
        return CostEstimate(
            amount=amount,
            currency="USD",
            confidence="estimate",
            qpu_seconds=circuits * NOMINAL_SECONDS_PER_CIRCUIT,
            heuristic=PRICE_HEURISTIC,
        )

    def submit_warnings(self, spec: JobSpec) -> list[str]:
        warnings: list[str] = []
        if classify_price_table_age(PRICE_TABLE_VERIFIED_ON) == "warn":
            warnings.append(
                f"the Braket price table was last verified {PRICE_TABLE_VERIFIED_ON} "
                f"(>90 days ago); re-check {PRICE_TABLE_SOURCE} before trusting "
                "estimates with a raised cap"
            )
        if not bool(getattr(self._resolve_device(), "is_available", True)):
            warnings.append(
                f"device '{self._device_name()}' is outside its availability window "
                "right now; the job will queue until the next window"
            )
        return warnings

    # ---- provider hooks ------------------------------------------------------------

    async def _prevalidate(self, spec: JobSpec) -> Any:
        """Dialect conversion before the cost gate: a circuit the conversion
        refuses (dynamic constructs, partial measurement) must never reserve
        budget."""
        from braket.ir.openqasm import Program

        programs = []
        qubit_counts = []
        for index, source in enumerate(spec.circuits):
            try:
                converted, num_qubits = convert_qasm3_to_braket(
                    source, result_pragma=False, emit_measure_all=True
                )
            except SubmissionError as exc:
                raise type(exc)(f"circuit {index}: {exc}") from exc
            programs.append(Program(source=converted))
            qubit_counts.append(num_qubits)
        return programs, qubit_counts

    async def _do_submit(self, spec: JobSpec, prepared: Any) -> tuple[str, dict[str, Any]]:
        device = self._resolve_device()
        programs, qubit_counts = prepared
        run_kwargs: dict[str, Any] = {"shots": spec.shots}
        if self._s3_folder is not None:
            run_kwargs["s3_destination_folder"] = self._s3_folder
        batch = device.run_batch(programs, **run_kwargs)
        tasks = list(batch.tasks)
        job_id = ";".join(str(task.id) for task in tasks)
        self._jobs[job_id] = tasks
        return job_id, {
            "task_arns": [str(task.id) for task in tasks],
            "qubit_counts": qubit_counts,
            "warnings": self.submit_warnings(spec),
            "transpilation": {
                "sdk": "veriqant-bench",
                "sdk_version": __version__,
                "settings": {
                    "conversion": "openqasm3-to-braket-dialect",
                    "gate_renames": GATE_RENAMES,
                    "measure_strategy": "all-qubits-device-sampling",
                },
            },
        }

    async def _provider_status(self, job: Any) -> JobStatus:
        statuses = []
        for task in job:
            label = str(task.state()).upper()
            status = _STATUS_MAP.get(label)
            if status is None:
                raise SubmissionError(
                    f"unrecognized Braket task state {label!r} (amazon-braket-sdk "
                    f"{self.adapter_version}); refusing to guess a lifecycle state"
                )
            statuses.append(status)
        if any(status is JobStatus.FAILED for status in statuses):
            return JobStatus.FAILED
        if any(status is JobStatus.CANCELLED for status in statuses):
            return JobStatus.CANCELLED
        if all(status is JobStatus.COMPLETED for status in statuses):
            return JobStatus.COMPLETED
        if all(status is JobStatus.QUEUED for status in statuses):
            return JobStatus.QUEUED
        return JobStatus.RUNNING

    async def _provider_result(self, job: Any, handle_document: dict[str, Any]) -> JobResult:
        counts: list[dict[str, int]] = []
        created_stamps: list[datetime] = []
        ended_stamps: list[datetime] = []
        for task in job:
            result = task.result()
            raw_counts = dict(result.measurement_counts)
            # Braket keys put qubit 0 leftmost; QPR wants bit 0 rightmost.
            # Confirmed on fakes; real-hardware confirmation is gated behind
            # --live-conformance before any Braket QPR is published (Q3).
            counts.append({key[::-1]: int(value) for key, value in raw_counts.items()})
            metadata_fn = getattr(task, "metadata", None)
            if metadata_fn is not None:
                task_metadata = metadata_fn() or {}
                created = task_metadata.get("createdAt")
                ended = task_metadata.get("endedAt")
                if isinstance(created, datetime):
                    created_stamps.append(
                        created if created.tzinfo else created.replace(tzinfo=UTC)
                    )
                if isinstance(ended, datetime):
                    ended_stamps.append(ended if ended.tzinfo else ended.replace(tzinfo=UTC))
        retrieved_at = datetime.now(tz=UTC)
        timing_source = (
            "braket_task_metadata_created_to_ended_no_execution_split"
            if created_stamps and ended_stamps
            else "unavailable_braket_no_task_timestamps"
        )
        return JobResult(
            counts=counts,
            shots=sum(counts[0].values()) if counts and counts[0] else 1,
            started_at=min(created_stamps) if created_stamps else retrieved_at,
            completed_at=max(ended_stamps) if ended_stamps else retrieved_at,
            metadata={
                "sdk_versions": {
                    "amazon-braket-sdk": self.adapter_version,
                    "veriqant-bench": __version__,
                },
                "platform": platform.platform(),
                "job_ids": [str(task.id) for task in job],
                "timing": {
                    # Braket exposes creation/end per task but no queue vs
                    # execution split; never present created→ended as
                    # execution time.
                    "source": timing_source,
                },
            },
        )

    def _attach(self, job_id: str) -> Any:
        factory = self._task_factory
        if factory is None:  # pragma: no cover - network path
            from braket.aws import AwsQuantumTask

            factory = AwsQuantumTask
        return [factory(arn) for arn in job_id.split(";")]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, int | float | str | bool) or value is None:
        return value
    return str(value)
