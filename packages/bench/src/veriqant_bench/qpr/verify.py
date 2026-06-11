"""Independent verification of Quantum Performance Records.

verify_qpr_document() re-derives everything that can be re-derived from the
record itself: structural validity, circuit hashes, the sealed content hash,
internal cross-references, and statistical sanity of reported metrics.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pydantic

from ._generated import QuantumPerformanceRecord
from .canonical import content_sha256, sha256_hex
from .records import SUPPORTED_QPR_MAJOR_VERSIONS, qpr_major_version
from .sign import signing_available, verify_signature

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


@dataclass
class VerificationReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def add(self, severity: Severity, code: str, message: str) -> None:
        self.issues.append(Issue(severity, code, message))


def verify_qpr_document(document: Mapping[str, Any]) -> VerificationReport:
    """Verify a parsed QPR JSON document. Returns a report; report.ok means
    no errors (warnings may still be present)."""
    report = VerificationReport()

    major = qpr_major_version(document)
    if major is None:
        report.add("error", "version.missing", "qpr_version is missing or malformed")
        return report
    if major not in SUPPORTED_QPR_MAJOR_VERSIONS:
        report.add(
            "error",
            "version.unsupported",
            f"QPR major version {major} not supported "
            f"(supported: {sorted(SUPPORTED_QPR_MAJOR_VERSIONS)})",
        )
        return report

    _check_no_nulls(document, report)

    try:
        record = QuantumPerformanceRecord.model_validate(document)
    except pydantic.ValidationError as exc:
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "<root>"
            report.add("error", "schema.invalid", f"{location}: {error['msg']}")
        return report

    _check_circuits(record, report)
    _check_raw_results(record, report)
    _check_metrics(record, report)
    _check_content_hash(document, record, report)
    _check_signature(record, report)
    return report


def verify_qpr_file(path: Path | str) -> VerificationReport:
    """Verify a QPR JSON file."""
    import json

    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report = VerificationReport()
        report.add("error", "file.unreadable", str(exc))
        return report
    if not isinstance(document, dict):
        report = VerificationReport()
        report.add("error", "file.not_object", "top-level JSON value is not an object")
        return report
    return verify_qpr_document(document)


# Free-form blobs where provider payloads may legitimately contain nulls.
# Everywhere else the schema requires absent fields, never null.
_FREE_FORM_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("benchmark", "parameters"),
    ("execution", "transpilation", "settings"),
    ("device", "calibration_snapshot"),
    ("circuits", "*", "metadata"),
    ("results", "analysis"),
)


def _in_free_form(path: tuple[str, ...]) -> bool:
    return any(
        len(path) >= len(prefix)
        and all(part == "*" or part == path[i] for i, part in enumerate(prefix))
        for prefix in _FREE_FORM_PREFIXES
    )


def _check_no_nulls(document: Mapping[str, Any], report: VerificationReport) -> None:
    """JSON nulls outside the free-form blobs violate the spec (absent,
    never null) even though the lenient generated models accept None — and
    they break the seal, since the producer hashed the null-free form. The
    classic cause is serializing with pydantic's model_dump_json(); name
    the fix instead of leaving only a hash mismatch."""
    null_paths: list[str] = []

    def walk(value: Any, path: tuple[str, ...]) -> None:
        if len(null_paths) >= 5 or _in_free_form(path):
            return
        if value is None:
            null_paths.append(".".join(path) or "<root>")
        elif isinstance(value, Mapping):
            for key, item in value.items():
                walk(item, (*path, str(key)))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, (*path, str(index)))

    walk(dict(document), ())
    if null_paths:
        report.add(
            "error",
            "schema.null_values",
            f"JSON null at {', '.join(null_paths)}: the spec requires absent "
            "optional fields, never null. If this record was produced with "
            "pydantic's model_dump_json(), use veriqant_bench.qpr.dumps_qpr() "
            "(or to_json_dict()) instead — see QPR-SPEC §Serialization",
        )


def _check_circuits(record: QuantumPerformanceRecord, report: VerificationReport) -> None:
    for position, circuit in enumerate(record.circuits):
        label = f"circuits[{position}]"
        if circuit.index != position:
            report.add(
                "error",
                "circuit.index_mismatch",
                f"{label}: index {circuit.index} does not match array position {position}",
            )
        if sha256_hex(circuit.qasm3) != circuit.qasm3_sha256:
            report.add(
                "error",
                "circuit.hash_mismatch",
                f"{label}: qasm3_sha256 does not match the qasm3 source",
            )
        # dependentRequired pair: pydantic models can't express it, enforce here.
        if (circuit.transpiled_qasm3 is None) != (circuit.transpiled_qasm3_sha256 is None):
            report.add(
                "error",
                "circuit.transpiled_pair",
                f"{label}: transpiled_qasm3 and transpiled_qasm3_sha256 must appear together",
            )
        elif (
            circuit.transpiled_qasm3 is not None
            and circuit.transpiled_qasm3_sha256 is not None
            and sha256_hex(circuit.transpiled_qasm3) != circuit.transpiled_qasm3_sha256
        ):
            report.add(
                "error",
                "circuit.transpiled_hash_mismatch",
                f"{label}: transpiled_qasm3_sha256 does not match the transpiled source",
            )


def _check_raw_results(record: QuantumPerformanceRecord, report: VerificationReport) -> None:
    circuit_count = len(record.circuits)
    for position, raw in enumerate(record.results.raw):
        label = f"results.raw[{position}]"
        if raw.circuit_index >= circuit_count:
            report.add(
                "error",
                "raw.circuit_index_out_of_range",
                f"{label}: circuit_index {raw.circuit_index} "
                f"out of range for {circuit_count} circuits",
            )
        total = sum(raw.counts.values())
        if total != raw.shots:
            report.add(
                "error",
                "raw.counts_shots_mismatch",
                f"{label}: counts sum to {total} but shots is {raw.shots}",
            )


def _check_metrics(record: QuantumPerformanceRecord, report: VerificationReport) -> None:
    for position, metric in enumerate(record.results.metrics):
        label = f"results.metrics[{position}] ({metric.name})"
        statistics = metric.statistics
        if statistics.ci_lower > statistics.ci_upper:
            report.add(
                "error",
                "metric.ci_inverted",
                f"{label}: ci_lower {statistics.ci_lower} > ci_upper {statistics.ci_upper}",
            )
        elif not statistics.ci_lower <= metric.value <= statistics.ci_upper:
            report.add(
                "warning",
                "metric.value_outside_ci",
                f"{label}: value {metric.value} lies outside "
                f"[{statistics.ci_lower}, {statistics.ci_upper}]",
            )


def _check_content_hash(
    document: Mapping[str, Any],
    record: QuantumPerformanceRecord,
    report: VerificationReport,
) -> None:
    expected = content_sha256(document)
    if record.integrity.content_sha256 != expected:
        report.add(
            "error",
            "integrity.content_hash_mismatch",
            "integrity.content_sha256 does not match the canonical record contents",
        )


def _check_signature(record: QuantumPerformanceRecord, report: VerificationReport) -> None:
    if record.integrity.signature is None:
        report.add("warning", "integrity.unsigned", "record carries no signature")
        return
    if not signing_available():  # pragma: no cover - depends on optional extra
        report.add(
            "warning",
            "integrity.signature_unverified",
            "signature present but the 'signing' extra is not installed",
        )
        return
    if verify_signature(record):
        return
    report.add("error", "integrity.signature_invalid", "Ed25519 signature verification failed")
