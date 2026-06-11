"""The cost-gate decision table — every rule, both directions — plus the
price-table staleness thresholds."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from veriqant_bench.adapters.errors import CostGateError
from veriqant_bench.adapters.types import CostEstimate
from veriqant_bench.live import (
    SpendLedger,
    SpendLimits,
    check_cost_gate,
    classify_price_table_age,
)

JUNE = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
JUNE_30 = datetime(2026, 6, 30, 23, 50, tzinfo=UTC)
JULY = datetime(2026, 7, 1, 0, 10, tzinfo=UTC)


@pytest.fixture
def ledger(tmp_path: Path) -> SpendLedger:
    return SpendLedger(tmp_path / "ledger.jsonl", lock_timeout=2.0)


def gate(
    estimate: CostEstimate,
    limits: SpendLimits,
    ledger: SpendLedger,
    now: datetime = JUNE,
) -> str:
    return check_cost_gate(
        estimate, limits=limits, ledger=ledger, adapter="test", device="dev", now=now
    )


def paid(amount: str, seconds: float = 1.0) -> CostEstimate:
    return CostEstimate(
        amount=Decimal(amount), currency="USD", confidence="estimate", qpu_seconds=seconds
    )


def quota_only(seconds: float) -> CostEstimate:
    return CostEstimate(
        amount=Decimal("0"), currency="USD", confidence="estimate", qpu_seconds=seconds
    )


# ---- default posture: cap 0 blocks everything -----------------------------


def test_cap_zero_blocks_a_paid_estimate(ledger: SpendLedger) -> None:
    with pytest.raises(CostGateError, match=r"over the cap of 0\.00"):
        gate(paid("0.30"), SpendLimits(), ledger)


def test_cap_zero_blocks_a_free_but_quota_consuming_estimate(ledger: SpendLedger) -> None:
    # IBM open plan: $0 but qpu_seconds > 0 — still refused out of the box.
    with pytest.raises(CostGateError, match=r"quota cap of 0\.0"):
        gate(quota_only(7.5), SpendLimits(), ledger)


def test_refusal_commits_nothing_to_the_ledger(ledger: SpendLedger) -> None:
    with pytest.raises(CostGateError):
        gate(paid("0.30"), SpendLimits(), ledger)
    assert ledger.monthly_totals(JUNE).entries == 0


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("defaults", id="no-config-at-all"),
        pytest.param("defaults, tightened by /repo/veriqant-limits.toml", id="repo-local-only"),
    ],
)
def test_default_limits_refusal_names_the_required_user_file(
    ledger: SpendLedger, source: str
) -> None:
    # No user-level limits file (including repo-local-only, which grants
    # nothing): the refusal must say what to create and point at the docs,
    # not just suggest raising a cap in a file that does not exist.
    limits = SpendLimits(source=source)
    with pytest.raises(CostGateError) as excinfo:
        gate(quota_only(7.5), limits, ledger)
    message = str(excinfo.value)
    assert "limits.toml" in message
    assert "docs/LIVE.md" in message


def test_configured_limits_refusal_suggests_raising_the_cap(ledger: SpendLedger) -> None:
    limits = SpendLimits(
        monthly_qpu_seconds_cap=5.0, source="/home/user/.config/veriqant/limits.toml"
    )
    with pytest.raises(CostGateError, match="Raise the cap in the limits file"):
        gate(quota_only(7.5), limits, ledger)


# ---- rule a/b: unknown or vacuous estimates --------------------------------


def test_unknown_confidence_blocks(ledger: SpendLedger) -> None:
    estimate = CostEstimate(
        amount=Decimal("0"), currency="USD", confidence="unknown", qpu_seconds=0.0
    )
    limits = SpendLimits(monthly_monetary_cap=Decimal("100"), monthly_qpu_seconds_cap=1e6)
    with pytest.raises(CostGateError, match="confidence is 'unknown'"):
        gate(estimate, limits, ledger)


def test_missing_qpu_seconds_blocks(ledger: SpendLedger) -> None:
    estimate = CostEstimate(amount=Decimal("1.00"), currency="USD", confidence="estimate")
    limits = SpendLimits(monthly_monetary_cap=Decimal("100"), monthly_qpu_seconds_cap=1e6)
    with pytest.raises(CostGateError, match="no qpu_seconds"):
        gate(estimate, limits, ledger)


def test_both_zero_estimate_blocks_even_under_generous_caps(ledger: SpendLedger) -> None:
    # An estimator claiming a live job is literally free is a bug, not a
    # free job; "cap 0 blocks everything" must be an invariant.
    limits = SpendLimits(monthly_monetary_cap=Decimal("100"), monthly_qpu_seconds_cap=1e6)
    with pytest.raises(CostGateError, match="zero cost on both budgets"):
        gate(quota_only(0.0), limits, ledger)


def test_allow_unknown_cost_admits_and_charges_zero(ledger: SpendLedger) -> None:
    estimate = CostEstimate(amount=Decimal("0"), currency="USD", confidence="unknown")
    limits = SpendLimits(
        monthly_monetary_cap=Decimal("1"), monthly_qpu_seconds_cap=1.0, allow_unknown_cost=True
    )
    entry_id = gate(estimate, limits, ledger)
    assert entry_id
    assert ledger.monthly_totals(JUNE).entries == 1
    assert ledger.monthly_totals(JUNE).qpu_seconds == 0.0


# ---- rule c: currency ------------------------------------------------------


def test_currency_mismatch_blocks_when_money_at_stake(ledger: SpendLedger) -> None:
    limits = SpendLimits(
        monthly_monetary_cap=Decimal("100"), currency="EUR", monthly_qpu_seconds_cap=1e6
    )
    with pytest.raises(CostGateError, match="USD does not match"):
        gate(paid("1.00"), limits, ledger)


def test_currency_mismatch_tolerated_at_zero_amount(ledger: SpendLedger) -> None:
    # A $0.00 quota-only estimate cannot lose money to FX.
    limits = SpendLimits(
        monthly_monetary_cap=Decimal("0"), currency="EUR", monthly_qpu_seconds_cap=100.0
    )
    assert gate(quota_only(10.0), limits, ledger)


# ---- rules d/e: caps and accumulation ---------------------------------------


def test_passes_within_both_caps_and_returns_entry_id(ledger: SpendLedger) -> None:
    limits = SpendLimits(monthly_monetary_cap=Decimal("1.00"), monthly_qpu_seconds_cap=60.0)
    entry_id = gate(paid("0.40", seconds=10.0), limits, ledger)
    totals = ledger.monthly_totals(JUNE)
    assert totals.monetary == Decimal("0.40")
    assert totals.qpu_seconds == 10.0
    assert entry_id in ledger.path.read_text()


def test_cumulative_accounting_blocks_the_job_that_crosses_the_cap(ledger: SpendLedger) -> None:
    limits = SpendLimits(monthly_monetary_cap=Decimal("1.00"), monthly_qpu_seconds_cap=1e6)
    gate(paid("0.40"), limits, ledger)
    gate(paid("0.40"), limits, ledger)
    with pytest.raises(CostGateError, match=r"bring this month's total to 1\.20"):
        gate(paid("0.40"), limits, ledger)
    # Exactly reaching the cap is allowed; exceeding it is not.
    assert gate(paid("0.20"), limits, ledger)


def test_quota_exhaustion_blocks_free_tier(ledger: SpendLedger) -> None:
    limits = SpendLimits(monthly_qpu_seconds_cap=60.0)
    gate(quota_only(50.0), limits, ledger)
    with pytest.raises(CostGateError, match=r"quota cap of 60\.0"):
        gate(quota_only(20.0), limits, ledger)


def test_month_rollover_resets_headroom(ledger: SpendLedger) -> None:
    limits = SpendLimits(monthly_qpu_seconds_cap=60.0)
    gate(quota_only(50.0), limits, ledger, now=JUNE)
    with pytest.raises(CostGateError):
        gate(quota_only(20.0), limits, ledger, now=JUNE_30)
    # New month: the same job passes.
    assert gate(quota_only(20.0), limits, ledger, now=JULY)


def test_provider_reported_actuals_tighten_the_gate(ledger: SpendLedger) -> None:
    limits = SpendLimits(monthly_qpu_seconds_cap=60.0)
    entry = gate(quota_only(10.0), limits, ledger)
    ledger.record_actuals(entry, qpu_seconds=55.0, now=JUNE)
    with pytest.raises(CostGateError):
        gate(quota_only(10.0), limits, ledger)


def test_released_charge_restores_headroom(ledger: SpendLedger) -> None:
    limits = SpendLimits(monthly_qpu_seconds_cap=60.0)
    entry = gate(quota_only(50.0), limits, ledger)
    ledger.record_released(entry, reason="provider rejected submit", now=JUNE)
    assert gate(quota_only(50.0), limits, ledger)


# ---- price-table staleness thresholds (ratified amendment) -------------------


def at_age(days: int) -> tuple[date, datetime]:
    verified = date(2026, 1, 1)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC) + timedelta(days=days)
    return verified, now


@pytest.mark.parametrize(
    ("age_days", "expected"),
    [
        (0, "fresh"),
        (90, "fresh"),  # boundary: exactly 90 days is still fresh
        (91, "warn"),
        (180, "warn"),  # boundary: exactly 180 days still passes, loudly
        (181, "stale"),  # older than 180 days: stale == unknown == refused
        (400, "stale"),
    ],
)
def test_price_table_staleness_thresholds(age_days: int, expected: str) -> None:
    verified, now = at_age(age_days)
    assert classify_price_table_age(verified, now) == expected
