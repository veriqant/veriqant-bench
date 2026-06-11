# Changelog

All notable changes to veriqant-bench. The QPR schema is versioned
separately (see the compatibility table in README.md).

## @veriqant/schema 0.2.0 (npm) — unreleased

QPR schema 0.2.0 → 0.3.0 (additive minor): optional `execution.timing`
(queue vs execution wall-clock split, with its source) and optional
`execution.cost` (live spend accountability: the gated estimate, its
append-only-ledger entry id, provider-reported actual usage; monetary
amounts as decimal strings). All existing 0.2.0 records remain valid.

## @veriqant/schema 0.1.1 (npm) — 2026-06-10

No functional changes relative to 0.1.0 (which was published manually).
First release through the automated trusted-publishing pipeline; release
workflow now runs npm >= 11.5 (Node 24) as trusted publishing requires.

## 0.1.0 — 2026-06-10

First public release. Simulator-validated; live hardware adapters are the
next milestone.

### QPR — Quantum Performance Record (schema 0.2.0)

- Versioned, sealed JSON records: seed, OpenQASM 3 circuits with SHA-256
  hashes, full transpiler config, raw counts, provenance, content seal,
  optional Ed25519 signatures.
- Mandatory uncertainty: metrics without sample size and confidence
  intervals do not validate. Optional `quality` field
  (`reliable` + machine-readable issues) so failed diagnostics are
  published flagged, never hidden.
- Independent verification: `veriqant-bench verify` re-derives circuit
  hashes and the content seal, checks cross-references and statistical
  sanity. Generated Pydantic models and TypeScript types from one schema;
  cross-language golden fixture.

### Adapters

- `QPUAdapter` protocol (runtime-checkable), explicit job lifecycle with
  typed errors, entry-point registry, importable conformance suite.
- `aer_simulator` (ideal or noisy via the public `NoiseSpec`) and
  `braket_local` (QASM 3 dialect conversion at the boundary, recorded;
  deterministic counts via exact probabilities + seeded sampling;
  unsupported constructs fail loudly).

### Benchmarks

- `rb`: 1Q/2Q Clifford randomized benchmarking with bootstrap CIs and
  fit-quality gates; closed-loop validated against analytic noise.
- `mirror`: randomized mirror circuits (success probability and
  polarization vs. depth).
- `qv`: Quantum Volume per the published standard, with circuit-count
  honesty tiers and never-omitted failures.
- `throughput`: sequential batch timing under its own metric names
  (explicitly not CLOPS); simulator timing machine-flagged as not
  comparable to hardware.
- `qec_repetition` / `qec_surface`: memory experiments with MWPM decoding
  (decoder identity recorded verbatim), Wilson CIs, Λ suppression with
  unresolved-zero honesty, exact post-selection accounting, and pluggable
  criteria profiles — first profile `ab-lq-2026` (Alice & Bob, June 2026),
  with simulated-noise verdicts machine-flagged. Closed-loop validated
  against Stim.

### Reporting

- `veriqant-bench report`: one self-contained HTML file (inline CSS/SVG,
  zero external requests), inputs verified first, unreliable metrics
  visibly badged, criteria scorecards watermarked when simulator-derived.
