"""Qiskit Aer simulator adapter (pip install veriqant-bench[local])."""

from __future__ import annotations

import platform
from datetime import UTC, datetime
from importlib.metadata import version
from typing import Any

from qiskit import QuantumCircuit, qasm3, transpile
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, ReadoutError, depolarizing_error, thermal_relaxation_error

from .errors import SubmissionError
from .local import LocalAdapterBase
from .types import (
    CalibrationSnapshot,
    CostEstimate,
    DeviceCapabilities,
    JobSpec,
    NoiseSpec,
)

# Gate basis the adapter transpiles to when noise is enabled, so noise
# channels attach to every operation actually executed. 'rz' is treated as
# virtual (error-free), mirroring frame-tracking on real hardware.
NOISE_BASIS = ["id", "rz", "sx", "x", "cx"]
_NOISY_1Q_GATES = ["id", "sx", "x"]
_NOISY_2Q_GATES = ["cx"]


def build_noise_model(spec: NoiseSpec) -> NoiseModel:
    """Translate a NoiseSpec into an Aer NoiseModel over NOISE_BASIS."""
    model = NoiseModel(basis_gates=NOISE_BASIS)
    one_qubit = None
    if spec.depolarizing_1q > 0:
        one_qubit = depolarizing_error(spec.depolarizing_1q, 1)
    two_qubit = None
    if spec.depolarizing_2q > 0:
        two_qubit = depolarizing_error(spec.depolarizing_2q, 2)
    if spec.t1_us is not None and spec.t2_us is not None:
        t1_ns = spec.t1_us * 1000.0
        t2_ns = spec.t2_us * 1000.0
        relax_1q = thermal_relaxation_error(t1_ns, t2_ns, spec.gate_time_1q_ns)
        relax_2q_single = thermal_relaxation_error(t1_ns, t2_ns, spec.gate_time_2q_ns)
        relax_2q = relax_2q_single.tensor(relax_2q_single)
        one_qubit = relax_1q if one_qubit is None else one_qubit.compose(relax_1q)
        two_qubit = relax_2q if two_qubit is None else two_qubit.compose(relax_2q)
    if one_qubit is not None:
        model.add_all_qubit_quantum_error(one_qubit, _NOISY_1Q_GATES)
    if two_qubit is not None:
        model.add_all_qubit_quantum_error(two_qubit, _NOISY_2Q_GATES)
    if spec.readout_error_0to1 > 0 or spec.readout_error_1to0 > 0:
        p01 = spec.readout_error_0to1
        p10 = spec.readout_error_1to0
        model.add_all_qubit_readout_error(ReadoutError([[1 - p01, p01], [p10, 1 - p10]]))
    return model


class AerSimulatorAdapter(LocalAdapterBase):
    """Local ideal or noisy simulation via Qiskit Aer."""

    name = "aer_simulator"
    adapter_version = version("qiskit-aer")

    def __init__(self, noise: NoiseSpec | None = None) -> None:
        super().__init__()
        if noise is not None and noise.is_ideal:
            noise = None
        self._noise_spec = noise
        self._backend = (
            AerSimulator() if noise is None else AerSimulator(noise_model=build_noise_model(noise))
        )

    @property
    def noise_spec(self) -> NoiseSpec | None:
        return self._noise_spec

    def capabilities(self) -> DeviceCapabilities:
        backend = self._backend
        return DeviceCapabilities(
            device_name=backend.name,
            device_version=getattr(backend, "backend_version", None),
            num_qubits=backend.num_qubits,
            native_gates=sorted(backend.operation_names),
            coupling_map=None,  # Aer is all-to-all
            max_shots=getattr(backend, "max_shots", None),
            supports_midcircuit_measurement=True,
            is_simulator=True,
            raw={
                "provider": "qiskit-aer",
                "qiskit_version": version("qiskit"),
                "qiskit_aer_version": version("qiskit-aer"),
                "noisy": self._noise_spec is not None,
            },
        )

    def calibration_snapshot(self) -> CalibrationSnapshot | None:
        if self._noise_spec is None:
            return None
        return CalibrationSnapshot(
            source="noise_spec",
            retrieved_at=datetime.now(tz=UTC),
            data={"noise_spec": self._noise_spec.model_dump(mode="json", exclude_none=True)},
        )

    def estimate_cost(self, spec: JobSpec) -> CostEstimate:
        return CostEstimate.free()

    def _prepare(self, spec: JobSpec) -> list[QuantumCircuit]:
        circuits: list[QuantumCircuit] = []
        for index, source in enumerate(spec.circuits):
            try:
                circuits.append(qasm3.loads(source))
            # The importer leaks parser exceptions (e.g. QASM3ParsingError)
            # besides its own error type; the contract is typed errors only.
            except Exception as exc:
                raise SubmissionError(f"circuit {index}: invalid OpenQASM 3: {exc}") from exc
        if self._noise_spec is not None:
            # Pin every executed operation to the noisy basis; no optimization
            # so the executed structure stays a deterministic function of the
            # input circuit.
            circuits = transpile(
                circuits,
                basis_gates=NOISE_BASIS,
                optimization_level=0,
                seed_transpiler=spec.seed,
            )
        return circuits

    def _execute(
        self, prepared: list[QuantumCircuit], spec: JobSpec
    ) -> tuple[list[dict[str, int]], dict[str, Any]]:
        result = self._backend.run(prepared, shots=spec.shots, seed_simulator=spec.seed).result()
        counts: list[dict[str, int]] = []
        for index in range(len(prepared)):
            raw = result.get_counts(index)
            # Qiskit separates registers with spaces; QPR uses one bitstring.
            counts.append({key.replace(" ", ""): int(value) for key, value in raw.items()})
        metadata: dict[str, Any] = {
            "sdk_versions": {"qiskit": version("qiskit"), "qiskit-aer": version("qiskit-aer")},
            "platform": platform.platform(),
            "transpilation": {
                "sdk": "qiskit",
                "sdk_version": version("qiskit"),
                "optimization_level": 0 if self._noise_spec is not None else None,
                "settings": (
                    {"basis_gates": NOISE_BASIS, "seed_transpiler": spec.seed}
                    if self._noise_spec is not None
                    else {"note": "executed as submitted; no transpilation"}
                ),
            },
        }
        return counts, metadata
