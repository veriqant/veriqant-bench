"""Spending limits for live execution.

Two independent budgets, both capped per calendar month (UTC):
- monetary (in `currency` units), and
- QPU/runtime seconds (e.g. IBM's open-plan ~10-minutes-per-month quota).

Out of the box both caps are 0.00: no live execution is possible — not even
monetarily-free open-plan jobs — until the user writes a limits file. Caps
live in config files, deliberately never in CLI flags, so a typo'd flag
cannot raise a limit:

    ~/.config/veriqant/limits.toml        (user-wide)
    ./veriqant-limits.toml                (repo-local, takes precedence)

Example:

    [budgets]
    monthly_monetary_cap = 5.00       # in `currency`
    currency = "USD"
    monthly_qpu_seconds_cap = 300.0   # e.g. half the IBM open-plan quota
    allow_unknown_cost = false        # DANGEROUS: see docs/LIVE.md

A malformed limits file is a hard error naming the file and the problem —
a broken safety config never silently degrades to defaults.
"""

from __future__ import annotations

import os
import tomllib
from decimal import Decimal
from pathlib import Path

import pydantic
from pydantic import BaseModel, ConfigDict, Field

USER_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "veriqant"
USER_LIMITS_PATH = USER_CONFIG_DIR / "limits.toml"
LOCAL_LIMITS_NAME = "veriqant-limits.toml"


class LimitsFileError(ValueError):
    """The limits file exists but cannot be trusted (parse error, unknown
    key, invalid value). Fix the file; the gate will not guess."""


class SpendLimits(BaseModel):
    """Effective spending limits. Defaults are the zero-trust posture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    monthly_monetary_cap: Decimal = Field(default=Decimal("0.00"), ge=0)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    monthly_qpu_seconds_cap: float = Field(default=0.0, ge=0.0)
    allow_unknown_cost: bool = False
    """Permit submissions whose cost cannot be estimated. Dangerous: an
    unknown cost cannot be charged against any budget before it is incurred.
    Config-file only, by design."""

    source: str = "defaults"
    """Where these limits came from (for refusal messages)."""


def load_limits(cwd: Path | None = None) -> SpendLimits:
    """Load limits with the repo-local file taking precedence over the user
    config, falling back to the all-zero defaults."""
    local = (cwd or Path.cwd()) / LOCAL_LIMITS_NAME
    for path in (local, USER_LIMITS_PATH):
        if path.is_file():
            return _parse(path)
    return SpendLimits()


def _parse(path: Path) -> SpendLimits:
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise LimitsFileError(f"{path}: invalid TOML: {exc}") from exc
    unknown_tables = set(document) - {"budgets"}
    if unknown_tables:
        raise LimitsFileError(
            f"{path}: unknown table(s) {sorted(unknown_tables)}; only [budgets] is recognized"
        )
    budgets = document.get("budgets", {})
    if not isinstance(budgets, dict):
        raise LimitsFileError(f"{path}: [budgets] must be a table")
    if "monthly_monetary_cap" in budgets:
        # TOML floats for money are tolerated but normalized through str()
        # to avoid binary-float artifacts in Decimal.
        budgets["monthly_monetary_cap"] = Decimal(str(budgets["monthly_monetary_cap"]))
    try:
        return SpendLimits(**budgets, source=str(path))
    except pydantic.ValidationError as exc:
        raise LimitsFileError(f"{path}: invalid [budgets]: {exc}") from exc
