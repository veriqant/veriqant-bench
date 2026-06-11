"""limits.toml loading: precedence, defaults, and fail-closed parsing."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from veriqant_bench.live import LimitsFileError, SpendLimits, load_limits
from veriqant_bench.live import limits as limits_module


@pytest.fixture
def isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the user-level limits path into tmp so tests never read a real
    ~/.config/veriqant/limits.toml."""
    user_path = tmp_path / "userconfig" / "limits.toml"
    monkeypatch.setattr(limits_module, "USER_LIMITS_PATH", user_path)
    return user_path


def test_defaults_are_all_zero_and_refuse_unknown(
    isolated_user_config: Path, tmp_path: Path
) -> None:
    limits = load_limits(cwd=tmp_path)
    assert limits.monthly_monetary_cap == Decimal("0.00")
    assert limits.monthly_qpu_seconds_cap == 0.0
    assert limits.allow_unknown_cost is False
    assert limits.source == "defaults"


def test_user_file_is_loaded(isolated_user_config: Path, tmp_path: Path) -> None:
    isolated_user_config.parent.mkdir(parents=True)
    isolated_user_config.write_text(
        '[budgets]\nmonthly_monetary_cap = 5.00\ncurrency = "EUR"\n'
        "monthly_qpu_seconds_cap = 300.0\n",
        encoding="utf-8",
    )
    limits = load_limits(cwd=tmp_path)
    assert limits.monthly_monetary_cap == Decimal("5.00")
    assert limits.currency == "EUR"
    assert limits.monthly_qpu_seconds_cap == 300.0
    assert limits.source == str(isolated_user_config)


def test_repo_local_file_takes_precedence(isolated_user_config: Path, tmp_path: Path) -> None:
    isolated_user_config.parent.mkdir(parents=True)
    isolated_user_config.write_text(
        "[budgets]\nmonthly_qpu_seconds_cap = 600.0\n", encoding="utf-8"
    )
    local = tmp_path / "veriqant-limits.toml"
    local.write_text("[budgets]\nmonthly_qpu_seconds_cap = 60.0\n", encoding="utf-8")
    limits = load_limits(cwd=tmp_path)
    assert limits.monthly_qpu_seconds_cap == 60.0
    assert limits.source == str(local)


def test_money_parses_through_decimal_str_no_float_artifacts(
    isolated_user_config: Path, tmp_path: Path
) -> None:
    local = tmp_path / "veriqant-limits.toml"
    local.write_text("[budgets]\nmonthly_monetary_cap = 0.10\n", encoding="utf-8")
    limits = load_limits(cwd=tmp_path)
    assert limits.monthly_monetary_cap == Decimal("0.1")


def test_unknown_key_is_a_hard_error_naming_the_file(
    isolated_user_config: Path, tmp_path: Path
) -> None:
    local = tmp_path / "veriqant-limits.toml"
    # A typo'd cap name must never silently leave the real cap at a default.
    local.write_text("[budgets]\nmonthly_monetary_capp = 100.0\n", encoding="utf-8")
    with pytest.raises(LimitsFileError, match=str(local).replace("\\", "\\\\")):
        load_limits(cwd=tmp_path)


def test_unknown_table_is_a_hard_error(isolated_user_config: Path, tmp_path: Path) -> None:
    local = tmp_path / "veriqant-limits.toml"
    local.write_text("[budget]\nmonthly_monetary_cap = 100.0\n", encoding="utf-8")
    with pytest.raises(LimitsFileError, match="unknown table"):
        load_limits(cwd=tmp_path)


def test_malformed_toml_is_a_hard_error(isolated_user_config: Path, tmp_path: Path) -> None:
    local = tmp_path / "veriqant-limits.toml"
    local.write_text("[budgets\nmonthly_monetary_cap = 1.0\n", encoding="utf-8")
    with pytest.raises(LimitsFileError, match="invalid TOML"):
        load_limits(cwd=tmp_path)


def test_invalid_values_are_hard_errors(isolated_user_config: Path, tmp_path: Path) -> None:
    local = tmp_path / "veriqant-limits.toml"
    local.write_text('[budgets]\ncurrency = "euros"\n', encoding="utf-8")
    with pytest.raises(LimitsFileError, match="invalid \\[budgets\\]"):
        load_limits(cwd=tmp_path)


def test_negative_caps_rejected(isolated_user_config: Path, tmp_path: Path) -> None:
    local = tmp_path / "veriqant-limits.toml"
    local.write_text("[budgets]\nmonthly_qpu_seconds_cap = -1.0\n", encoding="utf-8")
    with pytest.raises(LimitsFileError):
        load_limits(cwd=tmp_path)


def test_limits_model_is_frozen() -> None:
    limits = SpendLimits()
    with pytest.raises(Exception, match="frozen"):
        limits.monthly_monetary_cap = Decimal("9999")  # type: ignore[misc]
