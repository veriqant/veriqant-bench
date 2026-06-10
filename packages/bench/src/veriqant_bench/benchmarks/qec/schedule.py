"""Memory-experiment schedules: a tiny internal IR for QEC circuits.

One deterministic builder produces a MemorySchedule (gates, measurement
layout, detectors, matching-graph edges, logical observable). Two backends
consume it:

- the product path: schedule -> Qiskit circuit -> OpenQASM 3 -> any adapter;
- the validation path: schedule -> Stim circuit (tests only), so the Aer
  execution chain can be cross-checked against Stim sampling of the *same*
  structure without any possibility of structural divergence.

Detectors are XORs of classical bits (syndrome bits and final data bits),
uniformly for both codes. The matching graph is phenomenological
(single-error space edges + measurement-error time edges, uniform weights);
circuit-level correlated faults such as hook errors are not modeled — see
docs/BENCHMARKS.md for the decoder caveat.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from qiskit import QuantumCircuit, qasm3

OpKind = Literal["h", "x", "cx", "measure", "reset", "barrier"]


class Op(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: OpKind
    qubits: tuple[int, ...]
    clbit: int | None = None
    """Target classical bit for 'measure'."""


class Edge(BaseModel):
    """One matching-graph edge: a single elementary fault mechanism."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    detector_a: int
    detector_b: int | None = None
    """None = boundary edge."""
    flips_observable: bool = False


class MemorySchedule(BaseModel):
    """A complete memory experiment plus its decoding structure."""

    model_config = ConfigDict(extra="forbid")

    code: str
    basis: Literal["z", "x"]
    distance: int
    rounds: int
    num_qubits: int
    num_clbits: int
    ops: list[Op]
    detectors: list[list[int]]
    """Each detector = XOR of these classical-bit indices."""
    observable_clbits: list[int]
    """Observed logical value = parity of these final-measurement bits."""
    edges: list[Edge]
    data_qubits: list[int]
    cx_per_data_qubit_per_round: dict[int, int]
    """For the analytic physical baseline (min over data qubits matters)."""

    def to_qiskit(self) -> QuantumCircuit:
        circuit = QuantumCircuit(self.num_qubits, self.num_clbits)
        for op in self.ops:
            if op.kind == "h":
                circuit.h(op.qubits[0])
            elif op.kind == "x":
                circuit.x(op.qubits[0])
            elif op.kind == "cx":
                circuit.cx(op.qubits[0], op.qubits[1])
            elif op.kind == "measure":
                assert op.clbit is not None
                circuit.measure(op.qubits[0], op.clbit)
            elif op.kind == "reset":
                circuit.reset(op.qubits[0])
            elif op.kind == "barrier":  # pragma: no cover - not emitted yet
                circuit.barrier()
        return circuit

    def to_qasm3(self) -> str:
        return str(qasm3.dumps(self.to_qiskit()))


def repetition_memory(distance: int, rounds: int) -> MemorySchedule:
    """Bit-flip repetition code memory experiment (corrects X errors only).

    Data qubits 0..d-1, ancillas d..2d-2. Each round: CX(data_a, anc_a),
    CX(data_a+1, anc_a), measure + reset every ancilla. Final transversal
    Z-basis measurement of all data qubits. Logical observable: data 0.
    """
    if distance < 3 or distance % 2 == 0:
        raise ValueError("repetition distance must be an odd integer >= 3")
    if rounds < distance:
        raise ValueError(
            f"rounds ({rounds}) must be >= distance ({distance}): fewer "
            "syndrome-extraction rounds than the code distance invalidates "
            "the error-rate claim (ab-lq-2026 criterion 3)"
        )
    n_anc = distance - 1
    ancilla = [distance + a for a in range(n_anc)]
    ops: list[Op] = []
    for round_index in range(rounds):
        for a in range(n_anc):
            ops.append(Op(kind="cx", qubits=(a, ancilla[a])))
            ops.append(Op(kind="cx", qubits=(a + 1, ancilla[a])))
        for a in range(n_anc):
            ops.append(Op(kind="measure", qubits=(ancilla[a],), clbit=round_index * n_anc + a))
            ops.append(Op(kind="reset", qubits=(ancilla[a],)))
    final_base = rounds * n_anc
    for q in range(distance):
        ops.append(Op(kind="measure", qubits=(q,), clbit=final_base + q))

    def syndrome(round_index: int, a: int) -> int:
        return round_index * n_anc + a

    detectors: list[list[int]] = []
    for a in range(n_anc):
        detectors.append([syndrome(0, a)])
    for round_index in range(1, rounds):
        for a in range(n_anc):
            detectors.append([syndrome(round_index, a), syndrome(round_index - 1, a)])
    for a in range(n_anc):
        detectors.append([final_base + a, final_base + a + 1, syndrome(rounds - 1, a)])

    def detector(layer: int, a: int) -> int:
        return layer * n_anc + a

    edges: list[Edge] = []
    for layer in range(rounds + 1):
        # Space edges: a single X on data qubit q flips the adjacent
        # detectors of one layer; boundary data qubits flip just one.
        edges.append(Edge(detector_a=detector(layer, 0), flips_observable=True))
        for q in range(1, distance - 1):
            edges.append(Edge(detector_a=detector(layer, q - 1), detector_b=detector(layer, q)))
        edges.append(Edge(detector_a=detector(layer, n_anc - 1)))
    for layer in range(rounds):
        # Time edges: ancilla measurement errors.
        for a in range(n_anc):
            edges.append(Edge(detector_a=detector(layer, a), detector_b=detector(layer + 1, a)))

    return MemorySchedule(
        code="repetition",
        basis="z",
        distance=distance,
        rounds=rounds,
        num_qubits=2 * distance - 1,
        num_clbits=final_base + distance,
        ops=ops,
        detectors=detectors,
        observable_clbits=[final_base + 0],
        edges=edges,
        data_qubits=list(range(distance)),
        cx_per_data_qubit_per_round={
            q: (1 if q in (0, distance - 1) else 2) for q in range(distance)
        },
    )


# Rotated d=3 surface code ("surface-17"): 9 data qubits on a 3x3 grid,
# 4 Z-type and 4 X-type stabilizers (checkerboard bulk + weight-2 boundary).
#   d0 d1 d2
#   d3 d4 d5
#   d6 d7 d8
SURFACE3_Z_STABILIZERS: list[list[int]] = [[0, 1, 3, 4], [4, 5, 7, 8], [3, 6], [2, 5]]
SURFACE3_X_STABILIZERS: list[list[int]] = [[0, 1], [1, 2, 4, 5], [3, 4, 6, 7], [7, 8]]
SURFACE3_LOGICAL_Z: list[int] = [0, 1, 2]  # top row
SURFACE3_LOGICAL_X: list[int] = [0, 3, 6]  # left column
SURFACE3_DATA = list(range(9))


def surface3_memory(rounds: int, basis: Literal["z", "x"]) -> MemorySchedule:
    """Rotated distance-3 surface code memory experiment (17 qubits).

    basis 'z': prepare |0...0>, decode X errors via Z-stabilizer detectors,
    read out logical Z = parity of the top data row. basis 'x': prepare
    |+...+>, decode Z errors via X-stabilizer detectors, read out logical X
    after a final transversal H. Both stabilizer types are measured every
    round regardless of basis; only the relevant type feeds the decoder.
    CX order within a round is not hook-optimized (documented caveat).
    """
    distance = 3
    if rounds < distance:
        raise ValueError(
            f"rounds ({rounds}) must be >= distance ({distance}) (ab-lq-2026 criterion 3)"
        )
    z_anc = [9 + k for k in range(4)]
    x_anc = [13 + k for k in range(4)]
    n_anc = 8

    ops: list[Op] = []
    if basis == "x":
        for q in SURFACE3_DATA:
            ops.append(Op(kind="h", qubits=(q,)))
    for round_index in range(rounds):
        for k, support in enumerate(SURFACE3_X_STABILIZERS):
            ops.append(Op(kind="h", qubits=(x_anc[k],)))
            for q in support:
                ops.append(Op(kind="cx", qubits=(x_anc[k], q)))
            ops.append(Op(kind="h", qubits=(x_anc[k],)))
        for k, support in enumerate(SURFACE3_Z_STABILIZERS):
            for q in support:
                ops.append(Op(kind="cx", qubits=(q, z_anc[k])))
        for k in range(4):
            ops.append(Op(kind="measure", qubits=(z_anc[k],), clbit=round_index * n_anc + k))
            ops.append(Op(kind="reset", qubits=(z_anc[k],)))
        for k in range(4):
            ops.append(Op(kind="measure", qubits=(x_anc[k],), clbit=round_index * n_anc + 4 + k))
            ops.append(Op(kind="reset", qubits=(x_anc[k],)))
    final_base = rounds * n_anc
    if basis == "x":
        for q in SURFACE3_DATA:
            ops.append(Op(kind="h", qubits=(q,)))
    for q in SURFACE3_DATA:
        ops.append(Op(kind="measure", qubits=(q,), clbit=final_base + q))

    stabilizers = SURFACE3_Z_STABILIZERS if basis == "z" else SURFACE3_X_STABILIZERS
    logical = SURFACE3_LOGICAL_Z if basis == "z" else SURFACE3_LOGICAL_X
    offset = 0 if basis == "z" else 4

    def syndrome(round_index: int, k: int) -> int:
        return round_index * n_anc + offset + k

    detectors: list[list[int]] = []
    for k in range(4):
        detectors.append([syndrome(0, k)])
    for round_index in range(1, rounds):
        for k in range(4):
            detectors.append([syndrome(round_index, k), syndrome(round_index - 1, k)])
    for k, support in enumerate(stabilizers):
        detectors.append([final_base + q for q in support] + [syndrome(rounds - 1, k)])

    def detector(layer: int, k: int) -> int:
        return layer * 4 + k

    edges: list[Edge] = []
    for layer in range(rounds + 1):
        for q in SURFACE3_DATA:
            containing = [k for k, support in enumerate(stabilizers) if q in support]
            flips = q in logical
            if len(containing) == 2:
                edges.append(
                    Edge(
                        detector_a=detector(layer, containing[0]),
                        detector_b=detector(layer, containing[1]),
                        flips_observable=flips,
                    )
                )
            else:
                edges.append(
                    Edge(detector_a=detector(layer, containing[0]), flips_observable=flips)
                )
    for layer in range(rounds):
        for k in range(4):
            edges.append(Edge(detector_a=detector(layer, k), detector_b=detector(layer + 1, k)))

    cx_counts = {
        q: sum(q in support for support in SURFACE3_Z_STABILIZERS + SURFACE3_X_STABILIZERS)
        for q in SURFACE3_DATA
    }
    return MemorySchedule(
        code="surface",
        basis=basis,
        distance=distance,
        rounds=rounds,
        num_qubits=17,
        num_clbits=final_base + 9,
        ops=ops,
        detectors=detectors,
        observable_clbits=[final_base + q for q in logical],
        edges=edges,
        data_qubits=SURFACE3_DATA,
        cx_per_data_qubit_per_round=cx_counts,
    )
