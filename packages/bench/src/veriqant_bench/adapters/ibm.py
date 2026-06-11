"""IBM Quantum Runtime adapter (pip install 'veriqant-bench[ibm]').

- SamplerV2 in job mode only. Sessions are deliberately unsupported in v1:
  a session reserves dedicated (paid) time, which contradicts the
  zero-spend-by-default posture; job mode queues fairly on the open plan.
- Credentials come from the standard qiskit-ibm-runtime paths (the
  QISKIT_IBM_TOKEN environment variable or a previously saved account);
  this adapter never writes credentials.
- Open plan only in v1: monetarily free, but it consumes the per-month
  runtime quota, so estimates are charged against the QPU-seconds budget.
  The plan is read from the service's structured account data; if it
  cannot be determined, the adapter refuses (fail closed) — an account we
  cannot classify might be billable.
- Backend resolution is lazy: constructing the adapter is free and
  offline. The backend (named, or least-busy when omitted) is resolved on
  first use, which is also why a least-busy submission's ledger entry may
  carry the placeholder device name 'ibm:least-busy' — the sealed QPR
  always records the exact resolved device.
"""

from __future__ import annotations

import contextlib
import platform
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from typing import Any

from veriqant_bench import __version__
from veriqant_bench.live.base import LiveAdapterBase

from .errors import LiveRefusedError, SubmissionError
from .types import (
    CalibrationSnapshot,
    CostEstimate,
    DeviceCapabilities,
    JobResult,
    JobSpec,
    JobStatus,
)

# QPU-seconds heuristic: per-circuit overhead (load/initialization) plus a
# per-shot term (typical repetition delay ~250us plus readout, rounded up
# generously). The quota estimate deliberately over-bounds: refusing too
# often is safe and annoying; refusing too rarely is the failure mode this
# system exists to exclude. Any future change to these constants is
# evaluated against that sentence. Provider-reported usage amends the
# ledger after every job, so the bookkeeping converges toward truth.
PER_CIRCUIT_OVERHEAD_S = 1.0
PER_SHOT_S = 0.001
QUOTA_HEURISTIC = "per_circuit_1s_per_shot_1ms"

_STATUS_MAP = {
    "INITIALIZING": JobStatus.QUEUED,
    "QUEUED": JobStatus.QUEUED,
    "VALIDATING": JobStatus.QUEUED,
    "RUNNING": JobStatus.RUNNING,
    "DONE": JobStatus.COMPLETED,
    "ERROR": JobStatus.FAILED,
    "CANCELLED": JobStatus.CANCELLED,
}

LEAST_BUSY_PLACEHOLDER = "ibm:least-busy"


def _json_safe(value: Any) -> Any:
    """Provider property payloads contain datetimes; make them recordable."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, int | float | str | bool) or value is None:
        return value
    return str(value)


class IBMRuntimeAdapter(LiveAdapterBase):
    """Live execution on IBM Quantum via qiskit-ibm-runtime SamplerV2
    (job mode, open plan only)."""

    name = "ibm_runtime"
    adapter_version = version("qiskit-ibm-runtime")

    def __init__(
        self,
        backend_name: str | None = None,
        *,
        allow_live: bool = False,
        service: Any | None = None,
        backend: Any | None = None,
        **live_kwargs: Any,
    ) -> None:
        super().__init__(allow_live=allow_live, **live_kwargs)
        self._backend_name = backend_name
        self._service = service
        self._backend = backend  # resolved lazily; construction is offline

    # ---- backend resolution / credentials ---------------------------------

    def _service_or_raise(self) -> Any:
        if self._service is None:
            try:
                from qiskit_ibm_runtime import QiskitRuntimeService

                self._service = QiskitRuntimeService()
            except Exception as exc:
                raise LiveRefusedError(
                    "no usable IBM Quantum account: set QISKIT_IBM_TOKEN or save one "
                    f"with QiskitRuntimeService.save_account() ({exc})"
                ) from exc
        return self._service

    def _resolve_backend(self) -> Any:
        if self._backend is None:
            service = self._service_or_raise()
            if self._backend_name:
                self._backend = service.backend(self._backend_name)
            else:
                self._backend = service.least_busy(operational=True, simulator=False)
        return self._backend

    def _missing_credentials(self) -> str | None:
        import os

        if self._service is not None or self._backend is not None:
            return None
        if os.environ.get("QISKIT_IBM_TOKEN"):
            return None
        # Presence check only (no network): a saved account on disk counts.
        try:
            from qiskit_ibm_runtime.accounts import management

            if management.AccountManager.list():
                return None
        except Exception:  # pragma: no cover - account store API drift
            pass
        return "QISKIT_IBM_TOKEN not set and no saved qiskit-ibm-runtime account"

    def _auth_exception_types(self) -> tuple[type[BaseException], ...]:
        try:
            from qiskit_ibm_runtime.exceptions import IBMNotAuthorizedError

            return (IBMNotAuthorizedError,)
        except ImportError:  # pragma: no cover - extras not installed
            return ()

    def _device_name(self) -> str:
        if self._backend is not None:
            return str(getattr(self._backend, "name", "unknown"))
        # No network for a gate refusal message: keep the placeholder; the
        # QPR always carries the exact resolved device.
        return self._backend_name or LEAST_BUSY_PLACEHOLDER

    def _resume_kwargs(self) -> dict[str, Any]:
        return {"backend_name": self._device_name()}

    def _require_open_plan(self) -> None:
        """Refuse anything that is not positively the free open plan.

        Fail closed: an account whose plan cannot be determined from the
        structured account data might be billable, and only the open plan's
        quota accounting is implemented. (The exact field names are pinned
        against a real account — see the build report; until then this
        reads the documented 'plan' key and refuses on absence.)"""
        service = self._service_or_raise()
        account: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            account = dict(service.active_account() or {})
        plan = account.get("plan")
        if plan is None:
            raise LiveRefusedError(
                "cannot determine the IBM plan for the active account "
                f"(structured fields: {sorted(account)}); refusing rather than "
                "risking a billable submission. Only the open plan is supported in v1."
            )
        if str(plan).strip().lower() != "open":
            raise LiveRefusedError(
                f"premium billing not supported in v1: the active IBM account is on "
                f"plan {plan!r}. Only the monetarily-free open plan is gated correctly."
            )

    # ---- discovery ----------------------------------------------------------

    def capabilities(self) -> DeviceCapabilities:
        backend = self._resolve_backend()
        coupling = getattr(backend, "coupling_map", None)
        coupling_edges = [(int(a), int(b)) for a, b in coupling.get_edges()] if coupling else None
        operations = sorted(str(op) for op in getattr(backend, "operation_names", []))
        return DeviceCapabilities(
            device_name=str(getattr(backend, "name", "unknown")),
            device_version=str(getattr(backend, "backend_version", "") or "") or None,
            provider_name="ibm",
            num_qubits=int(backend.num_qubits),
            native_gates=operations,
            coupling_map=coupling_edges,
            max_shots=getattr(backend, "max_shots", None),  # not uniform on BackendV2
            supports_midcircuit_measurement="measure" in operations and "reset" in operations,
            is_simulator=False,
            raw={
                "provider": "qiskit-ibm-runtime",
                "qiskit_ibm_runtime_version": self.adapter_version,
                "dynamic_circuits": "if_else" in operations,
            },
        )

    def calibration_snapshot(self) -> CalibrationSnapshot | None:
        """Backend properties verbatim (T1/T2, gate/readout errors,
        calibration timestamps) — raw, never summarized."""
        backend = self._resolve_backend()
        properties = getattr(backend, "properties", None)
        if properties is None:
            return None
        payload = properties()
        if payload is None:
            return None
        raw = payload.to_dict() if hasattr(payload, "to_dict") else payload
        return CalibrationSnapshot(
            source="provider_api",
            retrieved_at=datetime.now(tz=UTC),
            data={"backend_properties": _json_safe(raw)},
        )

    def estimate_cost(self, spec: JobSpec) -> CostEstimate:
        # Local-only by design: constants, no backend contact.
        circuits = len(spec.circuits)
        seconds = circuits * PER_CIRCUIT_OVERHEAD_S + circuits * spec.shots * PER_SHOT_S
        return CostEstimate(
            amount=Decimal(0),
            currency="USD",
            confidence="estimate",
            qpu_seconds=seconds,
            heuristic=QUOTA_HEURISTIC,
        )

    # ---- provider hooks -------------------------------------------------------

    async def _do_submit(self, spec: JobSpec) -> tuple[str, dict[str, Any]]:
        self._require_open_plan()
        backend = self._resolve_backend()
        from qiskit import qasm3, transpile
        from qiskit_ibm_runtime import SamplerV2

        try:
            circuits = [qasm3.loads(source) for source in spec.circuits]
        except Exception as exc:
            raise SubmissionError(f"invalid OpenQASM 3: {exc}") from exc
        isa_circuits = transpile(
            circuits, backend=backend, optimization_level=1, seed_transpiler=spec.seed
        )
        sampler = SamplerV2(mode=backend)
        # Local-mode fake backends sample; seed them for reproducible tests.
        # Real backends reject simulator options — suppression is deliberate.
        with contextlib.suppress(Exception):
            sampler.options.simulator.seed_simulator = spec.seed
        job = sampler.run(isa_circuits, shots=spec.shots)
        job_id = str(job.job_id())
        self._jobs[job_id] = job
        return job_id, {
            "transpilation": {
                "sdk": "qiskit",
                "sdk_version": version("qiskit"),
                "optimization_level": 1,
                "settings": {
                    "target": str(getattr(backend, "name", "unknown")),
                    "isa": True,
                    "seed_transpiler": spec.seed,
                },
            }
        }

    async def _provider_status(self, job: Any) -> JobStatus:
        raw = job.status()
        label = str(getattr(raw, "name", raw)).upper()
        status = _STATUS_MAP.get(label)
        if status is None:
            raise SubmissionError(
                f"unrecognized IBM job status {label!r} (qiskit-ibm-runtime "
                f"{self.adapter_version}); refusing to guess a lifecycle state"
            )
        return status

    async def _provider_result(self, job: Any, handle_document: dict[str, Any]) -> JobResult:
        result = job.result()
        counts = [self._pub_counts(pub_result) for pub_result in result]
        metrics_payload = self._job_metrics(job)
        metadata: dict[str, Any] = {
            "sdk_versions": {
                "qiskit": version("qiskit"),
                "qiskit-ibm-runtime": self.adapter_version,
                "veriqant-bench": __version__,
            },
            "platform": platform.platform(),
            "job_ids": [str(job.job_id())],
            **metrics_payload,
        }
        retrieved_at = datetime.now(tz=UTC)
        timing = metrics_payload.get("timing", {})
        started_at = timing.get("_started_at") or retrieved_at
        completed_at = timing.get("_finished_at") or retrieved_at
        timing.pop("_started_at", None)
        timing.pop("_finished_at", None)
        return JobResult(
            counts=counts,
            shots=sum(counts[0].values()) if counts and counts[0] else 1,
            started_at=started_at,
            completed_at=completed_at,
            metadata=metadata,
        )

    def _pub_counts(self, pub_result: Any) -> dict[str, int]:
        """Counts from one pub result, in QPR bit order.

        Qiskit's get_counts keys already read c[n-1]..c[0] (bit 0 rightmost);
        spaces appear only between multiple classical registers, which our
        single-register circuits do not produce — but are stripped
        defensively. An unrecognized data shape is a typed error, never a
        guess (the SamplerV2 result layout has drifted across releases)."""
        data = pub_result.data
        arrays = [
            getattr(data, name)
            for name in dir(data)
            if not name.startswith("_") and hasattr(getattr(data, name), "get_counts")
        ]
        if len(arrays) != 1:
            raise SubmissionError(
                f"unrecognized sampler result shape: expected exactly one classical "
                f"register with counts, found {len(arrays)} (qiskit-ibm-runtime "
                f"{self.adapter_version})"
            )
        raw_counts = arrays[0].get_counts()
        return {key.replace(" ", ""): int(count) for key, count in raw_counts.items()}

    def _job_metrics(self, job: Any) -> dict[str, Any]:
        """Queue vs execution timing + actual usage, where the API offers it.
        Local-mode PrimitiveJobs expose no metrics(); say so honestly."""
        metrics_fn = getattr(job, "metrics", None)
        if metrics_fn is None:
            return {"timing": {"source": "unavailable_no_job_metrics"}}
        try:
            metrics = metrics_fn() or {}
        except Exception:
            return {"timing": {"source": "unavailable_metrics_call_failed"}}
        timestamps = metrics.get("timestamps", {})
        created = _as_datetime(timestamps.get("created"))
        running = _as_datetime(timestamps.get("running"))
        finished = _as_datetime(timestamps.get("finished"))
        timing: dict[str, Any] = {"source": "provider_job_metrics"}
        if created and running:
            timing["queue_seconds"] = max((running - created).total_seconds(), 0.0)
        if running and finished:
            timing["execution_seconds"] = max((finished - running).total_seconds(), 0.0)
        if running:
            timing["_started_at"] = running
        if finished:
            timing["_finished_at"] = finished
        payload: dict[str, Any] = {"timing": timing}
        usage = metrics.get("usage", {})
        if isinstance(usage, dict) and usage.get("seconds") is not None:
            payload["actual_qpu_seconds"] = float(usage["seconds"])
        return payload

    def _attach(self, job_id: str) -> Any:
        return self._service_or_raise().job(job_id)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
