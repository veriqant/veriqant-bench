from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from veriqore_bench import __version__
from veriqore_bench.cli import main
from veriqore_bench.qpr import QPR_VERSION, QuantumPerformanceRecord, dump_qpr, dumps_qpr


def test_verify_valid_file(record: QuantumPerformanceRecord, tmp_path: Path) -> None:
    path = tmp_path / "run.qpr.json"
    dump_qpr(record, path)
    result = CliRunner().invoke(main, ["verify", str(path)])
    assert result.exit_code == 0
    assert "OK" in result.output
    assert "integrity.unsigned" in result.output


def test_verify_tampered_file_fails(record: QuantumPerformanceRecord, tmp_path: Path) -> None:
    document = json.loads(dumps_qpr(record))
    document["circuits"][0]["qasm3"] += " "
    path = tmp_path / "tampered.qpr.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    result = CliRunner().invoke(main, ["verify", str(path)])
    assert result.exit_code == 1
    assert "circuit.hash_mismatch" in result.output


def test_verify_missing_file_fails() -> None:
    result = CliRunner().invoke(main, ["verify", "does-not-exist.json"])
    assert result.exit_code != 0


def test_schema_prints_bundled_schema() -> None:
    result = CliRunner().invoke(main, ["schema"])
    assert result.exit_code == 0
    schema = json.loads(result.output)
    assert schema["title"] == "QuantumPerformanceRecord"
    assert QPR_VERSION in schema["$id"]


def test_version_command() -> None:
    result = CliRunner().invoke(main, ["version"])
    assert result.exit_code == 0
    assert f"veriqore-bench {__version__}" in result.output
    assert f"qpr-schema {QPR_VERSION}" in result.output
