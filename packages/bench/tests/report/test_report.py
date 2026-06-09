"""Report generator: golden-file determinism, refusal of unverifiable
records, and visible unreliability badges."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import (
    FIXED_AT,
    mirror_record,
    qec_record,
    qv_record,
    rb_record,
    throughput_record,
)

from veriqore_bench.cli import main
from veriqore_bench.qpr import QuantumPerformanceRecord, dump_qpr
from veriqore_bench.report import (
    ReportInputError,
    collect_qpr_files,
    load_verified_records,
    render_report,
)

GOLDEN_PATH = Path(__file__).parent / "golden_report.html"


@pytest.fixture
async def fixture_records() -> list[QuantumPerformanceRecord]:
    return [
        await rb_record(),
        await mirror_record(),
        await qv_record(),
        await qec_record(),
        throughput_record(),
    ]


@pytest.fixture
def qpr_dir(tmp_path: Path, fixture_records: list[QuantumPerformanceRecord]) -> Path:
    directory = tmp_path / "records"
    directory.mkdir()
    for record in fixture_records:
        dump_qpr(record, directory / f"{record.benchmark.id}.qpr.json")
    return directory


def render(records: list[QuantumPerformanceRecord], qpr_dir: Path) -> str:
    loaded = load_verified_records(collect_qpr_files([qpr_dir]))
    return render_report(loaded, generated_at=FIXED_AT, tool_version="0.1.0")


def test_golden_file(fixture_records: list[QuantumPerformanceRecord], qpr_dir: Path) -> None:
    """Fixed inputs + fixed generated-at => byte-identical HTML.

    Regenerate intentionally with: UPDATE_GOLDEN=1 uv run pytest tests/report
    """
    document = render(fixture_records, qpr_dir)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN_PATH.write_text(document, encoding="utf-8")
    assert GOLDEN_PATH.exists(), "golden file missing; run with UPDATE_GOLDEN=1"
    assert document == GOLDEN_PATH.read_text(encoding="utf-8")


def test_rendering_is_deterministic(
    fixture_records: list[QuantumPerformanceRecord], qpr_dir: Path
) -> None:
    assert render(fixture_records, qpr_dir) == render(fixture_records, qpr_dir)


def test_report_content(fixture_records: list[QuantumPerformanceRecord], qpr_dir: Path) -> None:
    document = render(fixture_records, qpr_dir)
    # All benchmarks present, charts inline, zero external requests.
    for benchmark_id in (
        "rb_1q",
        "mirror_circuits",
        "quantum_volume",
        "qec_repetition_memory",
        "throughput",
    ):
        assert benchmark_id in document
    assert "<svg" in document
    assert "http://" not in document and "https://" not in document
    assert "2/3 pass threshold" in document
    # The throughput fixture's honesty flag is visibly badged.
    assert "UNRELIABLE" in document
    assert "timing.simulator_not_comparable_to_hardware" in document
    # The QEC criteria scorecard renders with the simulator watermark and
    # grey not-evaluable badges.
    assert "criteria scorecard: ab-lq-2026" in document
    assert "simulated noise model" in document
    assert "not evaluable" in document
    assert "Alice &amp; Bob" in document
    # Content hashes are shown for auditability.
    for record in fixture_records:
        assert record.integrity.content_sha256[:12] in document


def test_refuses_tampered_records(qpr_dir: Path) -> None:
    target = sorted(qpr_dir.glob("*.qpr.json"))[0]
    document = json.loads(target.read_text(encoding="utf-8"))
    document["execution"]["shots"] = 999_999
    target.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ReportInputError) as excinfo:
        load_verified_records(collect_qpr_files([qpr_dir]))
    assert target.name in str(excinfo.value)
    assert "content_hash_mismatch" in str(excinfo.value)


def test_empty_input_is_an_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ReportInputError, match="no QPR files"):
        load_verified_records(collect_qpr_files([empty]))


def test_cli_report(qpr_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "out" / "report.html"
    result = CliRunner().invoke(
        main,
        [
            "report",
            str(qpr_dir),
            "-o",
            str(output),
            "--generated-at",
            "2026-01-01T12:00:00+00:00",
        ],
    )
    assert result.exit_code == 0, result.output
    assert output.exists()
    assert "Veriqore benchmark report" in output.read_text(encoding="utf-8")


def test_cli_report_rejects_naive_timestamp(qpr_dir: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "report",
            str(qpr_dir),
            "-o",
            str(tmp_path / "r.html"),
            "--generated-at",
            "2026-01-01T12:00:00",
        ],
    )
    assert result.exit_code != 0
    assert "timezone" in result.output


def test_cli_report_refusal_lists_the_file(qpr_dir: Path, tmp_path: Path) -> None:
    target = sorted(qpr_dir.glob("*.qpr.json"))[0]
    document = json.loads(target.read_text(encoding="utf-8"))
    document["execution"]["shots"] = 999_999
    target.write_text(json.dumps(document), encoding="utf-8")
    result = CliRunner().invoke(main, ["report", str(qpr_dir), "-o", str(tmp_path / "r.html")])
    assert result.exit_code != 0
    assert target.name in result.output
    assert not (tmp_path / "r.html").exists()


def test_default_generated_at_is_now(qpr_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "now.html"
    result = CliRunner().invoke(main, ["report", str(qpr_dir), "-o", str(output)])
    assert result.exit_code == 0, result.output
    year = str(datetime.now(tz=UTC).year)
    assert f"generated {year}" in output.read_text(encoding="utf-8")
