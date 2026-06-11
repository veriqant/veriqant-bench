"""Shared machinery for adapters that talk to real provider queues.

Layered opt-in (the default is "no"): submission requires allow_live=True
(set only by the --live CLI flag or explicit code), valid credentials, and
the cost gate passing against the local ledger. The layer check reports ALL
missing layers at once in a typed LiveRefusedError.

Live queues run minutes-to-hours, so polling backs off exponentially with
jitter (2s -> x1.6 -> capped at 60s by default). Status polls retry through
transient network errors; provider auth failures surface as CredentialError
(re-authenticate, then resume); submits are NEVER blindly retried. Every
accepted submission persists its JobHandle (plus the full JobSpec, the
calibration snapshot at submit time, and the ledger entry id) to a handle
file, so an interrupted wait can be resumed later from a fresh process via
provider-side job re-attachment. Resuming polls and fetches — it has no
code path to a provider submit, which is why it needs credentials but not
the live flag or the cost gate.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from veriqant_bench.adapters.errors import (
    CredentialError,
    ExecutionError,
    LiveRefusedError,
    SubmissionError,
    TimeoutError,
    UnknownJobError,
)
from veriqant_bench.adapters.lifecycle import AwaitResultMixin
from veriqant_bench.adapters.types import (
    CalibrationSnapshot,
    CostEstimate,
    DeviceCapabilities,
    JobHandle,
    JobResult,
    JobSpec,
    JobStatus,
)

from .gate import check_cost_gate
from .ledger import SpendLedger
from .limits import USER_CONFIG_DIR, SpendLimits, load_limits

DEFAULT_JOBS_DIR = USER_CONFIG_DIR / "jobs"

POLL_INITIAL_SECONDS = 2.0
POLL_FACTOR = 1.6
POLL_MAX_SECONDS = 60.0
POLL_JITTER = 0.2
DEFAULT_LIVE_TIMEOUT_SECONDS = 14_400.0  # 4 hours: live queues run minutes-to-hours
TRANSIENT_RETRIES = 3
TRANSIENT_RETRY_BASE_SECONDS = 2.0
# ConnectionError and the builtin TimeoutError are both OSError subclasses;
# OSError is the transport-failure umbrella for retry classification.
_TRANSIENT_ERRORS = (OSError,)


class LiveAdapterBase(AwaitResultMixin, ABC):
    """Base for live adapters: opt-in layers, cost gating, ledger
    bookkeeping, backoff polling, and handle persistence."""

    name: str
    adapter_version: str

    def __init__(
        self,
        *,
        allow_live: bool = False,
        limits: SpendLimits | None = None,
        ledger: SpendLedger | None = None,
        jobs_dir: Path | None = None,
        poll_initial: float = POLL_INITIAL_SECONDS,
        poll_max: float = POLL_MAX_SECONDS,
        retry_base: float = TRANSIENT_RETRY_BASE_SECONDS,
    ) -> None:
        self._allow_live = allow_live
        self._limits = limits or load_limits()
        self._ledger = ledger or SpendLedger()
        self._jobs_dir = jobs_dir or DEFAULT_JOBS_DIR
        self._poll_initial = poll_initial
        self._poll_max = poll_max
        self._retry_base = retry_base
        self._jobs: dict[str, Any] = {}

    # ---- protocol members every concrete live adapter implements ---------

    @abstractmethod
    def capabilities(self) -> DeviceCapabilities: ...

    @abstractmethod
    def calibration_snapshot(self) -> CalibrationSnapshot | None: ...

    @abstractmethod
    def estimate_cost(self, spec: JobSpec) -> CostEstimate:
        """Local-only estimation: the gate must work offline and fail
        closed, so this must never touch the network."""

    # ---- provider hooks ---------------------------------------------------

    @abstractmethod
    def _missing_credentials(self) -> str | None:
        """Human-readable description of missing credentials, or None.
        Must not touch the network (presence check, not validity check)."""

    @abstractmethod
    def _device_name(self) -> str:
        """Target device identifier for gating/ledger messages."""

    async def _prevalidate(self, spec: JobSpec) -> Any:
        """Everything that can fail WITHOUT spending: circuit parsing and
        conversion, plan/account classification, local transpilation. Runs
        after the opt-in layers but BEFORE the cost gate — budget is never
        reserved for a run that validation would have refused anyway. Raise
        typed errors (SubmissionError/UnsupportedCircuitError/
        LiveRefusedError); the return value is handed to _do_submit."""
        return None

    @abstractmethod
    async def _do_submit(self, spec: JobSpec, prepared: Any) -> tuple[str, dict[str, Any]]:
        """Provider submission of artifacts prevalidated by _prevalidate.
        Returns (job_id, submit_metadata). Never called until every opt-in
        layer AND the cost gate have passed; never retried. Raise
        SubmissionError for a definite synchronous rejection (the job never
        reached the queue — its budget charge is released); let transport
        errors propagate (ambiguous: the job MAY exist, the charge stays)."""

    @abstractmethod
    async def _provider_status(self, job: Any) -> JobStatus: ...

    @abstractmethod
    async def _provider_result(self, job: Any, handle_document: dict[str, Any]) -> JobResult:
        """Fetch and normalize the result. Use provider-reported timestamps
        for started_at/completed_at where the API offers them; fall back to
        retrieval time only when it does not, with timing.source saying so."""

    @abstractmethod
    def _attach(self, job_id: str) -> Any:
        """Re-attach to a provider job by id (resume after restart)."""

    def _resume_kwargs(self) -> dict[str, Any]:
        """Constructor kwargs needed to rebuild this adapter at resume time
        (e.g. the backend name / device ARN)."""
        return {}

    def _auth_exception_types(self) -> tuple[type[BaseException], ...]:
        """Provider exception types meaning absent/invalid/expired
        credentials. Mapped to CredentialError; never retried."""
        return ()

    def submit_warnings(self, spec: JobSpec) -> list[str]:
        """Non-fatal pre-submit warnings (e.g. device availability windows)."""
        return []

    # ---- the gated lifecycle ----------------------------------------------

    def require_live_layers(self) -> None:
        """Check every opt-in layer, reporting ALL missing ones at once."""
        missing: list[str] = []
        if not self._allow_live:
            missing.append("live execution not enabled (pass --live / allow_live=True)")
        credentials_problem = self._missing_credentials()
        if credentials_problem is not None:
            missing.append(f"credentials: {credentials_problem}")
        if missing:
            raise LiveRefusedError(
                f"refusing live submission to '{self.name}': " + "; ".join(missing)
            )

    async def submit(self, spec: JobSpec) -> JobHandle:
        # Order is load-bearing: layers -> cheap validation -> cost gate ->
        # provider submit. A run that validation would refuse must never
        # reserve budget, so _prevalidate runs strictly before the gate.
        self.require_live_layers()
        prepared = await self._prevalidate(spec)
        estimate = self.estimate_cost(spec)
        ledger_entry_id = check_cost_gate(
            estimate,
            limits=self._limits,
            ledger=self._ledger,
            adapter=self.name,
            device=self._device_name(),
        )
        calibration = self.calibration_snapshot()
        try:
            job_id, submit_metadata = await self._do_submit(spec, prepared)
        except SubmissionError as exc:
            # Definite synchronous rejection: the job never reached the
            # queue, so the charge goes back to the budget.
            self._ledger.record_released(ledger_entry_id, reason=str(exc))
            raise
        except CredentialError as exc:
            self._ledger.record_released(ledger_entry_id, reason=str(exc))
            raise
        except Exception as exc:
            if isinstance(exc, self._auth_exception_types()):
                self._ledger.record_released(ledger_entry_id, reason=str(exc))
                raise CredentialError(
                    f"provider rejected credentials during submit to '{self.name}': {exc}"
                ) from exc
            # Ambiguous (e.g. network timeout mid-submit): the job MAY
            # exist, so the charge conservatively stays committed.
            raise SubmissionError(
                f"ambiguous submit failure on '{self.name}' ({exc}); the job may or "
                "may not have been created — check the provider console. The budget "
                "charge stays committed (conservative)."
            ) from exc
        handle = JobHandle(job_id=job_id, adapter=self.name, submitted_at=datetime.now(tz=UTC))
        self._persist_handle(
            handle,
            spec,
            submit_metadata,
            ledger_entry_id=ledger_entry_id,
            estimate=estimate,
            calibration=calibration,
        )
        return handle

    async def poll(self, handle: JobHandle) -> JobStatus:
        job = self._job_for(handle)
        status: JobStatus = await self._guarded(lambda: self._provider_status(job), "status poll")
        return status

    async def result(self, handle: JobHandle) -> JobResult:
        job = self._job_for(handle)
        document = self.read_handle_document(handle)
        result: JobResult = await self._guarded(
            lambda: self._provider_result(job, document), "result fetch"
        )
        ledger_entry_id = document.get("ledger_entry_id")
        actual_seconds = result.metadata.get("actual_qpu_seconds")
        if isinstance(ledger_entry_id, str) and actual_seconds is not None:
            self._ledger.record_actuals(ledger_entry_id, qpu_seconds=float(actual_seconds))
        # Spend accountability into the result metadata; the runner promotes
        # it to the structural execution.cost block of the sealed QPR. Read
        # from the handle file, never from instance state, so a resumed run
        # keeps its cross-reference.
        cost_info = document.get("cost")
        if isinstance(ledger_entry_id, str) and isinstance(cost_info, dict):
            cost_block: dict[str, Any] = {"ledger_entry_id": ledger_entry_id, **cost_info}
            if actual_seconds is not None:
                cost_block["actual_qpu_seconds"] = float(actual_seconds)
            result.metadata["cost"] = cost_block
        return result

    async def await_result(
        self,
        handle: JobHandle,
        *,
        timeout: float = DEFAULT_LIVE_TIMEOUT_SECONDS,
        poll_interval: float = 0.0,
    ) -> JobResult:
        """Poll with exponential backoff + jitter until terminal.

        poll_interval is accepted for protocol compatibility and ignored:
        live adapters back off instead of polling at a fixed rate."""
        import asyncio

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        delay = self._poll_initial
        while True:
            status = await self.poll(handle)
            if status is JobStatus.COMPLETED:
                return await self.result(handle)
            if status in (JobStatus.FAILED, JobStatus.CANCELLED):
                raise ExecutionError(
                    f"live job {handle.job_id} on '{self.name}' ended {status.value}"
                )
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"live job {handle.job_id} still {status.value} after {timeout:.0f}s. "
                    f"It keeps running provider-side; resume later with: "
                    f"veriqant-bench jobs resume {self.handle_file(handle)}"
                )
            await asyncio.sleep(delay * (1.0 + random.uniform(-POLL_JITTER, POLL_JITTER)))
            delay = min(delay * POLL_FACTOR, self._poll_max)

    # ---- persistence & attachment ------------------------------------------

    def handle_file(self, handle: JobHandle) -> Path:
        return self.handle_file_for_job_id(handle.job_id)

    def handle_file_for_job_id(self, job_id: str) -> Path:
        # Provider job ids may be ARNs (slashes, colons); keep filenames safe
        # and collision-free via a sanitized prefix + content digest.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", job_id)[:60]
        digest = hashlib.sha256(job_id.encode()).hexdigest()[:12]
        return self._jobs_dir / f"{self.name}_{safe}_{digest}.json"

    def read_handle_document(self, handle: JobHandle) -> dict[str, Any]:
        path = self.handle_file(handle)
        if not path.is_file():
            return {}
        document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return document

    def _persist_handle(
        self,
        handle: JobHandle,
        spec: JobSpec,
        submit_metadata: dict[str, Any],
        *,
        ledger_entry_id: str,
        estimate: CostEstimate,
        calibration: CalibrationSnapshot | None,
    ) -> None:
        self._jobs_dir.mkdir(parents=True, exist_ok=True)
        document = {
            "adapter": self.name,
            "adapter_kwargs": self._resume_kwargs(),
            "handle": handle.model_dump(mode="json"),
            "spec": spec.model_dump(mode="json"),
            "submit_metadata": submit_metadata,
            "ledger_entry_id": ledger_entry_id,
            "cost": {
                "estimated_amount": str(estimate.amount),
                "currency": estimate.currency,
                "estimated_qpu_seconds": estimate.qpu_seconds or 0.0,
            },
            "calibration_at_submit": (
                None if calibration is None else calibration.model_dump(mode="json")
            ),
        }
        self.handle_file(handle).write_text(
            json.dumps(document, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _job_for(self, handle: JobHandle) -> Any:
        if handle.adapter != self.name:
            raise UnknownJobError(f"handle for adapter '{handle.adapter}' given to '{self.name}'")
        if handle.job_id not in self._jobs:
            try:
                self._jobs[handle.job_id] = self._attach(handle.job_id)
            except Exception as exc:
                if isinstance(exc, self._auth_exception_types()):
                    raise CredentialError(
                        f"credentials rejected while re-attaching job {handle.job_id} on "
                        f"'{self.name}': {exc}. Re-authenticate, then resume from the "
                        f"handle file: veriqant-bench jobs resume {self.handle_file(handle)}"
                    ) from exc
                raise UnknownJobError(
                    f"cannot re-attach job {handle.job_id} on '{self.name}': {exc}"
                ) from exc
        return self._jobs[handle.job_id]

    async def _guarded(self, operation: Any, what: str) -> Any:
        """Run an idempotent provider read: auth failures become
        CredentialError (no retry — retrying an expired token is noise);
        transient transport errors retry with linear backoff."""
        import asyncio

        last: Exception | None = None
        for attempt in range(TRANSIENT_RETRIES):
            try:
                return await operation()
            except Exception as exc:
                if isinstance(exc, self._auth_exception_types()):
                    raise CredentialError(
                        f"provider credentials rejected during {what} on '{self.name}': "
                        f"{exc}. Re-authenticate, then resume from the handle file "
                        "(veriqant-bench jobs resume <file>)."
                    ) from exc
                if isinstance(exc, _TRANSIENT_ERRORS):
                    last = exc
                    await asyncio.sleep(self._retry_base * (attempt + 1))
                    continue
                raise
        raise ExecutionError(
            f"provider unreachable during {what} after {TRANSIENT_RETRIES} attempts: {last}"
        ) from last
