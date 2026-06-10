"""A deterministic reference QPR.

Used by the test suites of every Veriqant component (Python and TypeScript)
as the canonical cross-language fixture, and committed to the repository at
packages/schema/examples/. Regenerate with:
packages/schema/scripts/generate-example.sh
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from ._generated import (
    Benchmark,
    Circuit,
    Device,
    Execution,
    Integrity,
    Metric,
    MetricStatistics,
    Provenance,
    Provider,
    QuantumPerformanceRecord,
    RawResult,
    Results,
    Transpilation,
)
from .canonical import sha256_hex
from .records import QPR_VERSION, seal

EXAMPLE_QASM3 = (
    "OPENQASM 3.0;\n"
    'include "stdgates.inc";\n'
    "qubit[1] q;\n"
    "bit[1] c;\n"
    "h q[0];\n"
    "c[0] = measure q[0];\n"
)


def example_record() -> QuantumPerformanceRecord:
    """A small but complete, sealed QPR for a fictional 1-qubit RB run."""
    timestamp = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
    record = QuantumPerformanceRecord(
        qpr_version=QPR_VERSION,
        record_id=UUID("7f1c1b2a-9d4e-4f6a-8b3c-2e5d6a7b8c9d"),
        created_at=timestamp,
        benchmark=Benchmark(
            id="rb_1q",
            display_name="1-qubit randomized benchmarking",
            suite_version="0.1.0",
            parameters={"sequence_lengths": [1, 2, 4, 8], "samples_per_length": 5},
        ),
        provider=Provider(name="local", adapter="aer_simulator"),
        device=Device(name="aer_simulator", num_qubits=1, simulator=True),
        execution=Execution(
            seed=42,
            shots=100,
            live=False,
            transpilation=Transpilation(
                sdk="qiskit",
                sdk_version="1.4.2",
                optimization_level=1,
                settings={"routing_method": "sabre"},
            ),
            submitted_at=timestamp,
            completed_at=timestamp,
        ),
        circuits=[
            Circuit(
                index=0,
                name="rb_q0_len1_sample0",
                qasm3=EXAMPLE_QASM3,
                qasm3_sha256=sha256_hex(EXAMPLE_QASM3),
                metadata={"sequence_length": 1},
            )
        ],
        results=Results(
            raw=[RawResult(circuit_index=0, shots=100, counts={"0": 52, "1": 48})],
            metrics=[
                Metric(
                    name="survival_probability",
                    value=0.52,
                    unit="probability",
                    qubits=[0],
                    statistics=MetricStatistics(
                        sample_size=100,
                        confidence_level=0.95,
                        ci_lower=0.42,
                        ci_upper=0.62,
                        std_error=0.05,
                        estimator="binomial_wilson",
                    ),
                )
            ],
        ),
        provenance=Provenance(
            veriqant_bench_version="0.1.0",
            python_version="3.12.13",
            platform="test",
            sdk_versions={"qiskit": "1.4.2"},
        ),
        integrity=Integrity(content_sha256="0" * 64),
    )
    return seal(record)
