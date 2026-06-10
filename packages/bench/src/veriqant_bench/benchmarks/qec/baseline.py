"""Physical-qubit baselines for the breakeven criterion.

Breakeven compares the logical error rate against the best constituent
physical qubit. On simulators the baseline is derived analytically from the
NoiseSpec; on hardware it will come from a measured idle-decay
circuit family. The evidence always records which baseline type was used.

Analytic derivation (documented per ab-lq-2026 criterion 1):
Aer's 2-qubit depolarizing channel with parameter lam2 marginalizes on each
participating qubit to a 1-qubit depolarizing channel with the same lam2,
whose bit-flip probability is lam2/2 (the I/2 component flips the qubit
with probability 1/2). The *best* physical data qubit participates in
c_min CX gates per syndrome round, so its per-round flip probability is
    p_phys = 1 - (1 - lam2/2) ** c_min.
Single-qubit depolarizing and readout error are excluded: data qubits idle
through 1Q noise in these schedules, and readout applies once per
experiment, not per round. With no noise model the baseline is undefined
(zero error), making breakeven not_evaluable rather than trivially failed.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from veriqant_bench.adapters import NoiseSpec

from .schedule import MemorySchedule


class PhysicalBaseline(BaseModel):
    """A physical-qubit comparator with its provenance."""

    model_config = ConfigDict(extra="forbid")

    error_per_round: float
    baseline_type: Literal["analytic_noise_spec", "measured_idle_decay"]
    detail: dict[str, float | int | str]


class BaselineProvider(Protocol):
    """Hardware path hook: measured idle-decay baselines plug in
    here without touching the criteria code."""

    def baseline(self, schedule: MemorySchedule) -> PhysicalBaseline | None: ...


def analytic_baseline(noise: NoiseSpec | None, schedule: MemorySchedule) -> PhysicalBaseline | None:
    """Best-physical-qubit per-round flip probability from a NoiseSpec.

    Returns None for ideal simulation (no noise -> no meaningful comparator).
    """
    if noise is None or noise.depolarizing_2q == 0.0:
        return None
    c_min = min(schedule.cx_per_data_qubit_per_round.values())
    flip_per_cx = noise.depolarizing_2q / 2.0
    error_per_round = 1.0 - (1.0 - flip_per_cx) ** c_min
    return PhysicalBaseline(
        error_per_round=float(error_per_round),
        baseline_type="analytic_noise_spec",
        detail={
            "depolarizing_2q": noise.depolarizing_2q,
            "flip_probability_per_cx": flip_per_cx,
            "min_cx_per_data_qubit_per_round": c_min,
            "formula": "1 - (1 - lam2/2)^c_min",
        },
    )


class MeasuredIdleBaseline:
    """Stub for the hardware path: a dedicated idle-decay circuit family
    measured on the same device. Not implemented until live adapters exist."""

    def baseline(self, schedule: MemorySchedule) -> PhysicalBaseline | None:
        raise NotImplementedError(
            "measured idle-decay baselines arrive with the live-hardware adapters"
        )
