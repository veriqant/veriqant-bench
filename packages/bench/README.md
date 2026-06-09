# veriqore-bench

Standardized, reproducible benchmark suites for quantum processors. Runs against
local simulators by default and against live QPUs behind an explicit `--live` flag.
Every run produces a **Quantum Performance Record (QPR)** — a versioned, hash-sealed
JSON document containing everything needed to re-run and independently verify the
benchmark: seed, OpenQASM 3 circuits, transpiler settings, raw counts, SDK versions.

```bash
pip install veriqore-bench            # core (QPR schema + verification)
pip install veriqore-bench[local]     # + Qiskit Aer simulation (no account needed)
pip install veriqore-bench[braket]    # + Amazon Braket (local simulator + live)
pip install veriqore-bench[ibm]       # + IBM Quantum runtime
pip install veriqore-bench[signing]   # + Ed25519 QPR signing
```

## CLI

```bash
veriqore-bench verify results/run.qpr.json   # re-derive hashes, check consistency
veriqore-bench schema                        # print the bundled QPR JSON Schema
veriqore-bench adapters list                 # registered adapters + availability
veriqore-bench adapters probe aer_simulator  # capabilities, calibration, smoke run
veriqore-bench version
```

## Writing an adapter

Implement the `veriqore_bench.adapters.QPUAdapter` protocol, register it under
the `veriqore_bench.adapters` entry-point group, and certify it by subclassing
`veriqore_bench.adapters.conformance.AdapterConformanceSuite` in your tests.

See `docs/QPR-SPEC.md` at the repository root for the record format.
