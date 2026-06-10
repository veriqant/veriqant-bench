"""Schedule IR: structure, determinism, validation refusals, QASM3 round trip."""

from __future__ import annotations

from typing import Literal

import pydantic
import pytest
from qiskit import qasm3

from veriqant_bench.benchmarks.qec.memory import RepetitionParams, SurfaceParams
from veriqant_bench.benchmarks.qec.schedule import (
    SURFACE3_X_STABILIZERS,
    SURFACE3_Z_STABILIZERS,
    repetition_memory,
    surface3_memory,
)


def test_repetition_structure() -> None:
    schedule = repetition_memory(3, 5)
    assert schedule.num_qubits == 5  # 3 data + 2 ancilla
    assert schedule.num_clbits == 5 * 2 + 3
    assert len(schedule.detectors) == (5 + 1) * 2
    # d space edges per layer (incl. two boundaries) + (d-1) time edges per gap.
    assert len(schedule.edges) == 6 * 3 + 5 * 2
    assert schedule.observable_clbits == [10]
    assert schedule.cx_per_data_qubit_per_round == {0: 1, 1: 2, 2: 1}


def test_repetition_refuses_invalid_configs() -> None:
    with pytest.raises(ValueError, match="criterion 3"):
        repetition_memory(5, 3)
    with pytest.raises(ValueError, match="odd"):
        repetition_memory(4, 8)


def test_params_enforce_criterion_3_at_validation() -> None:
    with pytest.raises(pydantic.ValidationError, match="refused"):
        RepetitionParams(distances=[3, 5], rounds=3)
    with pytest.raises(pydantic.ValidationError, match="criterion 3"):
        SurfaceParams(rounds=2)


def test_surface_params_refuse_unsupported_distance() -> None:
    with pytest.raises(pydantic.ValidationError, match="distance 3 only"):
        SurfaceParams(distance=5, rounds=5)


def test_surface_stabilizers_commute_and_cover() -> None:
    for x_support in SURFACE3_X_STABILIZERS:
        for z_support in SURFACE3_Z_STABILIZERS:
            overlap = len(set(x_support) & set(z_support))
            assert overlap % 2 == 0, (x_support, z_support)
    assert sorted(q for s in SURFACE3_Z_STABILIZERS for q in s)
    assert set(range(9)) == {q for s in SURFACE3_X_STABILIZERS for q in s} | {
        q for s in SURFACE3_Z_STABILIZERS for q in s
    }


def test_surface_structure() -> None:
    schedule = surface3_memory(3, "z")
    assert schedule.num_qubits == 17
    assert schedule.num_clbits == 3 * 8 + 9
    assert len(schedule.detectors) == (3 + 1) * 4
    assert len(schedule.observable_clbits) == 3


def test_schedules_are_deterministic() -> None:
    assert repetition_memory(5, 7) == repetition_memory(5, 7)
    assert surface3_memory(3, "x") == surface3_memory(3, "x")
    assert repetition_memory(3, 5).to_qasm3() == repetition_memory(3, 5).to_qasm3()


@pytest.mark.parametrize("basis", ["z", "x"])
def test_surface_qasm3_round_trips_through_the_importer(basis: Literal["z", "x"]) -> None:
    """Mid-circuit measurement + reset must survive dumps -> loads, since
    that is exactly what the Aer adapter does."""
    source = surface3_memory(3, basis).to_qasm3()
    loaded = qasm3.loads(source)
    assert loaded.num_qubits == 17
    assert any(instruction.operation.name == "reset" for instruction in loaded.data)


def test_repetition_qasm3_round_trips_through_the_importer() -> None:
    source = repetition_memory(3, 3).to_qasm3()
    loaded = qasm3.loads(source)
    assert loaded.num_qubits == 5
