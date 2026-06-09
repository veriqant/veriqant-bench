# AUTO-GENERATED from packages/schema/schema/qpr-0.1.0.schema.json — do not edit.
# Regenerate with: packages/schema/scripts/generate-pydantic.sh

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel, constr


class Benchmark(BaseModel):
    """
    Identity and parameterization of the benchmark that produced this record.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    id: Annotated[str, Field(pattern="^[a-z0-9][a-z0-9_.-]*$")]
    """
    Machine-readable benchmark identifier, e.g. 'rb_1q', 'rb_2q', 'mirror_circuits', 'quantum_volume', 'clops', 'qec_repetition_memory'.
    """
    display_name: str | None = None
    """
    Human-readable benchmark name.
    """
    suite_version: Annotated[str, Field(pattern="^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)$")]
    """
    Semantic version of the benchmark implementation. Bumped whenever the circuit family, sampling procedure, or estimator changes.
    """
    parameters: dict[str, Any]
    """
    Complete benchmark-specific parameter set (e.g. sequence lengths, qubit pairs, number of random circuits). Together with execution.seed this MUST fully determine the generated circuits.
    """


class Provider(BaseModel):
    """
    The cloud provider / SDK path through which the device was reached.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str, Field(pattern="^[a-z0-9][a-z0-9_.-]*$")]
    """
    Provider identifier, e.g. 'local', 'ibm', 'aws-braket', 'azure-quantum'.
    """
    adapter: Annotated[str, Field(pattern="^[a-z0-9][a-z0-9_.-]*$")]
    """
    veriqore-bench adapter used, e.g. 'aer_simulator', 'braket_local', 'ibm_runtime', 'braket'.
    """
    region: str | None = None
    """
    Provider region or endpoint identifier, when applicable.
    """


class CouplingMapItemItem(RootModel[int]):
    root: Annotated[int, Field(ge=0)]


class CouplingMapItem(RootModel[list[CouplingMapItemItem]]):
    root: Annotated[list[CouplingMapItemItem], Field(max_length=2, min_length=2)]


class Device(BaseModel):
    """
    The target QPU or simulator, including the calibration snapshot in effect at execution time.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    name: str
    """
    Provider-scoped device/backend name, e.g. 'aer_simulator', 'ibm_torino', 'Ankaa-3'.
    """
    version: str | None = None
    """
    Device/backend version string as reported by the provider.
    """
    num_qubits: Annotated[int, Field(ge=1)]
    """
    Number of physical qubits available on the device.
    """
    simulator: bool
    """
    True if the target is a classical simulator rather than physical hardware.
    """
    basis_gates: list[str] | None = None
    """
    Native gate set of the device, if reported.
    """
    coupling_map: list[CouplingMapItem] | None = None
    """
    Directed qubit connectivity as [control, target] pairs, if reported.
    """
    calibration_snapshot_at: AwareDatetime | None = None
    """
    UTC timestamp of the provider calibration data captured below (RFC 3339).
    """
    calibration_snapshot: dict[str, Any] | None = None
    """
    Raw provider calibration data (T1/T2, gate/readout errors, ...) as reported at execution time. Free-form because formats differ per provider; recorded verbatim for auditability.
    """


class Transpilation(BaseModel):
    """
    Exact transpiler configuration. Identical circuit families are submitted to every provider; only this recorded transpilation step may differ, so it must be fully captured.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    sdk: str
    """
    Transpiling SDK, e.g. 'qiskit', 'braket'.
    """
    sdk_version: str
    """
    Exact version of the transpiling SDK.
    """
    optimization_level: Annotated[int | None, Field(ge=0)] = None
    """
    SDK optimization level, when the SDK uses that concept.
    """
    settings: dict[str, Any]
    """
    Complete transpiler settings (initial layout, routing method, scheduling, ...). Free-form per SDK, recorded verbatim.
    """


class Circuit(BaseModel):
    """
    One executed circuit: the abstract (pre-transpilation) definition, the transpiled form actually submitted, and SHA-256 hashes of both for verification.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    index: Annotated[int, Field(ge=0)]
    """
    Zero-based position of this circuit in the submission batch. Raw results refer to circuits by this index.
    """
    name: str | None = None
    """
    Benchmark-assigned circuit label, e.g. 'rb_q0_len16_sample3'.
    """
    qasm3: Annotated[str, Field(min_length=1)]
    """
    OpenQASM 3 source of the abstract circuit, before transpilation.
    """
    qasm3_sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    """
    Lowercase hex SHA-256 of the UTF-8 encoded qasm3 field.
    """
    transpiled_qasm3: Annotated[str | None, Field(min_length=1)] = None
    """
    OpenQASM 3 source of the circuit as actually submitted, after transpilation.
    """
    transpiled_qasm3_sha256: Annotated[str | None, Field(pattern="^[0-9a-f]{64}$")] = None
    """
    Lowercase hex SHA-256 of the UTF-8 encoded transpiled_qasm3 field. Required whenever transpiled_qasm3 is present.
    """
    metadata: dict[str, Any] | None = None
    """
    Benchmark-specific circuit metadata (e.g. sequence length, target qubits).
    """


class RawResult(BaseModel):
    """
    Measurement counts for one circuit.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    circuit_index: Annotated[int, Field(ge=0)]
    """
    Index into the circuits array.
    """
    shots: Annotated[int, Field(ge=1)]
    """
    Shots actually executed for this circuit.
    """
    counts: Annotated[dict[constr(pattern=r"^[01]+$"), int], Field(min_length=1)]
    """
    Map from measured bitstring (MSB-first, e.g. '0101') to occurrence count.
    """


class Qubit(RootModel[int]):
    root: Annotated[int, Field(ge=0)]


class MetricStatistics(BaseModel):
    """
    Mandatory uncertainty quantification for a metric.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    sample_size: Annotated[int, Field(ge=1)]
    """
    Number of independent samples the estimate is based on.
    """
    confidence_level: Annotated[float, Field(gt=0.0, lt=1.0)]
    """
    Confidence level of the interval, e.g. 0.95.
    """
    ci_lower: float
    """
    Lower bound of the confidence interval.
    """
    ci_upper: float
    """
    Upper bound of the confidence interval.
    """
    std_error: Annotated[float | None, Field(ge=0.0)] = None
    """
    Standard error of the estimate, when defined.
    """
    estimator: str
    """
    How the estimate and interval were obtained, e.g. 'exponential_fit_bootstrap', 'binomial_wilson', 'mean_normal'.
    """


class Provenance(BaseModel):
    """
    Software environment that produced this record, pinned to exact versions.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    veriqore_bench_version: str
    """
    Exact veriqore-bench version that produced this record.
    """
    python_version: str
    """
    Python interpreter version, e.g. '3.12.13'.
    """
    platform: str
    """
    OS / architecture string, e.g. 'macOS-15.5-arm64'.
    """
    sdk_versions: Annotated[dict[str, str], Field(min_length=1)]
    """
    Exact versions of every quantum SDK involved, e.g. {"qiskit": "1.4.2"}.
    """


class Signature(BaseModel):
    """
    Detached signature over integrity.content_sha256.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    algorithm: Literal["ed25519"]
    """
    Signature algorithm.
    """
    public_key: str
    """
    Base64-encoded public key of the signer.
    """
    value: str
    """
    Base64-encoded signature over the ASCII hex content_sha256.
    """


class Execution(BaseModel):
    """
    Everything about how the circuits were executed: seed, shots, transpilation, timing. Together with benchmark.parameters this makes the run reproducible.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    seed: Annotated[int, Field(ge=0)]
    """
    Master PRNG seed used for circuit generation and sampling. Required: without it the run is not reproducible.
    """
    shots: Annotated[int, Field(ge=1)]
    """
    Number of shots per circuit.
    """
    live: bool
    """
    True if executed on paid/live hardware, false for local simulators.
    """
    transpilation: Transpilation
    submitted_at: AwareDatetime
    """
    UTC timestamp when the job batch was submitted (RFC 3339).
    """
    completed_at: AwareDatetime | None = None
    """
    UTC timestamp when all results were retrieved (RFC 3339).
    """
    job_ids: list[str] | None = None
    """
    Provider job identifiers, for cross-referencing against provider records.
    """


class Metric(BaseModel):
    """
    One derived metric with mandatory uncertainty quantification.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str, Field(pattern="^[a-z0-9][a-z0-9_.-]*$")]
    """
    Metric identifier, e.g. 'error_per_clifford', 'quantum_volume', 'logical_error_rate'.
    """
    value: float
    """
    Point estimate.
    """
    unit: str | None = None
    """
    Unit of the value, when applicable, e.g. 'probability', 'circuits/s'.
    """
    qubits: list[Qubit] | None = None
    """
    Physical qubit indices this metric pertains to, when qubit-scoped.
    """
    statistics: MetricStatistics


class Integrity(BaseModel):
    """
    Tamper-evidence for the record. content_sha256 is the SHA-256 of the canonical JSON serialization of this record with the entire 'integrity' member removed (see QPR-SPEC §Canonicalization). The optional signature signs that hash.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    content_sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    """
    Lowercase hex SHA-256 over the canonical JSON of the record without its 'integrity' member.
    """
    signature: Signature | None = None


class Results(BaseModel):
    """
    Raw measurement outcomes plus derived metrics. Raw counts are always retained so metrics can be independently re-derived.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    raw: Annotated[list[RawResult], Field(min_length=1)]
    """
    Per-circuit raw measurement counts.
    """
    metrics: Annotated[list[Metric], Field(min_length=1)]
    """
    Derived metrics. Every metric MUST carry sample size and a confidence interval — point estimates without error bars are not valid QPR metrics.
    """
    analysis: dict[str, Any] | None = None
    """
    Benchmark-specific intermediate analysis artifacts (e.g. per-sequence-length survival probabilities, fit residuals).
    """


class QuantumPerformanceRecord(BaseModel):
    """
    QPR v0.1.0 — a self-contained, reproducible record of one benchmark execution against one quantum device or simulator. A QPR carries everything needed to re-run the benchmark bit-for-bit and to independently verify the reported metrics.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    qpr_version: Annotated[str, Field(pattern="^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)$")]
    """
    Semantic version of the QPR schema this record conforms to. Consumers MUST reject records whose major version they do not understand.
    """
    record_id: UUID
    """
    Globally unique identifier (UUID v4) of this record.
    """
    created_at: AwareDatetime
    """
    UTC timestamp at which this record was assembled (RFC 3339).
    """
    benchmark: Benchmark
    provider: Provider
    device: Device
    execution: Execution
    circuits: Annotated[list[Circuit], Field(min_length=1)]
    """
    Every circuit executed for this benchmark, in submission order, with content hashes for verification.
    """
    results: Results
    provenance: Provenance
    integrity: Integrity
