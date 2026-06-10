"""BraketLocalAdapter specifics: the QASM 3 → Braket dialect conversion and
its faithful-or-fail boundaries."""

from __future__ import annotations

import pytest

from veriqant_bench.adapters import JobSpec, SubmissionError, UnsupportedCircuitError
from veriqant_bench.adapters.braket_local import BraketLocalAdapter, convert_qasm3_to_braket
from veriqant_bench.adapters.conformance import BELL_2Q


def test_conversion_renames_gates_and_strips_classical_scaffolding() -> None:
    converted, num_qubits = convert_qasm3_to_braket(BELL_2Q)
    assert num_qubits == 2
    assert "cnot q[0], q[1];" in converted
    assert "include" not in converted
    assert "\nbit[" not in converted
    assert "measure" not in converted
    assert converted.rstrip().endswith("#pragma braket result probability")


def test_conversion_requires_a_sized_qubit_register() -> None:
    with pytest.raises(SubmissionError, match="qubit register"):
        convert_qasm3_to_braket("OPENQASM 3.0;\nh q[0];\n")


def test_conversion_rejects_multiple_registers() -> None:
    source = "OPENQASM 3.0;\nqubit[2] a;\nqubit[2] b;\n"
    with pytest.raises(SubmissionError, match="exactly one"):
        convert_qasm3_to_braket(source)


def test_conversion_rejects_partial_measurement() -> None:
    source = "OPENQASM 3.0;\nqubit[3] q;\nbit[1] c;\nh q[0];\nc[0] = measure q[0];\n"
    with pytest.raises(SubmissionError, match="partial measurement"):
        convert_qasm3_to_braket(source)


def test_conversion_rejects_crossed_measurement_map() -> None:
    source = "OPENQASM 3.0;\nqubit[2] q;\nbit[2] c;\nc[1] = measure q[0];\nc[0] = measure q[1];\n"
    with pytest.raises(SubmissionError, match="identity measurement map"):
        convert_qasm3_to_braket(source)


def test_mid_circuit_measurement_raises_unsupported() -> None:
    source = (
        "OPENQASM 3.0;\nqubit[2] q;\nbit[2] c;\n"
        "c[0] = measure q[0];\nh q[1];\nc[1] = measure q[1];\n"
    )
    with pytest.raises(UnsupportedCircuitError, match="mid-circuit measurement"):
        convert_qasm3_to_braket(source)


def test_dynamic_circuit_constructs_raise_unsupported() -> None:
    reset_source = "OPENQASM 3.0;\nqubit[1] q;\nh q[0];\nreset q[0];\n"
    with pytest.raises(UnsupportedCircuitError, match="dynamic-circuit"):
        convert_qasm3_to_braket(reset_source)

    feedback_source = "OPENQASM 3.0;\nqubit[1] q;\nbit[1] c;\nif (c[0] == 1) x q[0];\n"
    with pytest.raises(UnsupportedCircuitError, match="dynamic-circuit"):
        convert_qasm3_to_braket(feedback_source)


def test_measure_all_shorthand_is_accepted() -> None:
    source = "OPENQASM 3.0;\nqubit[2] q;\nbit[2] c;\nh q[0];\nc = measure q;\n"
    converted, num_qubits = convert_qasm3_to_braket(source)
    assert num_qubits == 2
    assert "measure" not in converted


async def test_submit_wraps_conversion_errors_with_circuit_index() -> None:
    adapter = BraketLocalAdapter()
    spec = JobSpec(circuits=[BELL_2Q, "OPENQASM 3.0;\nh q[0];\n"], shots=10, seed=0)
    with pytest.raises(SubmissionError, match="circuit 1"):
        await adapter.submit(spec)


async def test_result_metadata_records_conversion_and_sampling() -> None:
    adapter = BraketLocalAdapter()
    spec = JobSpec(circuits=[BELL_2Q], shots=100, seed=77)
    result = await adapter.await_result(await adapter.submit(spec))
    settings = result.metadata["transpilation"]["settings"]
    assert settings["conversion"] == "openqasm3-to-braket-dialect"
    assert settings["sampling"] == {"method": "client_multinomial", "seed": 77}
    assert sum(result.counts[0].values()) == 100
    assert set(result.counts[0]) <= {"00", "11"}  # ideal Bell state
