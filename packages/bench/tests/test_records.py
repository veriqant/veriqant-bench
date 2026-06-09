from __future__ import annotations

import json
from pathlib import Path

import pytest

from veriqore_bench.qpr import (
    QuantumPerformanceRecord,
    UnsupportedQprVersionError,
    content_sha256,
    dump_qpr,
    dumps_qpr,
    load_qpr,
    loads_qpr,
    qpr_major_version,
    seal,
    to_json_dict,
)


def test_file_round_trip(record: QuantumPerformanceRecord, tmp_path: Path) -> None:
    path = tmp_path / "run.qpr.json"
    dump_qpr(record, path)
    assert load_qpr(path) == record


def test_none_fields_are_omitted_not_null(record: QuantumPerformanceRecord) -> None:
    document = to_json_dict(record)
    assert "region" not in document["provider"]
    assert "null" not in dumps_qpr(record)


def test_seal_sets_matching_content_hash(record: QuantumPerformanceRecord) -> None:
    document = to_json_dict(record)
    assert record.integrity.content_sha256 == content_sha256(document)


def test_seal_drops_stale_signature(record: QuantumPerformanceRecord) -> None:
    pytest.importorskip("cryptography")
    from veriqore_bench.qpr import generate_signing_key, sign_qpr

    signed = sign_qpr(record, generate_signing_key())
    assert signed.integrity.signature is not None
    assert seal(signed).integrity.signature is None


def test_loads_rejects_unsupported_major_version(record: QuantumPerformanceRecord) -> None:
    document = to_json_dict(record)
    document["qpr_version"] = "99.0.0"
    with pytest.raises(UnsupportedQprVersionError, match=r"99\.0\.0"):
        loads_qpr(json.dumps(document))


def test_loads_rejects_missing_version() -> None:
    with pytest.raises(UnsupportedQprVersionError):
        loads_qpr("{}")
    with pytest.raises(UnsupportedQprVersionError):
        loads_qpr("[]")


def test_qpr_major_version_parsing() -> None:
    assert qpr_major_version({"qpr_version": "0.1.0"}) == 0
    assert qpr_major_version({"qpr_version": "12.3.4"}) == 12
    assert qpr_major_version({"qpr_version": "abc"}) is None
    assert qpr_major_version({"qpr_version": 1}) is None
    assert qpr_major_version({}) is None
    assert qpr_major_version([]) is None
