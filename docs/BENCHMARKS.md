# Benchmark methodologies

Every benchmark is versioned independently (`suite_version` in the QPR): any
change to the circuit family, sampling procedure, or estimator bumps it.
Generation is deterministic from `(params, seed)`; analysis is a pure
function of measured counts. Every metric carries sample size and a
confidence interval; failed quality diagnostics surface as
`quality.reliable=false` with machine-readable issues.

## `rb` — Clifford randomized benchmarking (suite 0.1.0)

- **Circuits.** For each sequence length m: m uniformly random Cliffords
  (1Q or 2Q) closed with the inverse of their product, so the ideal outcome
  is |0...0⟩. `samples_per_length` independent random sequences per length.
- **Estimator.** Per-length mean survival probability fit to
  `A·alpha^m + B` (bounded least squares). Error per Clifford:
  `EPC = (d−1)/d · (1−alpha)`, `d = 2^n`.
- **Uncertainty.** Nonparametric bootstrap over sequences: resample
  per-sequence survivals within each length, refit, percentile CI.
- **Quality gates.** Convergence, `R² ≥ 0.9`, amplitude collapse away from
  the ceiling (`A < 0.1` and `A+B < 0.95`), decay rate pinned at the lower
  bound, and a minimum number of successful bootstrap refits.

## `mirror` — randomized mirror circuits (suite 0.1.0)

In the spirit of Proctor et al., *Measuring the capabilities of quantum
computers*, Nat. Phys. 18, 75–79 (2022).

- **Circuits.** `depth` random layers (random 1Q Cliffords per qubit +
  CX on randomly paired qubits at `two_qubit_density`), a central random
  Pauli layer, then the exact inverse of the random half. The whole circuit
  is Clifford; the single ideal outcome is computed classically at
  generation time and recorded in circuit metadata.
- **Metrics (per depth, bootstrap CIs over sampled circuits).**
  `success_probability.depth_D` — frequency of the target bitstring;
  `mirror_polarization.depth_D` — success rescaled for the random-guessing
  floor: `(p − 1/2^n) / (1 − 1/2^n)`.

## `qv` — Quantum Volume (suite 0.1.0)

Standard methodology per Cross, Bishop, Sheldon, Nation, Gambetta,
*Validating quantum computers using randomized model circuits*,
Phys. Rev. A 100, 032328 (2019).

- **Circuits.** For width m: depth-m circuits, each layer a uniformly
  random qubit permutation with Haar-random SU(4) on each pair, synthesized
  to the rz/sx/x/cx basis deterministically at generation time.
- **Heavy outputs.** Computed at generation time by exact statevector
  simulation; the heavy set (ideal probability strictly above the median)
  is recorded in circuit metadata. Exact simulation is exponential in
  width: widths above 12 are refused outright (`HARD_WIDTH_LIMIT`), and the
  practical default tops out at 6.
- **Pass criterion (per width).** The standard one-sided 2σ rule
  (~97.7% confidence): pass iff
  `h̄ − 2·sqrt(h̄(1−h̄)/(n_circuits·shots)) > 2/3`, with the aggregate
  binomial σ exactly as defined by Cross et al. (Their formula treats all
  shots as one binomial sample and does not model circuit-to-circuit
  variance — we follow the standard rather than silently "improving" it;
  the per-width bootstrap CI on `heavy_output_probability` carries the
  circuit-to-circuit spread.)
- **Result.** `quantum_volume = 2^m` for the largest passing width
  (1 if none pass, flagged `qv.no_width_passed`). Failing widths are
  reported as `qv_pass.width_M = 0`, never omitted. A failure below a
  passing width is flagged `qv.non_monotonic_pass_pattern`.
- **Circuit count honesty.** Defaults (50/width) are cheap-but-honest for
  CI. Publication-grade claims require **≥ 100 circuits per width**;
  smaller counts carry `qv.circuit_count_below_publication_grade`, and
  counts below 30 are marked unreliable
  (`qv.insufficient_circuits_for_confidence`).

## `throughput` — sequential batch throughput (suite 0.1.0)

**This is not CLOPS, on purpose.** CLOPS (Wack et al., *Quality, Speed, and
Scale: three key attributes to measure the performance of near-term quantum
computers*, arXiv:2110.14108) is IBM's defined speed metric with a specific
protocol: parameterized template circuits whose parameters are updated at
runtime between layers, capturing the classical-quantum interaction loop.
Veriqore's `throughput` benchmark measures a related but different thing —
sequential client-observed round-trips of *static* circuit batches, with no
runtime parameter updates — so publishing it under the CLOPS name would be
exactly the kind of methodological blur this product exists to call out.
Numbers from the two protocols are not comparable.

- **Protocol.** A seeded template batch of B mirror circuits at fixed
  width/depth is executed R times sequentially at S shots, each batch with
  a derived seed (master + batch index). Wall-clock submit→result is
  measured per batch on the client. Where the adapter reports a
  queue/execution split it is recorded verbatim per batch; where it
  cannot, that inability is recorded (`adapter_timing.available = false`).
- **Metrics** (median over batches, bootstrap percentile CIs; median/IQR
  also recorded in `analysis`):
  - `job_round_trip_seconds` — wall-clock seconds per batch round trip.
  - `sustained_shots_per_second` — `(B·S) / round_trip` per batch.
  - `sequential_layers_per_second` — `(B·L) / round_trip` per batch, where
    `L = 2·depth + 1` is the template's layer count.
- **Honesty rule.** On simulators these numbers measure the harness and
  host machine, not a QPU. Every metric is then published with
  `quality.reliable=false` and issue
  `timing.simulator_not_comparable_to_hardware`, so no dashboard can rank a
  laptop against a QPU. The intended consumer is the live-hardware
  adapters.

## `qec_repetition` / `qec_surface` — QEC memory diagnostics (suite 0.1.0)

Requires `pip install veriqore-bench[local,qec]` (PyMatching for decoding;
Stim as the validation oracle — Stim is our calculator for checking
ourselves, never an execution target).

- **Codes.** Bit-flip repetition code (distances 3,5,7 by default —
  corrects **one error species only**; its scorecard demonstrates the
  machinery, a bit-flip code is not a full logical qubit) and the rotated
  distance-3 surface code (17 qubits, memory in both |0⟩ and |+⟩ bases).
  Surface d=5 (49 qubits) is **out of the Aer product path** and exists
  only in Stim-based validation tests; the parameter validator refuses it.
- **Circuits.** OpenQASM 3 with mid-circuit measurement + reset through the
  standard adapter path, deterministic from `(params, seed)`. Configs with
  `rounds < distance` are refused at parameter validation (criterion 3
  below), not silently analyzed.
- **Decoder in the loop.** MWPM (PyMatching) over a phenomenological
  matching graph with uniform weights; circuit-level correlated faults
  (hook errors) are not modeled, and the CX order is not hook-optimized.
  **A logical error rate is a property of (device × decoder)**: the decoder
  name, version, and graph construction are recorded verbatim in every QEC
  QPR (`analysis.decoder`), because longitudinal comparability breaks if
  the decoder changes invisibly.
- **Metrics.** Per-distance (or per-basis) logical error per round with
  Wilson CIs; Λ suppression per distance step with parametric-bootstrap CIs
  (zero errors at both adjacent distances → the step is marked
  `resolved=false` and Λ is reported as unresolved, never as 0 or ∞);
  `post_selection_fraction` with explicit accounting (shots submitted ==
  shots analyzed — discarding is impossible by construction in our
  pipeline).
- **Physical baseline (breakeven comparator).** On simulators, derived
  analytically from the NoiseSpec: Aer's 2Q depolarizing channel
  marginalizes to a 1Q depolarizing channel (bit-flip probability λ₂/2 per
  CX), so the best data qubit's per-round error is
  `1 − (1 − λ₂/2)^c_min`. Hardware will substitute a measured idle-decay
  baseline (interface stubbed); evidence records which baseline type was
  used. Ideal simulation → no baseline → breakeven `not_evaluable`.
- **Closed-loop validation.** A Stim mirror of the *same schedule IR*
  (exact Pauli-mixture noise conversion: `p_stim = 3λ₁/4` for 1Q,
  `15λ₂/16` for 2Q) is sampled and decoded through the same pipeline; Aer
  and Stim rates must agree statistically. Suppression is asserted in both
  directions (sub-threshold Λ>1 passes, super-threshold fails).
- **Shot counts.** CI-grade defaults (2000 shots) keep runs in seconds and
  resolve Λ only for moderate noise. Publication-grade QEC claims need
  orders of magnitude more shots.

## Criteria profiles

A criteria profile is a named, versioned, **cited** set of logical-qubit
criteria evaluated over QEC evidence — Veriqore executes others' published
norms, it does not define its own. Profiles register through the
`veriqore_bench.criteria_profiles` entry-point group. Verdicts are
`pass` / `fail` / `not_evaluable`; `not_evaluable` is a first-class outcome
with a reason and evidence — an honest "this experiment cannot answer that"
beats a forced verdict.

### Profile `ab-lq-2026` v1.0.0

Citation: Alice & Bob, *"Defining the Logical Qubit: Five Criteria to
Benchmark Logical Qubit Claims"*, June 2026.

| # | Criterion | Rule |
| --- | --- | --- |
| 1 | `breakeven` | `ci_upper(logical error/round)` at the largest distance < best physical qubit baseline |
| 2 | `scalable_parameters` | every Λ step `ci_lower > 1`; unresolved steps → `not_evaluable` |
| 3 | `sufficient_cycles` | syndrome rounds ≥ code distance; failure makes 1, 2, and 5 `not_evaluable` (their evidence is invalid) |
| 4 | `all_runs_counted` | `post_selection_fraction == 0.0` with explicit shot accounting |
| 5 | `utility_timescales` | ≥ 10⁶ sustained rounds at error budget 10⁻⁹; memory experiments at current scales report `not_evaluable` |

**Simulator-vs-hardware distinction (non-negotiable):** verdicts derived
from a simulated noise model always carry the machine-readable issue
`simulated_noise_model_not_hardware` and `quality.reliable=false`, and the
HTML report watermarks the scorecard. A NoiseSpec scorecard demonstrates
the machinery; it is never confusable with a hardware claim.
