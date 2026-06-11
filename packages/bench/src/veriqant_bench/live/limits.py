"""Spending limits for live execution.

Two independent budgets, both capped per calendar month (UTC):
- monetary (in `currency` units), and
- QPU/runtime seconds (e.g. IBM's open-plan ~10-minutes-per-month quota).

Out of the box both caps are 0.00: no live execution is possible — not even
monetarily-free open-plan jobs — until the user writes a limits file. Caps
live in config files, deliberately never in CLI flags, so a typo'd flag
cannot raise a limit:

    ~/.config/veriqant/limits.toml        (user-wide: the only file that GRANTS budget)
    ./veriqant-limits.toml                (repo-local: may only TIGHTEN, never loosen)

Precedence is monotonically tightening. The user-level file (or, absent one,
the all-zero defaults) sets the ceiling; a repo-local file can lower a cap or
keep allow_unknown_cost off for a project, but can never raise a cap, flip
allow_unknown_cost on, or change the currency. Budgets belong to the
account-holder, not to a repository: `cd` into an untrusted repo that ships a
generous veriqant-limits.toml must not weaken your caps.

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
    """Load the effective limits: the user-level file (or the all-zero
    defaults) is the authority; a repo-local file may only tighten it."""
    base = _parse(USER_LIMITS_PATH) if USER_LIMITS_PATH.is_file() else SpendLimits()
    local_path = (cwd or Path.cwd()) / LOCAL_LIMITS_NAME
    if not local_path.is_file():
        return base
    budgets = _read_budgets(local_path)
    local = _validate(local_path, budgets)
    return _tightened(base, local, declared=set(budgets), local_path=local_path)


def _tightened(
    base: SpendLimits, local: SpendLimits, *, declared: set[str], local_path: Path
) -> SpendLimits:
    """Merge a repo-local file into the base limits, tightening only.

    Fields the repo-local file does not declare inherit the base value —
    tightening one cap must not silently zero the others. Declared caps take
    the elementwise min; allow_unknown_cost can only be forced off (AND);
    the currency cannot change, because a cap denominated in a different
    currency is not comparable to the one it would replace."""
    if "currency" in declared and local.currency != base.currency:
        raise LimitsFileError(
            f"{local_path}: currency {local.currency} differs from the user-level "
            f"budget currency {base.currency}; a repo-local file cannot change the "
            "currency the caps are denominated in"
        )
    monetary = base.monthly_monetary_cap
    if "monthly_monetary_cap" in declared:
        monetary = min(monetary, local.monthly_monetary_cap)
    qpu_seconds = base.monthly_qpu_seconds_cap
    if "monthly_qpu_seconds_cap" in declared:
        qpu_seconds = min(qpu_seconds, local.monthly_qpu_seconds_cap)
    allow_unknown = base.allow_unknown_cost
    if "allow_unknown_cost" in declared:
        allow_unknown = allow_unknown and local.allow_unknown_cost
    return SpendLimits(
        monthly_monetary_cap=monetary,
        currency=base.currency,
        monthly_qpu_seconds_cap=qpu_seconds,
        allow_unknown_cost=allow_unknown,
        source=f"{base.source}, tightened by {local_path}",
    )


def _parse(path: Path) -> SpendLimits:
    return _validate(path, _read_budgets(path))


def _read_budgets(path: Path) -> dict[str, object]:
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
    return budgets


def _validate(path: Path, budgets: dict[str, object]) -> SpendLimits:
    try:
        return SpendLimits(**budgets, source=str(path))
    except pydantic.ValidationError as exc:
        raise LimitsFileError(f"{path}: invalid [budgets]: {exc}") from exc
