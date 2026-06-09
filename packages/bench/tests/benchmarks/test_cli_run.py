"""CLI tests for `veriqore-bench run rb|mirror`."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from veriqore_bench.cli import main
from veriqore_bench.qpr import verify_qpr_file

pytestmark = pytest.mark.slow

LAST_LINE = re.compile(r"^(?P<path>\S+\.qpr\.json) (?P<hash>[0-9a-f]{64})$")


def run_cli(args: list[str]) -> tuple[int, str]:
    result = CliRunner().invoke(main, args)
    return result.exit_code, result.output


def check_emitted_qpr(output: str) -> Path:
    last = output.strip().splitlines()[-1]
    match = LAST_LINE.match(last)
    assert match, f"last line must be '<path> <content hash>', got {last!r}"
    path = Path(match["path"])
    assert path.exists()
    assert verify_qpr_file(path).ok
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["integrity"]["content_sha256"] == match["hash"]
    return path


def test_run_rb_emits_verified_qpr(tmp_path: Path) -> None:
    code, output = run_cli(
        [
            "run",
            "rb",
            "--adapter",
            "aer",
            "--qubits",
            "0",
            "--lengths",
            "1,2,4",
            "--samples",
            "2",
            "--shots",
            "64",
            "--seed",
            "7",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0, output
    path = check_emitted_qpr(output)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["benchmark"]["id"] == "rb_1q"
    assert document["execution"]["seed"] == 7


def test_run_rb_generates_and_prints_seed_when_omitted(tmp_path: Path) -> None:
    code, output = run_cli(
        [
            "run",
            "rb",
            "--lengths",
            "1,2,4",
            "--samples",
            "2",
            "--shots",
            "32",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0, output
    assert "seed:" in output and "generated" in output
    check_emitted_qpr(output)


def test_run_mirror_emits_verified_qpr(tmp_path: Path) -> None:
    code, output = run_cli(
        [
            "run",
            "mirror",
            "--qubits",
            "0,1",
            "--depths",
            "1,2",
            "--samples",
            "2",
            "--shots",
            "32",
            "--seed",
            "7",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0, output
    path = check_emitted_qpr(output)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["benchmark"]["id"] == "mirror_circuits"


def test_run_rb_with_noise_file_records_calibration(tmp_path: Path) -> None:
    noise_file = tmp_path / "noise.json"
    noise_file.write_text(json.dumps({"depolarizing_1q": 0.05}), encoding="utf-8")
    code, output = run_cli(
        [
            "run",
            "rb",
            "--lengths",
            "1,2,4",
            "--samples",
            "2",
            "--shots",
            "32",
            "--seed",
            "3",
            "--noise",
            str(noise_file),
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0, output
    path = check_emitted_qpr(output)
    document = json.loads(path.read_text(encoding="utf-8"))
    snapshot = document["device"]["calibration_snapshot"]
    assert snapshot["noise_spec"]["depolarizing_1q"] == 0.05


def test_noise_with_non_aer_adapter_is_rejected(tmp_path: Path) -> None:
    noise_file = tmp_path / "noise.json"
    noise_file.write_text(json.dumps({"depolarizing_1q": 0.05}), encoding="utf-8")
    code, output = run_cli(
        [
            "run",
            "rb",
            "--adapter",
            "braket_local",
            "--noise",
            str(noise_file),
            "--out",
            str(tmp_path),
        ]
    )
    assert code != 0
    assert "only supported by the aer_simulator adapter" in output


def test_invalid_noise_file_is_rejected(tmp_path: Path) -> None:
    noise_file = tmp_path / "noise.json"
    noise_file.write_text(json.dumps({"depolarizing_1q": 2.0}), encoding="utf-8")
    code, output = run_cli(["run", "rb", "--noise", str(noise_file), "--out", str(tmp_path)])
    assert code != 0
    assert "invalid noise spec" in output


def test_unknown_adapter_fails_cleanly(tmp_path: Path) -> None:
    code, output = run_cli(["run", "rb", "--adapter", "warp_drive", "--out", str(tmp_path)])
    assert code != 0
    assert "unknown adapter" in output


def test_invalid_params_fail_cleanly(tmp_path: Path) -> None:
    code, output = run_cli(
        ["run", "rb", "--qubits", "0,1,2", "--out", str(tmp_path), "--seed", "1"]
    )
    assert code != 0
    assert "invalid parameters" in output


def test_bad_int_list_fails_cleanly(tmp_path: Path) -> None:
    code, output = run_cli(["run", "rb", "--qubits", "zero", "--out", str(tmp_path)])
    assert code != 0
    assert "comma-separated integers" in output
