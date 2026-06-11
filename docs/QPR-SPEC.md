# QPR — Quantum Performance Record

**Schema version: 0.3.0** (semver; canonical schema:
[`packages/schema/schema/qpr-0.3.0.schema.json`](../packages/schema/schema/qpr-0.3.0.schema.json))

Version history:

- **0.3.0** — adds optional `execution.timing` (queue vs execution
  wall-clock split, with its source) and `execution.cost` (live spend
  accountability: the gated estimate, its append-only-ledger entry id, and
  provider-reported actual usage). Both are structural so consumers never
  parse conventions out of free-form blobs; monetary amounts are decimal
  strings, never binary floats.
- **0.2.0** — adds optional `results.metrics[].quality`
  (`{reliable, issues[]}`): estimator self-assessment. A benchmark whose fit
  fails its quality thresholds publishes the metric with `reliable=false`
  and machine-readable reasons instead of a clean-looking number.
- **0.1.0** — initial record structure.

A QPR is a self-contained, tamper-evident JSON document describing one
benchmark execution against one quantum device or simulator. Design goals, in
priority order:

1. **Reproducibility** — the record carries everything needed to re-run the
   benchmark bit-for-bit.
2. **Independent verifiability** — anyone can re-derive the circuit hashes and
   the sealed content hash without trusting the producer.
3. **Statistical honesty** — metrics without sample size and confidence
   intervals are structurally invalid.

## Versioning policy

`qpr_version` is semver. Consumers MUST reject records whose **major** version
they do not understand (the reference CLI and the ingestion API both do).
Within a major version, minor/patch additions are backward-compatible: new
optional fields only. Any change to required fields, canonicalization, or
hashing is a major bump.

## Document structure

| Member | Purpose |
| --- | --- |
| `qpr_version` | Schema semver this record conforms to. |
| `record_id` | UUID v4, globally unique. |
| `created_at` | RFC 3339 UTC timestamp of record assembly. |
| `benchmark` | Benchmark identity: `id` (e.g. `rb_1q`, `mirror_circuits`), `suite_version` (semver of the benchmark implementation), and the complete `parameters` object. `parameters` + `execution.seed` MUST fully determine the generated circuits. |
| `provider` | Cloud path: provider `name` (`local`, `ibm`, `aws-braket`, ...), veriqant-bench `adapter`, optional `region`. |
| `device` | Target QPU/simulator: `name`, `num_qubits`, `simulator` flag, optional native `basis_gates`, `coupling_map`, and the raw provider `calibration_snapshot` (+ timestamp) in effect at execution time. |
| `execution` | `seed` (required — no seed, no reproducibility), `shots`, `live` flag, full `transpilation` block (SDK, exact version, optimization level, verbatim settings), submission/completion timestamps, provider `job_ids`, optional `timing` (queue vs execution seconds + source) and `cost` (ledger entry id, gated estimate, provider-reported actuals — live runs only). |
| `circuits[]` | Every executed circuit in submission order: OpenQASM 3 source (`qasm3`) with `qasm3_sha256`, plus the post-transpilation form (`transpiled_qasm3` / `transpiled_qasm3_sha256` — both or neither). `index` must equal the array position. |
| `results.raw[]` | Per-circuit measurement `counts` (bitstring → occurrences, MSB-first) with `shots`; counts must sum to shots. Raw counts are always retained so metrics can be re-derived. |
| `results.metrics[]` | Derived metrics. Each carries `value` plus mandatory `statistics`: `sample_size`, `confidence_level`, `ci_lower`, `ci_upper`, `estimator` (and optional `std_error`), plus optional `quality` (`reliable` flag + `issues[]`) — present whenever the producing benchmark runs fit/estimator diagnostics. |
| `results.analysis` | Optional benchmark-specific intermediate artifacts (fit curves, residuals). |
| `provenance` | Exact `veriqant_bench_version`, `python_version`, `platform`, and per-SDK versions. |
| `integrity` | `content_sha256` seal and optional Ed25519 `signature` (see below). |

## Serialization rules

- UTF-8 JSON. Producers MUST omit absent optional fields entirely — `null` is
  never serialized (the schema accepts absent fields, not null values).
  Practical note for producers using the Python SDK: serialize with
  `veriqant_bench.qpr.dumps_qpr()` / `to_json_dict()`. Pydantic's own
  `model_dump_json()` emits `null` for absent optional fields and produces a
  record that fails schema validation; the verifier recognizes this case and
  says so (`hint.null_fields`).
- Object keys not defined by the schema are rejected (`additionalProperties:
  false`) except inside designated free-form blobs (`benchmark.parameters`,
  `execution.transpilation.settings`, `device.calibration_snapshot`,
  `circuits[].metadata`, `results.analysis`).
- Numbers MUST be finite (no NaN/Infinity). Producers SHOULD keep integers
  within ±2^53 so JavaScript consumers read them losslessly.

## Canonicalization

The canonical form of a JSON value, for hashing purposes:

1. Object keys sorted lexicographically by Unicode code point, recursively.
2. Compact separators: `,` between items, `:` between key and value, no
   whitespace.
3. Non-ASCII characters serialized as raw UTF-8 (not `\uXXXX` escapes).
4. NaN/Infinity are errors.
5. Numbers serialized in Python `json` repr (shortest round-trip form).

> **Known limitation (v0.x):** rule 5 ties canonical float formatting to
> Python's serializer; cross-language re-hashing of records containing
> non-integral floats outside the canonical producer may differ in rare edge
> cases (e.g. `2.0` vs `2`). Adopting RFC 8785 (JCS) number formatting is
> planned for a future schema revision; until then the reference verifier is
> `veriqant-bench verify`.
>
> **Confirmed instance:** because the seal binds exact float bits, records
> whose metrics pass through platform-dependent numerics (scipy fits, BLAS
> statevector math) seal to *different hashes on different platforms* even
> when every displayed value is identical — observed between macOS and
> Linux CI for otherwise-identical benchmark runs. A sealed record remains
> verifiable everywhere; only *re-producing* it bit-for-bit requires the
> same platform. Producers needing platform-portable reproductions (e.g.
> this repo's committed golden-test fixtures, see
> `tests/report/make_fixtures.py`) should quantize float values to a fixed
> number of significant digits *before* sealing.

## Integrity & signing

- `integrity.content_sha256` = SHA-256 (lowercase hex) over the canonical JSON
  of the record **with the entire `integrity` member removed**. Computing the
  hash from the parsed document (not from a re-serialized model) means
  verification is independent of the producer's field ordering or formatting.
- `integrity.signature` (optional) is a detached Ed25519 signature over the
  ASCII hex `content_sha256`, with the raw public key base64-encoded alongside.
  A valid signature proves the record was sealed by the holder of that key;
  key trust/identity is the consumer's policy decision (a Veriqant-operated
  transparency registry is on the roadmap).

## Verification

`veriqant-bench verify <file>` re-derives, in order:

1. `qpr_version` major is supported (else hard stop).
2. Full schema validation (Pydantic models generated from the JSON Schema).
3. `circuits[].qasm3_sha256` (and transpiled hash) match the sources;
   `index` matches array position; transpiled source/hash appear as a pair.
4. `results.raw[].circuit_index` in range; counts sum to shots.
5. Metric sanity: `ci_lower ≤ ci_upper` (error); point estimate inside the
   interval (warning only — some estimators legitimately place the point
   estimate at an interval edge).
6. `integrity.content_sha256` matches the canonical document.
7. Signature, when present, verifies against the embedded key. An unsigned
   record is a warning, not an error.

Exit code is non-zero if any error-severity issue is found.

## Golden example

[`packages/schema/examples/qpr-rb-example.json`](../packages/schema/examples/qpr-rb-example.json)
is generated deterministically by `veriqant_bench.qpr.example.example_record()`
and validated by both the Python and TypeScript test suites. It is the
canonical cross-language fixture; regenerate it with
`packages/schema/scripts/generate-example.sh` after any schema change.

---

*This specification document is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) (see
[LICENSE-SPEC](../LICENSE-SPEC)). The machine-readable schema files and all
code are licensed under Apache-2.0.*
