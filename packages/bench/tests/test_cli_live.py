"""CLI surface of live execution: refusals before any network contact,
jobs resume, limits show, and the live-safe probe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from conftest import make_ibm_adapter

from veriqant_bench import cli
from veriqant_bench.cli import main
from veriqant_bench.live import ledger as ledger_module
from veriqant_bench.live import limits as limits_module


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def isolated_live_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the user-level limits/ledger paths into tmp so CLI tests never
    read or write the developer's real ~/.config/veriqant."""
    monkeypatch.setattr(limits_module, "USER_LIMITS_PATH", tmp_path / "limits.toml")
    monkeypatch.setattr(ledger_module, "DEFAULT_LEDGER_PATH", tmp_path / "ledger.jsonl")
    import veriqant_bench.live as live_package

    monkeypatch.setattr(live_package, "DEFAULT_LEDGER_PATH", tmp_path / "ledger.jsonl")
    return tmp_path


def test_run_on_live_adapter_without_live_flag_refuses_offline(runner: CliRunner) -> None:
    # Must refuse before any backend/service resolution (no network).
    result = runner.invoke(main, ["run", "rb", "--adapter", "ibm"])
    assert result.exit_code != 0
    assert "--live" in result.output
    assert "ibm_runtime" in result.output


def test_live_flag_on_local_adapter_is_an_error(runner: CliRunner) -> None:
    result = runner.invoke(main, ["run", "rb", "--adapter", "aer", "--live"])
    assert result.exit_code != 0
    assert "no meaning" in result.output


def test_device_on_local_adapter_is_an_error(runner: CliRunner) -> None:
    result = runner.invoke(main, ["run", "rb", "--adapter", "aer", "--device", "x"])
    assert result.exit_code != 0
    assert "no meaning" in result.output


def test_braket_aws_requires_device(runner: CliRunner) -> None:
    result = runner.invoke(main, ["run", "rb", "--adapter", "braket_aws", "--live"])
    assert result.exit_code != 0
    assert "--device" in result.output


def test_limits_show_reports_zero_trust_defaults(
    runner: CliRunner,
    isolated_live_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)  # no repo-local veriqant-limits.toml
    result = runner.invoke(main, ["limits", "show"])
    assert result.exit_code == 0, result.output
    assert "limits source:        defaults" in result.output
    assert "0.00 USD / month" in result.output
    assert "0.0 s / month" in result.output
    assert "advisory" in result.output


def test_limits_show_reads_a_limits_file_and_the_ledger(
    runner: CliRunner,
    isolated_live_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "limits.toml").write_text(
        "[budgets]\nmonthly_qpu_seconds_cap = 300.0\n", encoding="utf-8"
    )
    from decimal import Decimal

    from veriqant_bench.live import SpendLedger

    SpendLedger(tmp_path / "ledger.jsonl").record_estimate(
        adapter="ibm_runtime", device="d", amount=Decimal("0"), currency="USD", qpu_seconds=42.0
    )
    result = runner.invoke(main, ["limits", "show"])
    assert result.exit_code == 0, result.output
    assert "300.0 s / month" in result.output
    assert "42.0 qpu-seconds (1 entries)" in result.output


def test_jobs_resume_rejects_a_non_handle_file(runner: CliRunner, tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-handle.json"
    bogus.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    result = runner.invoke(main, ["jobs", "resume", str(bogus)])
    assert result.exit_code != 0
    assert "not a veriqant-bench handle file" in result.output


def test_probe_skips_smoke_on_live_adapter_without_live(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, _ = make_ibm_adapter(tmp_path, allow_live=False)

    def fake_get(name: str, **kwargs: Any) -> Any:
        assert name == "ibm_runtime"
        assert kwargs.get("allow_live") is False
        return adapter

    monkeypatch.setattr(cli, "get_adapter", fake_get)
    result = runner.invoke(main, ["adapters", "probe", "ibm_runtime"])
    assert result.exit_code == 0, result.output
    assert '"provider_name": "ibm"' in result.output
    assert "smoke circuit: skipped (live adapter" in result.output


def test_probe_with_live_flag_submits_one_gated_job(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, _ = make_ibm_adapter(tmp_path, allow_live=True)
    monkeypatch.setattr(cli, "get_adapter", lambda name, **kwargs: adapter)
    result = runner.invoke(main, ["adapters", "probe", "ibm_runtime", "--live", "--shots", "32"])
    assert result.exit_code == 0, result.output
    assert "smoke circuit (32 shots)" in result.output
    # The smoke job went through the cost gate and into the ledger.
    assert adapter._ledger.path.is_file()


def test_adapters_list_includes_the_live_adapters(runner: CliRunner) -> None:
    result = runner.invoke(main, ["adapters", "list"])
    assert result.exit_code == 0
    assert "ibm_runtime" in result.output
    assert "braket_aws" in result.output
