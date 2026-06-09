from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import reseal_document

from veriqore_bench.qpr import (
    QuantumPerformanceRecord,
    dumps_qpr,
    generate_signing_key,
    sign_qpr,
    verify_qpr_document,
    verify_qpr_file,
)


@pytest.fixture
def document(record: QuantumPerformanceRecord) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(dumps_qpr(record))
    return result


def codes(document: dict[str, Any]) -> set[str]:
    return {issue.code for issue in verify_qpr_document(document).issues}


def test_valid_record_passes_with_only_unsigned_warning(document: dict[str, Any]) -> None:
    report = verify_qpr_document(document)
    assert report.ok
    assert codes(document) == {"integrity.unsigned"}


def test_missing_version(document: dict[str, Any]) -> None:
    del document["qpr_version"]
    assert codes(document) == {"version.missing"}


def test_unsupported_major_version(document: dict[str, Any]) -> None:
    document["qpr_version"] = "99.0.0"
    assert codes(document) == {"version.unsupported"}


def test_schema_violation_is_reported_with_location(document: dict[str, Any]) -> None:
    document["benchmark"]["id"] = "Not Valid!"
    report = verify_qpr_document(document)
    assert not report.ok
    assert any(
        issue.code == "schema.invalid" and "benchmark.id" in issue.message
        for issue in report.issues
    )


def test_tampered_qasm_breaks_circuit_and_content_hash(document: dict[str, Any]) -> None:
    document["circuits"][0]["qasm3"] += "\n// tampered"
    found = codes(document)
    assert "circuit.hash_mismatch" in found
    assert "integrity.content_hash_mismatch" in found


def test_circuit_index_must_match_position(document: dict[str, Any]) -> None:
    document["circuits"][0]["index"] = 5
    reseal_document(document)
    assert "circuit.index_mismatch" in codes(document)


def test_transpiled_source_requires_hash(document: dict[str, Any]) -> None:
    document["circuits"][0]["transpiled_qasm3"] = "OPENQASM 3.0;"
    reseal_document(document)
    assert "circuit.transpiled_pair" in codes(document)


def test_transpiled_hash_is_checked(document: dict[str, Any]) -> None:
    document["circuits"][0]["transpiled_qasm3"] = "OPENQASM 3.0;"
    document["circuits"][0]["transpiled_qasm3_sha256"] = "0" * 64
    reseal_document(document)
    assert "circuit.transpiled_hash_mismatch" in codes(document)


def test_raw_circuit_index_out_of_range(document: dict[str, Any]) -> None:
    document["results"]["raw"][0]["circuit_index"] = 7
    reseal_document(document)
    assert "raw.circuit_index_out_of_range" in codes(document)


def test_counts_must_sum_to_shots(document: dict[str, Any]) -> None:
    document["results"]["raw"][0]["counts"]["0"] += 1
    reseal_document(document)
    assert "raw.counts_shots_mismatch" in codes(document)


def test_inverted_confidence_interval(document: dict[str, Any]) -> None:
    statistics = document["results"]["metrics"][0]["statistics"]
    statistics["ci_lower"], statistics["ci_upper"] = 0.9, 0.1
    reseal_document(document)
    assert "metric.ci_inverted" in codes(document)


def test_value_outside_ci_is_warning_only(document: dict[str, Any]) -> None:
    document["results"]["metrics"][0]["value"] = 0.99
    reseal_document(document)
    report = verify_qpr_document(document)
    assert report.ok
    assert any(issue.code == "metric.value_outside_ci" for issue in report.issues)


def test_signed_record_verifies_clean(record: QuantumPerformanceRecord) -> None:
    signed = sign_qpr(record, generate_signing_key())
    document = json.loads(dumps_qpr(signed))
    report = verify_qpr_document(document)
    assert report.ok
    assert report.issues == []


def test_tampered_signature_fails(record: QuantumPerformanceRecord) -> None:
    signed = sign_qpr(record, generate_signing_key())
    document = json.loads(dumps_qpr(signed))
    document["integrity"]["signature"]["value"] = "QUFBQQ=="
    assert "integrity.signature_invalid" in codes(document)


def test_signature_from_wrong_key_fails(record: QuantumPerformanceRecord) -> None:
    signed = sign_qpr(record, generate_signing_key())
    impostor = sign_qpr(record, generate_signing_key())
    document = json.loads(dumps_qpr(signed))
    assert impostor.integrity.signature is not None
    document["integrity"]["signature"]["public_key"] = impostor.integrity.signature.public_key
    assert "integrity.signature_invalid" in codes(document)


def test_verify_file_ok(record: QuantumPerformanceRecord, tmp_path: Path) -> None:
    path = tmp_path / "run.qpr.json"
    path.write_text(dumps_qpr(record), encoding="utf-8")
    assert verify_qpr_file(path).ok


def test_verify_file_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    report = verify_qpr_file(path)
    assert not report.ok
    assert report.issues[0].code == "file.unreadable"


def test_verify_file_not_object(tmp_path: Path) -> None:
    path = tmp_path / "array.json"
    path.write_text("[]", encoding="utf-8")
    report = verify_qpr_file(path)
    assert not report.ok
    assert report.issues[0].code == "file.not_object"


def test_issue_str_format(document: dict[str, Any]) -> None:
    report = verify_qpr_document(document)
    assert str(report.issues[0]) == "[warning] integrity.unsigned: record carries no signature"
