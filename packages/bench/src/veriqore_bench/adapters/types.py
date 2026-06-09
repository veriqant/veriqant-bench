"""Data types of the QPUAdapter contract.

Counts bitstring convention (everywhere in veriqore-bench, matching the QPR
spec): the rightmost character is qubit/classical bit 0 — i.e. the string
reads c[n-1] ... c[0]. Adapters normalize backend-native orderings to this.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from veriqore_bench.qpr._generated import Device


class JobStatus(StrEnum):
    """Lifecycle states: QUEUED → RUNNING → COMPLETED | FAILED | CANCELLED."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})

VALID_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


class DeviceCapabilities(BaseModel):
    """What a backend reports about itself, in neutral form."""

    model_config = ConfigDict(extra="forbid")

    device_name: str
    device_version: str | None = None
    provider_name: str = Field(default="local", pattern="^[a-z0-9][a-z0-9_.-]*$")
    """QPR provider.name this device is reached through ('local' for
    simulators, 'ibm', 'aws-braket', ... for live paths)."""
    num_qubits: int = Field(ge=1)
    native_gates: list[str]
    coupling_map: list[tuple[int, int]] | None = None
    """Directed [control, target] connectivity; None means all-to-all."""
    max_shots: int | None = Field(default=None, ge=1)
    supports_midcircuit_measurement: bool = False
    is_simulator: bool
    raw: dict[str, Any] = Field(default_factory=dict)
    """Whatever the backend reported, verbatim, for auditability."""

    def to_qpr_device(self, calibration: CalibrationSnapshot | None = None) -> Device:
        """Map onto the QPR 'device' section (validated generated model)."""
        return Device(
            name=self.device_name,
            version=self.device_version,
            num_qubits=self.num_qubits,
            simulator=self.is_simulator,
            basis_gates=self.native_gates,
            coupling_map=(
                None if self.coupling_map is None else [list(edge) for edge in self.coupling_map]
            ),
            calibration_snapshot=None if calibration is None else calibration.data,
            calibration_snapshot_at=None if calibration is None else calibration.retrieved_at,
        )


class CalibrationSnapshot(BaseModel):
    """Verbatim backend calibration data plus retrieval time.

    For simulators this is the configured noise description; ideal simulators
    return None from calibration_snapshot() instead.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    """Where the data came from, e.g. 'noise_spec', 'provider_api'."""
    retrieved_at: AwareDatetime
    data: dict[str, Any]


class CostEstimate(BaseModel):
    """Estimated cost of executing a JobSpec. The live-adapter cost-cap
    guardrail refuses submission when amount exceeds the configured cap."""

    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(ge=0)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    confidence: Literal["exact", "estimate", "unknown"]

    @classmethod
    def free(cls) -> CostEstimate:
        """The simulator case: exactly zero."""
        return cls(amount=Decimal(0), currency="USD", confidence="exact")


class JobSpec(BaseModel):
    """One submission: OpenQASM 3 circuits + shots + master seed."""

    model_config = ConfigDict(extra="forbid")

    circuits: list[str] = Field(min_length=1)
    """OpenQASM 3 sources, executed in order."""
    shots: int = Field(ge=1)
    seed: int = Field(ge=0)
    """Master PRNG seed; identical (seed, circuits, shots) on a simulator
    must reproduce identical counts."""
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobHandle(BaseModel):
    """Opaque reference to a submitted job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    adapter: str
    submitted_at: AwareDatetime


class JobResult(BaseModel):
    """Raw outcome of a completed job."""

    model_config = ConfigDict(extra="forbid")

    counts: list[dict[str, int]] = Field(min_length=1)
    """Per-circuit measurement counts, bitstrings in QPR convention
    (rightmost character = bit 0)."""
    shots: int = Field(ge=1)
    """Shots as reported by the backend."""
    started_at: AwareDatetime
    completed_at: AwareDatetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    """Execution metadata: SDK versions, transpilation/conversion record,
    backend details. Benchmarks fold this into the QPR."""


class NoiseSpec(BaseModel):
    """Compact, adapter-agnostic noise description for simulators.

    Part of the public API: the QEC diagnostics suite drives it, and it is
    recorded verbatim in the QPR calibration snapshot. Adapters translate it
    into their backend's native noise model.
    """

    model_config = ConfigDict(extra="forbid")

    depolarizing_1q: float = Field(default=0.0, ge=0.0, lt=1.0)
    """Depolarizing error probability per 1-qubit gate."""
    depolarizing_2q: float = Field(default=0.0, ge=0.0, lt=1.0)
    """Depolarizing error probability per 2-qubit gate."""
    readout_error_0to1: float = Field(default=0.0, ge=0.0, le=1.0)
    """P(measure 1 | prepared 0)."""
    readout_error_1to0: float = Field(default=0.0, ge=0.0, le=1.0)
    """P(measure 0 | prepared 1)."""
    t1_us: float | None = Field(default=None, gt=0.0)
    """Amplitude-damping time constant, microseconds."""
    t2_us: float | None = Field(default=None, gt=0.0)
    """Dephasing time constant, microseconds. Requires t2 <= 2*t1."""
    gate_time_1q_ns: float = Field(default=50.0, gt=0.0)
    gate_time_2q_ns: float = Field(default=300.0, gt=0.0)

    @model_validator(mode="after")
    def _check_relaxation(self) -> NoiseSpec:
        if (self.t1_us is None) != (self.t2_us is None):
            raise ValueError("t1_us and t2_us must be given together")
        if self.t1_us is not None and self.t2_us is not None and self.t2_us > 2 * self.t1_us:
            raise ValueError("t2_us must not exceed 2 * t1_us")
        return self

    @property
    def is_ideal(self) -> bool:
        """True when every channel is exactly zero / absent."""
        return (
            self.depolarizing_1q == 0.0
            and self.depolarizing_2q == 0.0
            and self.readout_error_0to1 == 0.0
            and self.readout_error_1to0 == 0.0
            and self.t1_us is None
        )
