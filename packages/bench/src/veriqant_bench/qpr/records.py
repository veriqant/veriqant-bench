"""Loading, serializing, and sealing Quantum Performance Records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._generated import Integrity, QuantumPerformanceRecord
from .canonical import content_sha256

QPR_VERSION = "0.2.0"
SUPPORTED_QPR_MAJOR_VERSIONS = frozenset({0})


class UnsupportedQprVersionError(ValueError):
    """The record declares a QPR major version this library cannot interpret."""


def qpr_major_version(document: Any) -> int | None:
    """Major version from a raw document's qpr_version, or None if absent/malformed."""
    if not isinstance(document, dict):
        return None
    version = document.get("qpr_version")
    if not isinstance(version, str):
        return None
    major, _, _ = version.partition(".")
    return int(major) if major.isdigit() else None


def to_json_dict(record: QuantumPerformanceRecord) -> dict[str, Any]:
    """JSON-compatible dict of a record. None-valued optional fields are omitted,
    matching the schema (which permits absent fields, never null)."""
    result: dict[str, Any] = record.model_dump(mode="json", exclude_none=True)
    return result


def seal(record: QuantumPerformanceRecord) -> QuantumPerformanceRecord:
    """Return a copy with integrity.content_sha256 recomputed from the current
    contents. Any existing signature is dropped: it would no longer match."""
    digest = content_sha256(to_json_dict(record))
    return record.model_copy(update={"integrity": Integrity(content_sha256=digest)})


def dumps_qpr(record: QuantumPerformanceRecord) -> str:
    """Serialize a record to the on-disk JSON form (2-space indent, UTF-8)."""
    return json.dumps(to_json_dict(record), indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def loads_qpr(text: str) -> QuantumPerformanceRecord:
    """Parse a QPR from JSON text, rejecting unsupported major versions."""
    document = json.loads(text)
    major = qpr_major_version(document)
    if major not in SUPPORTED_QPR_MAJOR_VERSIONS:
        declared = document.get("qpr_version") if isinstance(document, dict) else None
        raise UnsupportedQprVersionError(
            f"unsupported QPR version {declared!r}; "
            f"supported major versions: {sorted(SUPPORTED_QPR_MAJOR_VERSIONS)}"
        )
    return QuantumPerformanceRecord.model_validate(document)


def load_qpr(path: Path | str) -> QuantumPerformanceRecord:
    """Load a QPR from a JSON file."""
    return loads_qpr(Path(path).read_text(encoding="utf-8"))


def dump_qpr(record: QuantumPerformanceRecord, path: Path | str) -> None:
    """Write a QPR to a JSON file."""
    Path(path).write_text(dumps_qpr(record), encoding="utf-8")
