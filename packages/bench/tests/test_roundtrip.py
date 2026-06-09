"""Property-based round-trip tests over the full QPR model space.

Strategy-generated records are internally consistent (correct hashes, counts
summing to shots, in-range indices), so every generated record must both
round-trip bit-for-bit and pass full verification.
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from veriqore_bench.qpr import (
    QPR_VERSION,
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
    content_sha256,
    dumps_qpr,
    loads_qpr,
    seal,
    sha256_hex,
    to_json_dict,
    verify_qpr_document,
)

IDENTIFIER = st.from_regex(r"[a-z0-9][a-z0-9_.-]{0,15}", fullmatch=True)
SEMVER = st.from_regex(r"(0|[1-9][0-9]?)\.(0|[1-9][0-9]?)\.(0|[1-9][0-9]?)", fullmatch=True)
FINITE_FLOAT = st.floats(allow_nan=False, allow_infinity=False, width=64)
UTC_DATETIME = st.datetimes(timezones=st.just(UTC))
TEXT = st.text(max_size=30)

# Free-form JSON parameter blobs. Nulls are deliberately excluded: the QPR
# spec requires absent fields instead of null (see QPR-SPEC §Serialization).
JSON_SCALAR = st.booleans() | st.integers(min_value=-(2**53), max_value=2**53) | FINITE_FLOAT | TEXT
JSON_VALUE = st.recursive(
    JSON_SCALAR,
    lambda children: (
        st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=10), children, max_size=3)
    ),
    max_leaves=8,
)
PARAMETERS = st.dictionaries(st.text(min_size=1, max_size=10), JSON_VALUE, max_size=4)


@st.composite
def metric_statistics(draw: st.DrawFn) -> MetricStatistics:
    bounds = sorted([draw(FINITE_FLOAT), draw(FINITE_FLOAT)])
    return MetricStatistics(
        sample_size=draw(st.integers(min_value=1, max_value=10**6)),
        confidence_level=draw(
            st.floats(min_value=0.5, max_value=0.999, exclude_min=False, exclude_max=False)
        ),
        ci_lower=bounds[0],
        ci_upper=bounds[1],
        std_error=draw(st.none() | st.floats(min_value=0, max_value=1e6)),
        estimator=draw(st.sampled_from(["binomial_wilson", "mean_normal", "bootstrap"])),
    )


@st.composite
def metrics(draw: st.DrawFn) -> Metric:
    return Metric(
        name=draw(IDENTIFIER),
        value=draw(FINITE_FLOAT),
        unit=draw(st.none() | st.sampled_from(["probability", "circuits/s", "s"])),
        qubits=draw(st.none() | st.lists(st.integers(min_value=0, max_value=127), max_size=4)),
        statistics=draw(metric_statistics()),
    )


@st.composite
def circuits(draw: st.DrawFn, index: int) -> Circuit:
    qasm = draw(st.text(min_size=1, max_size=200))
    transpiled = draw(st.none() | st.text(min_size=1, max_size=200))
    return Circuit(
        index=index,
        name=draw(st.none() | TEXT.filter(bool)),
        qasm3=qasm,
        qasm3_sha256=sha256_hex(qasm),
        transpiled_qasm3=transpiled,
        transpiled_qasm3_sha256=None if transpiled is None else sha256_hex(transpiled),
        metadata=draw(st.none() | PARAMETERS),
    )


@st.composite
def raw_results(draw: st.DrawFn, circuit_count: int) -> RawResult:
    counts = draw(
        st.dictionaries(
            st.from_regex(r"[01]{1,8}", fullmatch=True),
            st.integers(min_value=0, max_value=10**6),
            min_size=1,
            max_size=8,
        ).filter(lambda c: sum(c.values()) >= 1)
    )
    return RawResult(
        circuit_index=draw(st.integers(min_value=0, max_value=circuit_count - 1)),
        shots=sum(counts.values()),
        counts=counts,
    )


@st.composite
def qpr_records(draw: st.DrawFn) -> QuantumPerformanceRecord:
    circuit_count = draw(st.integers(min_value=1, max_value=3))
    record = QuantumPerformanceRecord(
        qpr_version=QPR_VERSION,
        record_id=draw(st.uuids(version=4)),
        created_at=draw(UTC_DATETIME),
        benchmark=Benchmark(
            id=draw(IDENTIFIER),
            display_name=draw(st.none() | TEXT),
            suite_version=draw(SEMVER),
            parameters=draw(PARAMETERS),
        ),
        provider=Provider(
            name=draw(IDENTIFIER),
            adapter=draw(IDENTIFIER),
            region=draw(st.none() | TEXT),
        ),
        device=Device(
            name=draw(TEXT.filter(bool)),
            version=draw(st.none() | TEXT),
            num_qubits=draw(st.integers(min_value=1, max_value=1000)),
            simulator=draw(st.booleans()),
            basis_gates=draw(st.none() | st.lists(st.sampled_from(["cz", "rz", "sx", "x"]))),
            coupling_map=draw(
                st.none()
                | st.lists(
                    st.tuples(
                        st.integers(min_value=0, max_value=99),
                        st.integers(min_value=0, max_value=99),
                    ).map(list),
                    max_size=4,
                )
            ),
            calibration_snapshot_at=draw(st.none() | UTC_DATETIME),
            calibration_snapshot=draw(st.none() | PARAMETERS),
        ),
        execution=Execution(
            seed=draw(st.integers(min_value=0, max_value=2**63 - 1)),
            shots=draw(st.integers(min_value=1, max_value=10**6)),
            live=draw(st.booleans()),
            transpilation=Transpilation(
                sdk=draw(st.sampled_from(["qiskit", "braket"])),
                sdk_version=draw(SEMVER),
                optimization_level=draw(st.none() | st.integers(min_value=0, max_value=3)),
                settings=draw(PARAMETERS),
            ),
            submitted_at=draw(UTC_DATETIME),
            completed_at=draw(st.none() | UTC_DATETIME),
            job_ids=draw(st.none() | st.lists(TEXT.filter(bool), max_size=3)),
        ),
        circuits=[draw(circuits(index)) for index in range(circuit_count)],
        results=Results(
            raw=draw(st.lists(raw_results(circuit_count), min_size=1, max_size=4)),
            metrics=draw(st.lists(metrics(), min_size=1, max_size=4)),
            analysis=draw(st.none() | PARAMETERS),
        ),
        provenance=Provenance(
            veriqore_bench_version=draw(SEMVER),
            python_version=draw(SEMVER),
            platform=draw(TEXT.filter(bool)),
            sdk_versions=draw(st.dictionaries(IDENTIFIER, SEMVER, min_size=1, max_size=3)),
        ),
        integrity=Integrity(content_sha256="0" * 64),
    )
    return seal(record)


def _reorder_keys(value: Any) -> Any:
    """Recursively reverse dict key order, preserving content."""
    if isinstance(value, dict):
        return {key: _reorder_keys(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reorder_keys(item) for item in value]
    return value


@settings(max_examples=75, deadline=None)
@given(record=qpr_records())
def test_serialization_round_trip_is_lossless(record: QuantumPerformanceRecord) -> None:
    restored = loads_qpr(dumps_qpr(record))
    assert restored == record
    # A second trip must be byte-identical: serialization is deterministic.
    assert dumps_qpr(restored) == dumps_qpr(record)


@settings(max_examples=75, deadline=None)
@given(record=qpr_records())
def test_generated_records_pass_full_verification(record: QuantumPerformanceRecord) -> None:
    document = json.loads(dumps_qpr(record))
    report = verify_qpr_document(document)
    errors = [issue for issue in report.issues if issue.severity == "error"]
    assert report.ok, f"unexpected errors: {errors}"


@settings(max_examples=50, deadline=None)
@given(record=qpr_records())
def test_content_hash_is_key_order_independent(record: QuantumPerformanceRecord) -> None:
    document = to_json_dict(record)
    assert content_sha256(_reorder_keys(document)) == content_sha256(document)


@settings(max_examples=50, deadline=None)
@given(record=qpr_records())
def test_seal_is_idempotent(record: QuantumPerformanceRecord) -> None:
    assert seal(record) == record
