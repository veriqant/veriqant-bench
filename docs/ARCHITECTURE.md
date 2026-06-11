# Architecture

*Status: June 2026 — veriqant-bench 0.1.0 on PyPI, @veriqant/schema 0.1.1 on npm,
QPR schema 0.3.0. All benchmarks are simulator-validated; live hardware adapters
are the next milestone. Update this document when a module lands.*

Veriqant-bench is an independent verification SDK for quantum compute: it runs
standardized, reproducible benchmarks against QPUs and simulators and emits
**sealed Quantum Performance Records (QPRs)** — hash-sealed, optionally signed
JSON documents that anyone can independently re-verify with
`veriqant-bench verify`. No provider grades its own homework.

## 1. The system in one picture

```
                      github.com/veriqant/veriqant-bench
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│  packages/schema ────── THE CONTRACT ───────────────────────────────┐   │
│  │ schema/qpr-0.3.0.schema.json   (single source of truth)          │   │
│  │ ├─ codegen → Pydantic models (Python)                            │   │
│  │ ├─ codegen → TypeScript types + Ajv validator                    │   │
│  │ └─ CI drift check: the two can never silently diverge            │   │
│  │ published as  @veriqant/schema  on npm                           │   │
│  │                                                                  │   │
│  packages/bench  (Python SDK — published as veriqant-bench)         │   │
│  │                                                                  │   │
│  │   adapters/        "the universal socket"                        │   │
│  │   ├─ protocol.py    QPUAdapter (runtime-checkable Protocol)      │   │
│  │   ├─ types.py       JobSpec/JobHandle/JobResult, CostEstimate,   │   │
│  │   │                 DeviceCapabilities, NoiseSpec                │   │
│  │   ├─ lifecycle.py   QUEUED→RUNNING→COMPLETED|FAILED|CANCELLED    │   │
│  │   ├─ errors.py      typed AdapterError hierarchy                 │   │
│  │   ├─ local.py       LocalAdapterBase (in-process job machinery)  │   │
│  │   ├─ aer.py         AerSimulatorAdapter (+NoiseSpec)             │   │
│  │   ├─ braket_local.py BraketLocalAdapter (QASM3→Braket dialect)   │   │
│  │   ├─ registry.py    entry-point discovery (third-party-able)     │   │
│  │   ├─ conformance.py importable contract test suite               │   │
│  │   ├─ ibm.py         IBMRuntimeAdapter (live, open plan, [ibm])   │   │
│  │   └─ braket_aws.py  BraketAdapter (live devices, [braket])       │   │
│  │                                                                  │   │
│  │   live/            "the guardrails" (provider-independent)       │   │
│  │   ├─ limits.py      limits.toml: two budgets, default cap 0      │   │
│  │   ├─ ledger.py      append-only, file-locked spend ledger        │   │
│  │   ├─ gate.py        pre-submit cost gate (no bypass)             │   │
│  │   └─ base.py        LiveAdapterBase: opt-in layers, backoff      │   │
│  │                     polling, resumable handle files              │   │
│  │                                                                  │   │
│  │   benchmarks/      "the measurements"                            │   │
│  │   ├─ base.py + runner.py  generate→execute→analyze→seal          │   │
│  │   ├─ registry.py    entry-point discovery for benchmarks         │   │
│  │   ├─ rb.py          randomized benchmarking 1Q/2Q → EPC          │   │
│  │   ├─ mirror.py      mirror circuits → polarization               │   │
│  │   ├─ qv.py          quantum volume (hard width cap 12)           │   │
│  │   ├─ throughput.py  neutral timing metrics (NOT CLOPS)           │   │
│  │   ├─ stats.py       bootstrap CIs, fit-quality gates             │   │
│  │   └─ qec/           QEC diagnostics                              │   │
│  │       ├─ schedule.py   one schedule IR → two executors           │   │
│  │       ├─ memory.py     repetition d=3/5/7, surface d=3           │   │
│  │       ├─ decoding.py   PyMatching MWPM (identity in QPR)         │   │
│  │       ├─ baseline.py   physical-qubit baseline for breakeven     │   │
│  │       ├─ validation.py Stim oracle (test-only, never an adapter) │   │
│  │       └─ criteria/     pluggable profiles; ab-lq-2026            │   │
│  │                                                                  │   │
│  │   qpr/             "the sealed record"                           │   │
│  │   ├─ _generated.py  models generated from the schema ────────────┘   │
│  │   ├─ canonical.py   sorted-keys canonical JSON → SHA-256             │
│  │   ├─ records.py     assemble + seal                                  │
│  │   ├─ sign.py        optional Ed25519                                 │
│  │   ├─ verify.py      independent re-verification (the auditor)        │
│  │   └─ example.py     reference producer for the golden example        │
│  │                                                                      │
│  │   report.py        sealed QPRs → self-contained HTML (no CDN)        │
│  │   cli.py           run | verify | report | adapters | schema |       │
│  │                    version | jobs resume | limits show               │
│  │                                                                      │
│  scripts/build_demo_site.py → GitHub Pages demo (simulator data,        │
│                               watermarked, deliberately NOT a           │
│                               leaderboard until real hardware data)     │
│  .github/workflows/  ci.yml · release.yml · pages.yml                   │
└─────────────────────────────────────────────────────────────────────────┘
                │ produces                            │ publishes
                ▼                                     ▼
      sealed *.qpr.json files               PyPI: veriqant-bench
      (portable, independently               npm:  @veriqant/schema
       verifiable by anyone)                 (OIDC trusted publishing,
                                              zero stored secrets)
```

Downstream applications (ingestion, dashboards) live outside this repository
and consume only the published artifacts — the npm package and sealed QPR
files. The schema crosses that boundary exclusively as a published package,
never as shared source; CI enforces that nothing in `packages/` ever imports
from an application layer.

## 2. Non-negotiable principles

1. **Reproducibility is the product.** A QPR contains everything needed to
   re-run a benchmark bit-for-bit: master seed, OpenQASM 3 circuits with
   SHA-256 hashes, full transpiler configuration, exact SDK versions, raw
   counts. `veriqant-bench verify` re-derives all hashes and checks internal
   consistency without trusting the producer.
2. **Statistical honesty.** The schema makes uncertainty *structurally
   mandatory*: a metric without sample size and a confidence interval does
   not validate. Estimates that fail their own quality diagnostics are
   published as `quality.reliable=false` with machine-readable issues, never
   as clean-looking numbers. `not_evaluable` is a first-class verdict.
3. **Never claim what cannot be backed.** Hard caps are refused loudly (QV
   width 12), unresolvable values are marked `not_evaluable` (zero observed
   errors ≠ infinite suppression), simulator data is watermarked
   (`simulator_not_comparable_to_hardware`, `simulated_noise_model_not_hardware`)
   and can never rank against hardware. The word "leaderboard" is reserved
   until real hardware data exists.
4. **Neutrality is structural.** All providers run identical circuit families
   pre-transpilation; the transpilation step is fully recorded so differences
   are auditable. We implement others' published norms under their names
   (criteria profile `ab-lq-2026`) and follow published standards rather than
   silently improving them (the QV 2σ rule's documented weakness included).
   The format is open: Apache-2.0 code, CC-BY 4.0 spec.
5. **Simulator-first.** Every benchmark runs end-to-end against local
   simulators (Qiskit Aer, Braket LocalSimulator), so the full test suite is
   free and offline. Live QPU execution will sit behind an explicit `--live`
   flag, credentials, and cost-estimation guardrails with a default spend cap
   of zero.
6. **The default is "no" for anything costly or irreversible.** Cost gates,
   environment-gated release tags, schema major-version rejection.
7. **Schema versioning from day one.** The QPR schema is semver'd; consumers
   (CLI verifier and TypeScript validator alike) reject unknown major
   versions with a clear error instead of misinterpreting records.

## 3. Repository layout & the single source of truth

```
packages/
  schema/     QPR JSON Schema (single source of truth) + generated TS types
              + Ajv validator + golden example + codegen scripts
  bench/      veriqant-bench Python 3.12 SDK (uv + hatchling)
docs/         this file, QPR-SPEC.md, BENCHMARKS.md
scripts/      build_demo_site.py (GitHub Pages demo)
.github/      CI + release + pages workflows
```

The canonical schema lives at `packages/schema/schema/qpr-<version>.schema.json`
(currently 0.3.0). Generated from it (committed, drift-checked in CI):

| Artifact | Generator | Consumer |
| --- | --- | --- |
| `packages/schema/src/generated/qpr.ts` (+ bundled schema copy) | `packages/schema/scripts/generate-ts.mjs` (json-schema-to-typescript) | TypeScript consumers via `@veriqant/schema` |
| `packages/bench/src/veriqant_bench/qpr/_generated.py` (+ bundled schema copy) | `packages/schema/scripts/generate-pydantic.sh` (datamodel-code-generator) | Python SDK |
| `packages/schema/examples/qpr-rb-example.json` | `packages/schema/scripts/generate-example.sh` (reference producer in `veriqant_bench.qpr.example`) | cross-language golden fixture |

The golden example is produced by the Python SDK and validated by the
TypeScript test suite — any divergence between the two language stacks breaks
the build.

## 4. packages/bench (Python SDK)

- Python ≥3.12, packaged with `uv` + `hatchling`. Core deps: `pydantic` v2,
  `click`, `numpy`. Optional extras keep the core install lean: `[local]`
  (Qiskit Aer — no provider account needed; also required by benchmark
  circuit generation), `[ibm]` (qiskit-ibm-runtime), `[braket]` (one extra
  for both LocalSimulator and the future live path: they ship in the same
  SDK), `[qec]` (PyMatching + Stim), `[signing]` (Ed25519 via cryptography).
- `veriqant_bench.qpr` — QPR models, canonical JSON (sorted keys, compact,
  UTF-8; optional fields are omitted, never null) + content hashing, sealing,
  signing, and independent verification.
- `veriqant_bench.adapters` — the neutral execution layer:
  - `QPUAdapter`, a runtime-checkable `typing.Protocol`: `capabilities()`,
    `calibration_snapshot()`, `estimate_cost()` (the future live-path
    cost-cap hook; simulators return exactly zero), async
    `submit()/poll()/result()/await_result()`. Only OpenQASM 3 crosses the
    boundary; counts use the QPR bitstring convention (rightmost character =
    bit 0), normalized from backend-native orderings.
  - Explicit job lifecycle `QUEUED → RUNNING → COMPLETED|FAILED|CANCELLED`
    with a validated transition table; `LocalAdapterBase` runs simulator jobs
    on a worker thread and records every transition (`state_history()`), so
    even instant jobs pass through the states honestly. Queue and execution
    time are recorded separately in result metadata. All failures surface as
    the typed `AdapterError` hierarchy.
  - `AerSimulatorAdapter` (ideal or noisy via the public `NoiseSpec` model,
    recorded verbatim in the QPR calibration snapshot) and
    `BraketLocalAdapter` (textual QASM 3 → Braket-dialect conversion at the
    boundary, recorded in transpilation metadata; counts drawn client-side
    from exact probabilities with the job seed, since LocalSimulator has no
    sampling seed; mid-circuit measurement and dynamic circuits raise
    `UnsupportedCircuitError` instead of returning silently wrong answers).
  - Registry: adapters register through the `veriqant_bench.adapters`
    entry-point group (the built-ins use it too), so third-party adapters
    need no changes here. Missing extras are reported as "unavailable +
    install hint", never as import errors.
  - `veriqant_bench.adapters.conformance.AdapterConformanceSuite` — the
    importable behavioral contract; every adapter (including third-party
    ones) must pass it. In this repo it runs against Aer ideal, Aer noisy,
    and Braket local.
- `veriqant_bench.benchmarks` — the benchmark framework and built-in suites:
  - `Benchmark`: versioned implementations with two pure functions —
    deterministic `generate(params, seed)` (OpenQASM 3 out) and `analyze(...)`
    (metrics out, unit-testable without execution). Registered via the
    `veriqant_bench.benchmarks` entry-point group; any methodology change
    bumps the suite version recorded in the QPR.
  - `run_benchmark()`: the shared driver — generate → submit/await → analyze
    → assemble + seal a QPR, capturing capabilities and the calibration
    snapshot at execution time. `write_verified_qpr()` re-runs the
    independent verifier on the written file as a self-check; a record that
    fails never leaves the tool.
  - `rb` (suite 0.1.0): 1Q/2Q Clifford randomized benchmarking. Exponential
    decay fit → error per Clifford, bootstrap-over-sequences percentile CIs,
    fit-quality gates.
  - `mirror` (suite 0.1.0): randomized mirror circuits (Proctor-style) —
    success probability and guessing-floor-adjusted polarization vs. depth.
  - `qv` (suite 0.1.0): Quantum Volume per Cross et al. — heavy-output sets
    computed at generation time (hard width cap 12: exact simulation is
    exponential), standard 2σ pass rule, failing widths reported.
  - `throughput` (suite 0.1.0): sequential batch round-trip timing —
    deliberately NOT CLOPS (see [BENCHMARKS.md](BENCHMARKS.md)); simulator
    timing is always flagged not comparable to hardware.
  - `qec_repetition` / `qec_surface` (suite 0.1.0, `[qec]` extra): memory
    experiments (mid-circuit measure + reset through the standard adapter
    path), MWPM decoding in `analyze()` with the decoder identity and version
    recorded verbatim, Wilson CIs, Λ suppression with the unresolved-zero
    honesty rule, and pluggable criteria profiles
    (`veriqant_bench.criteria_profiles` entry points; first profile
    `ab-lq-2026`, fully cited). Verdicts from simulated noise always carry
    `simulated_noise_model_not_hardware`. Stim is the closed-loop validation
    oracle (never an adapter): a Stim mirror of the same schedule IR must
    agree with the Aer path through the same decoding pipeline.
  - Methodology details and metric definitions: [BENCHMARKS.md](BENCHMARKS.md).
- `veriqant_bench.report` — `veriqant-bench report <dir> -o report.html`: one
  self-contained HTML file (inline CSS + SVG, zero external requests), every
  input verified first, unreliable metrics visibly badged, deterministic
  given `--generated-at`.
- `veriqant_bench.cli` — `veriqant-bench verify|schema|version|report`,
  `veriqant-bench adapters list|probe`, and
  `veriqant-bench run rb|mirror|qv|throughput|qec --adapter ... --out results/`
  (one sealed, self-verified QPR per run; `--seed` printed when generated,
  `--noise` for scriptable noisy Aer runs, `--criteria` for QEC scorecards).
- Quality gates: pytest with ≥90% coverage enforced on full runs in CI
  (`pytest -n auto --cov=veriqant_bench --cov-fail-under=90`; targeted runs
  skip the gate), 60s per-test timeout (a hang fails loudly), `slow` marker
  on multi-second simulator executions, hypothesis property tests, ruff
  (format + lint), mypy `--strict`.

## 5. packages/schema (TypeScript)

Exports the schema document, generated types, `validateQpr()` / `isQpr()`
(Ajv 2020-12 with formats), `QPR_VERSION`, `SUPPORTED_QPR_MAJOR_VERSIONS`,
and `qprMajorVersion()` for ingestion gating.

## 6. The data flow of one measurement

```
veriqant-bench run rb --adapter aer --seed 42 --out results/
   │
   1. benchmark.generate(params, seed)     → deterministic OpenQASM 3 circuits
   2. adapter.submit / await_result        → raw counts (+ calibration snapshot,
      (lifecycle states, typed errors)        capabilities, timing recorded)
   3. benchmark.analyze(counts)            → metrics with sample size + CI
      (pure function; fit-quality gates       + quality {reliable, issues[]}
       can flag unreliable / not_evaluable)
   4. assemble QPR                         → identity, provenance (SDK & decoder
                                              versions, transpiler config), circuits
                                              with SHA-256 hashes, raw counts, metrics
   5. canonicalize + seal                  → content_sha256 (+ optional Ed25519)
   6. self-verify on write                 → the verifier re-derives everything;
                                              a record that fails never leaves the tool
```

Anyone, anywhere, can later run `veriqant-bench verify file.qpr.json` and
independently confirm: hashes match circuits, seal matches content, CIs are
internally sane. Tamper with one byte and verification fails.

## 7. Trust guarantees per layer (what a green check actually proves)

| Layer | Guarantee | Enforced by |
|---|---|---|
| Schema | Python and TS agree on what a QPR is | codegen drift check in CI |
| Adapter | honest lifecycle, typed errors, seed determinism | importable conformance suite (runs vs Aer ideal, Aer noisy, Braket local) |
| Benchmarks | measured values match known ground truth | closed-loop tests: injected noise λ recovered as EPC; Aer agrees with Stim oracle; ideal QV passes / noisy fails honestly |
| Statistics | no point estimates without error bars; weak fits flagged | structurally mandatory sample size + CI in schema; quality gates (incl. unresolved-Λ rule: zero errors ≠ infinite suppression) |
| Records | tamper-evidence, reproducibility | canonical JSON + SHA-256 seal + verifier; floats quantized in committed fixtures (documented platform caveat) |
| Releases | artifact provably built from this repo | OIDC trusted publishing, environment tag rules, npm provenance, no stored tokens |
| Spending (live adapters) | accidental cost impossible | layered opt-in (--live + credentials + cost gate), caps in config file only, default cap 0, append-only locked ledger |

What a green CI does **not** prove: absence of security vulnerabilities,
correctness against physics beyond the tested regimes, or that the
methodology is the right one — that is what the open spec and independent
review are for.

## 8. Module status

| Module | Scope | Status |
|---|---|---|
| 1 | Monorepo + QPR schema + seal/verify + codegen | ✅ |
| 2 | QPUAdapter protocol + simulator adapters + conformance suite | ✅ |
| 3 | Benchmark framework + RB + mirror circuits | ✅ |
| 4 | Quantum volume + neutral throughput + HTML report | ✅ |
| 5 | QEC diagnostics + criteria profiles (ab-lq-2026) | ✅ |
| Release | PyPI + npm trusted publishing, GitHub Pages demo | ✅ |
| Live | Live adapters (IBM Runtime, Braket) + cost guardrails (QPR 0.3.0) | ✅ code-complete; real-device conformance pending (manual) |

## 9. Development workflow

```bash
# TypeScript
pnpm install && pnpm -r build && pnpm -r test && pnpm lint && pnpm -r typecheck

# Python
cd packages/bench
uv sync && uv run pytest && uv run ruff check . && uv run mypy
# full coverage gate (CI / pre-release):
uv run pytest -n auto --cov=veriqant_bench --cov-fail-under=90

# Regenerate schema artifacts after editing the JSON Schema
pnpm --filter @veriqant/schema generate
packages/schema/scripts/generate-pydantic.sh
packages/schema/scripts/generate-example.sh

# Git hooks
uvx pre-commit install
```

CI (GitHub Actions) runs the Python gates, the TypeScript gates, and a
codegen-drift job that regenerates every derived artifact and fails on any
diff. All of it is simulator-only: CI never needs provider credentials or
paid hardware. Workflow actions are pinned to exact versions. Releases happen
only via `bench-v*` / `schema-v*` tags through environment-gated OIDC trusted
publishing — no stored registry secrets anywhere.

## 10. Conventions

- Conventional commits; all code, comments, and docs in English.
- QPR producers never serialize `null` — optional fields are omitted entirely
  (see [QPR-SPEC.md](QPR-SPEC.md) §Serialization).
- Generated files are committed and never edited by hand; sealed example
  records are regenerated via the SDK, never hand-edited.
