"""Amazon Braket LocalSimulator adapter (pip install veriqore-bench[braket]).

Two boundary conversions, both recorded in the job's transpilation metadata:

1. **Dialect**: Braket ingests its own OpenQASM dialect (no stdgates.inc, no
   classical bit registers, `cnot` instead of `cx`, ...). Circuits are
   converted textually; unsupported constructs fail the job rather than
   silently changing semantics.
2. **Sampling**: LocalSimulator exposes no sampling seed, which would break
   the contract that identical (seed, circuits, shots) reproduce identical
   counts. The adapter therefore requests exact output probabilities
   (shots=0) and draws the counts itself from a seeded multinomial —
   statistically identical to backend sampling, and reproducible.
"""

from __future__ import annotations

import platform
import re
from importlib.metadata import version
from typing import Any

import numpy as np
from braket.devices import LocalSimulator
from braket.ir.openqasm import Program

from .. import __version__
from .errors import SubmissionError
from .local import LocalAdapterBase
from .types import (
    CalibrationSnapshot,
    CostEstimate,
    DeviceCapabilities,
    JobSpec,
)

GATE_RENAMES = {
    "cx": "cnot",
    "ccx": "ccnot",
    "p": "phaseshift",
    "cp": "cphaseshift",
    "sdg": "si",
    "tdg": "ti",
    "sx": "v",
}

_INCLUDE_RE = re.compile(r"^\s*include\s+\".*\"\s*;\s*$")
_BIT_DECL_RE = re.compile(r"^\s*bit(\[\d+\])?\s+\w+\s*;\s*$")
_QUBIT_DECL_RE = re.compile(r"^\s*qubit\[(\d+)\]\s+(\w+)\s*;\s*$")
_MEASURE_ALL_RE = re.compile(r"^\s*\w+\s*=\s*measure\s+(\w+)\s*;\s*$")
_MEASURE_ONE_RE = re.compile(r"^\s*\w+\[(\d+)\]\s*=\s*measure\s+(\w+)\[(\d+)\]\s*;\s*$")


def convert_qasm3_to_braket(source: str) -> tuple[str, int]:
    """Convert an OpenQASM 3 circuit to Braket's dialect.

    Returns (converted source with a probability result pragma, qubit count).
    Raises SubmissionError for constructs the conversion cannot represent
    faithfully — notably partial measurement, since the adapter samples the
    distribution over *all* qubits.
    """
    out_lines: list[str] = []
    num_qubits: int | None = None
    register: str | None = None
    measured: set[int] = set()
    measured_all = False

    for line in source.splitlines():
        if _INCLUDE_RE.match(line) or _BIT_DECL_RE.match(line) or not line.strip():
            continue
        if declaration := _QUBIT_DECL_RE.match(line):
            if num_qubits is not None:
                raise SubmissionError("braket_local supports exactly one qubit register")
            num_qubits = int(declaration.group(1))
            register = declaration.group(2)
            out_lines.append(line)
            continue
        if measure_all := _MEASURE_ALL_RE.match(line):
            if measure_all.group(1) == register:
                measured_all = True
            continue
        if measure_one := _MEASURE_ONE_RE.match(line):
            if measure_one.group(2) == register:
                clbit, qubit = int(measure_one.group(1)), int(measure_one.group(3))
                if clbit != qubit:
                    raise SubmissionError(
                        "braket_local requires the identity measurement map "
                        f"(c[i] = measure q[i]); got c[{clbit}] = measure q[{qubit}]"
                    )
                measured.add(qubit)
            continue
        for old, new in GATE_RENAMES.items():
            line = re.sub(rf"\b{old}\b", new, line)
        out_lines.append(line)

    if num_qubits is None:
        raise SubmissionError("no sized qubit register declaration (e.g. 'qubit[2] q;') found")
    if not measured_all and measured and measured != set(range(num_qubits)):
        raise SubmissionError(
            "braket_local measures all qubits; partial measurement "
            f"of {sorted(measured)} out of {num_qubits} qubits is not supported"
        )

    out_lines.append("#pragma braket result probability")
    return "\n".join(out_lines) + "\n", num_qubits


class BraketLocalAdapter(LocalAdapterBase):
    """Local ideal simulation via Amazon Braket's LocalSimulator."""

    name = "braket_local"
    adapter_version = version("amazon-braket-sdk")

    def __init__(self) -> None:
        super().__init__()
        self._device = LocalSimulator()

    def capabilities(self) -> DeviceCapabilities:
        properties = self._device.properties
        qubit_count = int(properties.paradigm.qubitCount)
        native_gates: list[str] = []
        for action in properties.action.values():
            operations = getattr(action, "supportedOperations", None)
            if operations:
                native_gates = sorted(str(op) for op in operations)
                break
        return DeviceCapabilities(
            device_name=str(self._device.name),
            num_qubits=qubit_count,
            native_gates=native_gates,
            coupling_map=None,  # state-vector simulator is all-to-all
            max_shots=None,
            supports_midcircuit_measurement=False,
            is_simulator=True,
            raw={
                "provider": "amazon-braket-sdk",
                "braket_sdk_version": version("amazon-braket-sdk"),
                "measure_strategy": "all-qubits-via-probability",
            },
        )

    def calibration_snapshot(self) -> CalibrationSnapshot | None:
        return None  # ideal simulation only; no noise support in this adapter

    def estimate_cost(self, spec: JobSpec) -> CostEstimate:
        return CostEstimate.free()

    def _prepare(self, spec: JobSpec) -> list[tuple[Program, int]]:
        prepared: list[tuple[Program, int]] = []
        for index, source in enumerate(spec.circuits):
            try:
                converted, num_qubits = convert_qasm3_to_braket(source)
            except SubmissionError as exc:
                raise SubmissionError(f"circuit {index}: {exc}") from exc
            prepared.append((Program(source=converted), num_qubits))
        return prepared

    def _execute(
        self, prepared: list[tuple[Program, int]], spec: JobSpec
    ) -> tuple[list[dict[str, int]], dict[str, Any]]:
        rng = np.random.default_rng(spec.seed)
        counts: list[dict[str, int]] = []
        for program, num_qubits in prepared:
            result = self._device.run(program, shots=0).result()
            probabilities = np.asarray(result.values[0], dtype=float).clip(min=0.0)
            probabilities /= probabilities.sum()
            draws = rng.multinomial(spec.shots, probabilities)
            circuit_counts: dict[str, int] = {}
            for state_index, count in enumerate(draws):
                if count == 0:
                    continue
                # The probability vector is little-endian in qubit order
                # (q0 = least significant bit of the index), so the binary
                # form of the index is already QPR's bit-0-rightmost string.
                bitstring = format(state_index, f"0{num_qubits}b")
                circuit_counts[bitstring] = int(count)
            counts.append(circuit_counts)
        metadata: dict[str, Any] = {
            "sdk_versions": {
                "amazon-braket-sdk": version("amazon-braket-sdk"),
                "veriqore-bench": __version__,
            },
            "platform": platform.platform(),
            "transpilation": {
                "sdk": "veriqore-bench",
                "sdk_version": __version__,
                "settings": {
                    "conversion": "openqasm3-to-braket-dialect",
                    "gate_renames": GATE_RENAMES,
                    "measure_strategy": "all-qubits-via-probability",
                    "sampling": {"method": "client_multinomial", "seed": spec.seed},
                },
            },
        }
        return counts, metadata
