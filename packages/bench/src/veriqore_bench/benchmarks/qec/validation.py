"""Stim mirror of MemorySchedules — the validation oracle.

Builds a Stim circuit with exactly the same gate sequence, measurement
order, and noise placement as the Aer product path, so logical error rates
from both executors can be compared through the *same* detection/decoding
pipeline. Used by closed-loop tests (and nothing else); Stim is an oracle,
not an adapter.

Noise conversion (Aer NoiseSpec -> Stim, exact Pauli-mixture equivalence):
- Aer depolarizing_error(lam, 1) = (1-lam)*rho + lam*I/2, i.e. each of
  X,Y,Z with probability lam/4 -> Stim DEPOLARIZE1(p) with p = 3*lam/4.
- Aer depolarizing_error(lam, 2) -> each non-identity 2Q Pauli lam/16 ->
  Stim DEPOLARIZE2(p) with p = 15*lam/16.
- The Aer adapter transpiles H to rz-sx-rz when noisy, so each H carries
  exactly one 1Q-noisy gate -> one DEPOLARIZE1 after each H here.
- Symmetric readout error -> X_ERROR before M. Asymmetric readout has no
  exact Stim single-parameter analogue; validation specs use symmetric or
  zero readout.
"""

from __future__ import annotations

import numpy as np
import stim

from veriqore_bench.adapters import NoiseSpec

from .schedule import MemorySchedule


def schedule_to_stim(schedule: MemorySchedule, noise: NoiseSpec | None) -> stim.Circuit:
    if noise is not None and noise.readout_error_0to1 != noise.readout_error_1to0:
        raise ValueError(
            "the Stim mirror supports symmetric readout error only; "
            "use p(0->1) == p(1->0) (or zero) in validation noise specs"
        )
    p1 = 0.0 if noise is None else 3.0 * noise.depolarizing_1q / 4.0
    p2 = 0.0 if noise is None else 15.0 * noise.depolarizing_2q / 16.0
    p_readout = 0.0 if noise is None else noise.readout_error_0to1

    circuit = stim.Circuit()
    for op in schedule.ops:
        if op.kind == "h":
            circuit.append("H", list(op.qubits))
            if p1 > 0:
                circuit.append("DEPOLARIZE1", list(op.qubits), p1)
        elif op.kind == "x":  # pragma: no cover - not used by current schedules
            circuit.append("X", list(op.qubits))
            if p1 > 0:
                circuit.append("DEPOLARIZE1", list(op.qubits), p1)
        elif op.kind == "cx":
            circuit.append("CX", list(op.qubits))
            if p2 > 0:
                circuit.append("DEPOLARIZE2", list(op.qubits), p2)
        elif op.kind == "measure":
            if p_readout > 0:
                circuit.append("X_ERROR", list(op.qubits), p_readout)
            circuit.append("M", list(op.qubits))
        elif op.kind == "reset":
            circuit.append("R", list(op.qubits))
    return circuit


def sample_stim_bits(
    schedule: MemorySchedule, noise: NoiseSpec | None, shots: int, seed: int
) -> np.ndarray:
    """(shots, n_clbits) measurement array in clbit order.

    Measure ops are emitted in clbit order by the schedule builders, so
    Stim's measurement-record order matches clbit indices directly; this is
    asserted, not assumed.
    """
    clbit_order = [op.clbit for op in schedule.ops if op.kind == "measure" and op.clbit is not None]
    assert clbit_order == sorted(clbit_order), "schedule measures out of clbit order"
    sampler = schedule_to_stim(schedule, noise).compile_sampler(seed=seed)
    return np.asarray(sampler.sample(shots=shots), dtype=np.uint8)
