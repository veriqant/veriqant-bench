/* eslint-disable */
/**
 * AUTO-GENERATED from schema/qpr-0.2.0.schema.json — do not edit by hand.
 * Regenerate with: pnpm --filter @veriqore/schema generate
 */

/**
 * QPR v0.2.0 — a self-contained, reproducible record of one benchmark execution against one quantum device or simulator. A QPR carries everything needed to re-run the benchmark bit-for-bit and to independently verify the reported metrics.
 */
export interface QuantumPerformanceRecord {
  /**
   * Semantic version of the QPR schema this record conforms to. Consumers MUST reject records whose major version they do not understand.
   */
  qpr_version: string;
  /**
   * Globally unique identifier (UUID v4) of this record.
   */
  record_id: string;
  /**
   * UTC timestamp at which this record was assembled (RFC 3339).
   */
  created_at: string;
  benchmark: Benchmark;
  provider: Provider;
  device: Device;
  execution: Execution;
  /**
   * Every circuit executed for this benchmark, in submission order, with content hashes for verification.
   *
   * @minItems 1
   */
  circuits: [Circuit, ...Circuit[]];
  results: Results;
  provenance: Provenance;
  integrity: Integrity;
}
/**
 * Identity and parameterization of the benchmark that produced this record.
 */
export interface Benchmark {
  /**
   * Machine-readable benchmark identifier, e.g. 'rb_1q', 'rb_2q', 'mirror_circuits', 'quantum_volume', 'clops', 'qec_repetition_memory'.
   */
  id: string;
  /**
   * Human-readable benchmark name.
   */
  display_name?: string;
  /**
   * Semantic version of the benchmark implementation. Bumped whenever the circuit family, sampling procedure, or estimator changes.
   */
  suite_version: string;
  /**
   * Complete benchmark-specific parameter set (e.g. sequence lengths, qubit pairs, number of random circuits). Together with execution.seed this MUST fully determine the generated circuits.
   */
  parameters: {
    [k: string]: unknown | undefined;
  };
}
/**
 * The cloud provider / SDK path through which the device was reached.
 */
export interface Provider {
  /**
   * Provider identifier, e.g. 'local', 'ibm', 'aws-braket', 'azure-quantum'.
   */
  name: string;
  /**
   * veriqore-bench adapter used, e.g. 'aer_simulator', 'braket_local', 'ibm_runtime', 'braket'.
   */
  adapter: string;
  /**
   * Provider region or endpoint identifier, when applicable.
   */
  region?: string;
}
/**
 * The target QPU or simulator, including the calibration snapshot in effect at execution time.
 */
export interface Device {
  /**
   * Provider-scoped device/backend name, e.g. 'aer_simulator', 'ibm_torino', 'Ankaa-3'.
   */
  name: string;
  /**
   * Device/backend version string as reported by the provider.
   */
  version?: string;
  /**
   * Number of physical qubits available on the device.
   */
  num_qubits: number;
  /**
   * True if the target is a classical simulator rather than physical hardware.
   */
  simulator: boolean;
  /**
   * Native gate set of the device, if reported.
   */
  basis_gates?: string[];
  /**
   * Directed qubit connectivity as [control, target] pairs, if reported.
   */
  coupling_map?: [number, number][];
  /**
   * UTC timestamp of the provider calibration data captured below (RFC 3339).
   */
  calibration_snapshot_at?: string;
  /**
   * Raw provider calibration data (T1/T2, gate/readout errors, ...) as reported at execution time. Free-form because formats differ per provider; recorded verbatim for auditability.
   */
  calibration_snapshot?: {
    [k: string]: unknown | undefined;
  };
}
/**
 * Everything about how the circuits were executed: seed, shots, transpilation, timing. Together with benchmark.parameters this makes the run reproducible.
 */
export interface Execution {
  /**
   * Master PRNG seed used for circuit generation and sampling. Required: without it the run is not reproducible.
   */
  seed: number;
  /**
   * Number of shots per circuit.
   */
  shots: number;
  /**
   * True if executed on paid/live hardware, false for local simulators.
   */
  live: boolean;
  transpilation: Transpilation;
  /**
   * UTC timestamp when the job batch was submitted (RFC 3339).
   */
  submitted_at: string;
  /**
   * UTC timestamp when all results were retrieved (RFC 3339).
   */
  completed_at?: string;
  /**
   * Provider job identifiers, for cross-referencing against provider records.
   */
  job_ids?: string[];
}
/**
 * Exact transpiler configuration. Identical circuit families are submitted to every provider; only this recorded transpilation step may differ, so it must be fully captured.
 */
export interface Transpilation {
  /**
   * Transpiling SDK, e.g. 'qiskit', 'braket'.
   */
  sdk: string;
  /**
   * Exact version of the transpiling SDK.
   */
  sdk_version: string;
  /**
   * SDK optimization level, when the SDK uses that concept.
   */
  optimization_level?: number;
  /**
   * Complete transpiler settings (initial layout, routing method, scheduling, ...). Free-form per SDK, recorded verbatim.
   */
  settings: {
    [k: string]: unknown | undefined;
  };
}
/**
 * One executed circuit: the abstract (pre-transpilation) definition, the transpiled form actually submitted, and SHA-256 hashes of both for verification.
 */
export interface Circuit {
  /**
   * Zero-based position of this circuit in the submission batch. Raw results refer to circuits by this index.
   */
  index: number;
  /**
   * Benchmark-assigned circuit label, e.g. 'rb_q0_len16_sample3'.
   */
  name?: string;
  /**
   * OpenQASM 3 source of the abstract circuit, before transpilation.
   */
  qasm3: string;
  /**
   * Lowercase hex SHA-256 of the UTF-8 encoded qasm3 field.
   */
  qasm3_sha256: string;
  /**
   * OpenQASM 3 source of the circuit as actually submitted, after transpilation.
   */
  transpiled_qasm3?: string;
  /**
   * Lowercase hex SHA-256 of the UTF-8 encoded transpiled_qasm3 field. Required whenever transpiled_qasm3 is present.
   */
  transpiled_qasm3_sha256?: string;
  /**
   * Benchmark-specific circuit metadata (e.g. sequence length, target qubits).
   */
  metadata?: {
    [k: string]: unknown | undefined;
  };
}
/**
 * Raw measurement outcomes plus derived metrics. Raw counts are always retained so metrics can be independently re-derived.
 */
export interface Results {
  /**
   * Per-circuit raw measurement counts.
   *
   * @minItems 1
   */
  raw: [RawResult, ...RawResult[]];
  /**
   * Derived metrics. Every metric MUST carry sample size and a confidence interval — point estimates without error bars are not valid QPR metrics.
   *
   * @minItems 1
   */
  metrics: [Metric, ...Metric[]];
  /**
   * Benchmark-specific intermediate analysis artifacts (e.g. per-sequence-length survival probabilities, fit residuals).
   */
  analysis?: {
    [k: string]: unknown | undefined;
  };
}
/**
 * Measurement counts for one circuit.
 */
export interface RawResult {
  /**
   * Index into the circuits array.
   */
  circuit_index: number;
  /**
   * Shots actually executed for this circuit.
   */
  shots: number;
  /**
   * Map from measured bitstring (MSB-first, e.g. '0101') to occurrence count.
   */
  counts: {
    [k: string]: number | undefined;
  };
}
/**
 * One derived metric with mandatory uncertainty quantification.
 */
export interface Metric {
  /**
   * Metric identifier, e.g. 'error_per_clifford', 'quantum_volume', 'logical_error_rate'.
   */
  name: string;
  /**
   * Point estimate.
   */
  value: number;
  /**
   * Unit of the value, when applicable, e.g. 'probability', 'circuits/s'.
   */
  unit?: string;
  /**
   * Physical qubit indices this metric pertains to, when qubit-scoped.
   */
  qubits?: number[];
  statistics: MetricStatistics;
  quality?: MetricQuality;
}
/**
 * Mandatory uncertainty quantification for a metric.
 */
export interface MetricStatistics {
  /**
   * Number of independent samples the estimate is based on.
   */
  sample_size: number;
  /**
   * Confidence level of the interval, e.g. 0.95.
   */
  confidence_level: number;
  /**
   * Lower bound of the confidence interval.
   */
  ci_lower: number;
  /**
   * Upper bound of the confidence interval.
   */
  ci_upper: number;
  /**
   * Standard error of the estimate, when defined.
   */
  std_error?: number;
  /**
   * How the estimate and interval were obtained, e.g. 'exponential_fit_bootstrap', 'binomial_wilson', 'mean_normal'.
   */
  estimator: string;
}
/**
 * Estimator self-assessment. Present whenever the producing benchmark runs quality diagnostics; a metric whose fit or estimator failed its quality thresholds MUST be published with reliable=false and the reasons listed, never as a clean-looking number.
 */
export interface MetricQuality {
  /**
   * False when the estimate failed the benchmark's quality thresholds.
   */
  reliable: boolean;
  /**
   * Machine-readable reasons, e.g. 'fit.r_squared_below_threshold'.
   */
  issues?: string[];
}
/**
 * Software environment that produced this record, pinned to exact versions.
 */
export interface Provenance {
  /**
   * Exact veriqore-bench version that produced this record.
   */
  veriqore_bench_version: string;
  /**
   * Python interpreter version, e.g. '3.12.13'.
   */
  python_version: string;
  /**
   * OS / architecture string, e.g. 'macOS-15.5-arm64'.
   */
  platform: string;
  /**
   * Exact versions of every quantum SDK involved, e.g. {"qiskit": "1.4.2"}.
   */
  sdk_versions: {
    [k: string]: string | undefined;
  };
}
/**
 * Tamper-evidence for the record. content_sha256 is the SHA-256 of the canonical JSON serialization of this record with the entire 'integrity' member removed (see QPR-SPEC §Canonicalization). The optional signature signs that hash.
 */
export interface Integrity {
  /**
   * Lowercase hex SHA-256 over the canonical JSON of the record without its 'integrity' member.
   */
  content_sha256: string;
  signature?: Signature;
}
/**
 * Detached signature over integrity.content_sha256.
 */
export interface Signature {
  /**
   * Signature algorithm.
   */
  algorithm: 'ed25519';
  /**
   * Base64-encoded public key of the signer.
   */
  public_key: string;
  /**
   * Base64-encoded signature over the ASCII hex content_sha256.
   */
  value: string;
}
