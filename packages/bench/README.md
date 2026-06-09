# veriqore-bench

Standardized, reproducible benchmark suites for quantum processors. Runs against
local simulators by default and against live QPUs behind an explicit `--live` flag.
Every run produces a **Quantum Performance Record (QPR)** — a versioned, hash-sealed
JSON document containing everything needed to re-run and independently verify the
benchmark: seed, OpenQASM 3 circuits, transpiler settings, raw counts, SDK versions.

```bash
pip install veriqore-bench            # core (schema, verification, simulators TBD)
pip install veriqore-bench[ibm]       # + IBM Quantum runtime
pip install veriqore-bench[braket]    # + Amazon Braket
pip install veriqore-bench[signing]   # + Ed25519 QPR signing
```

## CLI

```bash
veriqore-bench verify results/run.qpr.json   # re-derive hashes, check consistency
veriqore-bench schema                        # print the bundled QPR JSON Schema
veriqore-bench version
```

See `docs/QPR-SPEC.md` at the repository root for the record format.
