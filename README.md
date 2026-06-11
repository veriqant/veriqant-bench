# veriqant-bench

[![CI](https://github.com/veriqant/veriqant-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/veriqant/veriqant-bench/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/veriqant-bench)](https://pypi.org/project/veriqant-bench/)
[![npm](https://img.shields.io/npm/v/%40veriqant%2Fschema)](https://www.npmjs.com/package/@veriqant/schema)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Independent, reproducible QPU benchmarking with sealed records.**

Every benchmark run emits a **Quantum Performance Record (QPR)**: a single
JSON document carrying everything needed to re-run the experiment
bit-for-bit (seed, OpenQASM 3 circuits with hashes, transpiler config, raw
counts, SDK versions) and to verify it independently — schema validation,
re-derived circuit hashes, a tamper-evident content seal, and optional
Ed25519 signatures. Every metric carries sample size and confidence
intervals; estimates that fail their own quality diagnostics are published
as flagged-unreliable, never as clean-looking numbers.

## Quickstart

```bash
pip install 'veriqant-bench[local]'                      # Aer simulators, no account needed
veriqant-bench run rb --adapter aer --qubits 0 --out results/
veriqant-bench verify results/*.qpr.json                 # independent re-verification
veriqant-bench report results/ -o report.html            # self-contained HTML report
```

## What's in the box

| Benchmark | What it measures |
| --- | --- |
| `rb` | 1Q/2Q Clifford randomized benchmarking → error per Clifford, bootstrap CIs, fit-quality gates |
| `mirror` | Randomized mirror circuits (Proctor et al.) → success probability & polarization vs. depth |
| `qv` | Quantum Volume (Cross et al., standard 2σ rule) → heavy-output probability, honest pass/fail per width |
| `throughput` | Sequential batch round-trip timing (deliberately **not** CLOPS — see [docs/BENCHMARKS.md](docs/BENCHMARKS.md)) |
| `qec_repetition` / `qec_surface` | QEC memory experiments with MWPM decoding, Λ suppression, and **criteria scorecards** |

**Criteria profiles** evaluate logical-qubit claims against published,
cited norms — pluggable and versioned. The first profile, `ab-lq-2026`,
implements Alice & Bob, *"Defining the Logical Qubit: Five Criteria to
Benchmark Logical Qubit Claims"* (June 2026): breakeven, scalable
parameters, sufficient QEC cycles, no post-selection, and utility
timescales. Verdicts are `pass` / `fail` / `not_evaluable` — an honest
"this experiment cannot answer that" is a first-class outcome.

```bash
pip install 'veriqant-bench[local,qec]'
veriqant-bench run qec --code repetition --distances 3,5,7 --rounds 7 \
    --criteria ab-lq-2026 --noise noise.json --out results/
```

## Status — read this before citing numbers

- **Simulator-validated today.** Every benchmark runs end-to-end against
  local simulators (Qiskit Aer, Braket LocalSimulator) and is closed-loop
  validated — RB against analytic noise injection, QEC against Stim as an
  independent oracle. Live hardware adapters are the next milestone.
- **Simulated results are machine-flagged.** Timing metrics from simulators
  carry `timing.simulator_not_comparable_to_hardware`; criteria verdicts
  from simulated noise carry `simulated_noise_model_not_hardware`. No
  dashboard built on QPRs can accidentally present a simulation as a
  hardware claim.
- **Independence.** Veriqant is not affiliated with, funded by, or
  endorsed by any quantum hardware vendor. Methodology corrections are
  welcome — open an issue.

## Compatibility

| veriqant-bench (PyPI) | QPR schema | @veriqant/schema (npm) |
| --- | --- | --- |
| main (unreleased) | 0.3.0 | 0.2.x |
| 0.1.x | 0.2.0 | 0.1.x |

Consumers must reject QPR major versions they do not understand; both the
CLI verifier and the TypeScript validator do.

## Documentation

- [docs/QPR-SPEC.md](docs/QPR-SPEC.md) — the record format: field-by-field
  spec, canonicalization, sealing, signing, versioning policy (CC BY 4.0).
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md) — methodology per benchmark,
  with citations and documented caveats.
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, tests, and the adapter
  conformance suite (the contract for third-party adapters).

## License

Code: [Apache-2.0](LICENSE). QPR specification document:
[CC BY 4.0](LICENSE-SPEC).
