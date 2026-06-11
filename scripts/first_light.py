"""First light: the first sealed QPR from a real quantum computer.

Runs minimal 1Q randomized benchmarking on the least-busy IBM open-plan
device and prints the sealed record's path and content hash. Deliberately
tiny and quota-frugal: 6 circuits x 256 shots, estimated well under one
minute of QPU time under the documented heuristic (the exact estimate is
printed and gated before anything is submitted).

This is a manual act, run personally by a human maintainer — never by CI
or any automation. Prerequisites (docs/LIVE.md):
  1. QISKIT_IBM_TOKEN set (or a saved qiskit-ibm-runtime account).
  2. A limits file permitting ~60 QPU-seconds this month, e.g.
     ~/.config/veriqant/limits.toml:
         [budgets]
         monthly_qpu_seconds_cap = 120.0
  3. Patience: open-plan queues run minutes to hours. If interrupted, the
     persisted handle file resumes with `veriqant-bench jobs resume <file>`.

Usage:  python scripts/first_light.py [--out results/]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from veriqant_bench.adapters import CostGateError, JobSpec, LiveRefusedError
from veriqant_bench.adapters.ibm import IBMRuntimeAdapter
from veriqant_bench.benchmarks import run_benchmark, write_verified_qpr
from veriqant_bench.benchmarks.rb import RandomizedBenchmarking, RBParams

PARAMS = RBParams(qubits=[0], lengths=[1, 2, 4], samples_per_length=2)
SHOTS = 256
SEED = 20260611


async def main(out_dir: Path) -> int:
    print("first light: 1Q RB, 6 circuits x 256 shots, IBM open plan")
    adapter = IBMRuntimeAdapter(allow_live=True)
    spec_preview = JobSpec(
        circuits=[circuit.qasm3 for circuit in RandomizedBenchmarking().generate(PARAMS, SEED)],
        shots=SHOTS,
        seed=SEED,
    )
    estimate = adapter.estimate_cost(spec_preview)
    print(f"estimated quota: {estimate.qpu_seconds:.1f} QPU-seconds, ${estimate.amount}")
    print("(deliberately over-bounded; provider-reported usage amends the ledger)")
    try:
        device = adapter.capabilities().device_name
        print(f"target device: {device} (least busy)")
        record = await run_benchmark(
            RandomizedBenchmarking(),
            adapter,
            PARAMS,
            seed=SEED,
            shots=SHOTS,
            timeout=14_400,
        )
    except (CostGateError, LiveRefusedError) as exc:
        print(f"refused before spending anything (as designed): {exc}", file=sys.stderr)
        return 1
    path = write_verified_qpr(record, out_dir)
    print()
    print("the first sealed record from real hardware:")
    print(f"  {path}")
    print(f"  content seal: {record.integrity.content_sha256}")
    print(f"  verify it:    veriqant-bench verify {path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("results"))
    raise SystemExit(asyncio.run(main(parser.parse_args().out)))
