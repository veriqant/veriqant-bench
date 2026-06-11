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


def test_repo_local_file_tightens_a_looser_user_cap(
    isolated_user_config: Path, tmp_path: Path
) -> None:
    isolated_user_config.parent.mkdir(parents=True)
    isolated_user_config.write_text(
        "[budgets]\nmonthly_qpu_seconds_cap = 600.0\n", encoding="utf-8"
    )
    local = tmp_path / "veriqant-limits.toml"
    local.write_text("[budgets]\nmonthly_qpu_seconds_cap = 60.0\n", encoding="utf-8")
    limits = load_limits(cwd=tmp_path)
    assert limits.monthly_qpu_seconds_cap == 60.0
    assert str(local) in limits.source
    assert str(isolated_user_config) in limits.source


def test_repo_local_file_cannot_weaken_user_caps(
    isolated_user_config: Path, tmp_path: Path
) -> None:
    # The Session 2b repro, inverted: an untrusted repo ships a generous
    # veriqant-limits.toml. The user's stricter caps must win on every
    # field, and allow_unknown_cost must stay off.
    isolated_user_config.parent.mkdir(parents=True)
    isolated_user_config.write_text(
        "[budgets]\nmonthly_monetary_cap = 5.00\nmonthly_qpu_seconds_cap = 60.0\n"
        "allow_unknown_cost = false\n",
        encoding="utf-8",
    )
    evil = tmp_path / "veriqant-limits.toml"
    evil.write_text(
        "[budgets]\nmonthly_monetary_cap = 1000000\nmonthly_qpu_seconds_cap = 999999\n"
        "allow_unknown_cost = true\n",
        encoding="utf-8",
    )
    limits = load_limits(cwd=tmp_path)
    assert limits.monthly_monetary_cap == Decimal("5.00")
    assert limits.monthly_qpu_seconds_cap == 60.0
    assert limits.allow_unknown_cost is False


def test_repo_local_fields_it_does_not_declare_inherit_the_user_values(
    isolated_user_config: Path, tmp_path: Path
) -> None:
    # Tightening one cap must not silently zero the others.
    isolated_user_config.parent.mkdir(parents=True)
    isolated_user_config.write_text(
        '[budgets]\nmonthly_monetary_cap = 5.00\ncurrency = "EUR"\n'
        "monthly_qpu_seconds_cap = 300.0\n",
        encoding="utf-8",
    )
    local = tmp_path / "veriqant-limits.toml"
    local.write_text("[budgets]\nmonthly_qpu_seconds_cap = 60.0\n", encoding="utf-8")
    limits = load_limits(cwd=tmp_path)
    assert limits.monthly_monetary_cap == Decimal("5.00")
    assert limits.currency == "EUR"
    assert limits.monthly_qpu_seconds_cap == 60.0


def test_repo_local_file_alone_cannot_grant_any_budget(
    isolated_user_config: Path, tmp_path: Path
) -> None:
    # With no user-level file the baseline is the all-zero defaults, and a
    # repo-local file can only tighten — so a drive-by repo cannot write
    # the limits file "for" a user who never configured one.
    local = tmp_path / "veriqant-limits.toml"
    local.write_text(
        "[budgets]\nmonthly_monetary_cap = 1000000\nmonthly_qpu_seconds_cap = 999999\n"
        "allow_unknown_cost = true\n",
        encoding="utf-8",
    )
    limits = load_limits(cwd=tmp_path)
    assert limits.monthly_monetary_cap == Decimal("0.00")
    assert limits.monthly_qpu_seconds_cap == 0.0
    assert limits.allow_unknown_cost is False


def test_repo_local_file_cannot_change_the_currency(
    isolated_user_config: Path, tmp_path: Path
) -> None:
    # A cap denominated in a different currency is not comparable; allowing
    # the switch would let 5.00 EUR become 5.00 of anything.
    isolated_user_config.parent.mkdir(parents=True)
    isolated_user_config.write_text(
        '[budgets]\nmonthly_monetary_cap = 5.00\ncurrency = "EUR"\n', encoding="utf-8"
    )
    local = tmp_path / "veriqant-limits.toml"
    local.write_text('[budgets]\ncurrency = "USD"\n', encoding="utf-8")
    with pytest.raises(LimitsFileError, match="currency"):
        load_limits(cwd=tmp_path)


def test_money_parses_through_decimal_str_no_float_artifacts(
    isolated_user_config: Path, tmp_path: Path
) -> None:
    isolated_user_config.parent.mkdir(parents=True)
    isolated_user_config.write_text("[budgets]\nmonthly_monetary_cap = 0.10\n", encoding="utf-8")
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
