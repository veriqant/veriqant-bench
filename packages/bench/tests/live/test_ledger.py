"""The append-only spend ledger: month attribution, amendments, fail-closed
reads, and the lock-or-refuse rule."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from veriqant_bench.adapters.errors import LedgerError
from veriqant_bench.live import SpendLedger
from veriqant_bench.live import ledger as ledger_module

JUNE = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
JUNE_30 = datetime(2026, 6, 30, 23, 50, tzinfo=UTC)
JULY = datetime(2026, 7, 1, 0, 10, tzinfo=UTC)


@pytest.fixture
def ledger(tmp_path: Path) -> SpendLedger:
    return SpendLedger(tmp_path / "ledger.jsonl", lock_timeout=2.0)


def test_empty_ledger_totals_zero(ledger: SpendLedger) -> None:
    totals = ledger.monthly_totals(JUNE)
    assert totals.monetary == Decimal("0")
    assert totals.qpu_seconds == 0.0
    assert totals.entries == 0


def test_estimates_accumulate_within_a_month(ledger: SpendLedger) -> None:
    for _ in range(3):
        ledger.record_estimate(
            adapter="a",
            device="d",
            amount=Decimal("1.50"),
            currency="USD",
            qpu_seconds=10.0,
            now=JUNE,
        )
    totals = ledger.monthly_totals(JUNE)
    assert totals.monetary == Decimal("4.50")
    assert totals.qpu_seconds == 30.0
    assert totals.entries == 3


def test_months_are_independent(ledger: SpendLedger) -> None:
    ledger.record_estimate(
        adapter="a",
        device="d",
        amount=Decimal("2.00"),
        currency="USD",
        qpu_seconds=5.0,
        now=JUNE,
    )
    assert ledger.monthly_totals(JULY).monetary == Decimal("0")
    assert ledger.monthly_totals(JULY).entries == 0
    assert ledger.monthly_totals(JUNE).monetary == Decimal("2.00")


def test_actuals_raise_but_never_lower_the_charge(ledger: SpendLedger) -> None:
    entry = ledger.record_estimate(
        adapter="a",
        device="d",
        amount=Decimal("1.00"),
        currency="USD",
        qpu_seconds=10.0,
        now=JUNE,
    )
    # Lower actual: the conservative estimate stands.
    ledger.record_actuals(entry, qpu_seconds=4.0, now=JUNE)
    assert ledger.monthly_totals(JUNE).qpu_seconds == 10.0
    # Higher actual: the committed figure rises.
    ledger.record_actuals(entry, qpu_seconds=25.0, now=JUNE)
    assert ledger.monthly_totals(JUNE).qpu_seconds == 25.0


def test_cross_month_amendment_applies_to_the_estimates_month(ledger: SpendLedger) -> None:
    # Job charged June 30, actuals reported July 1: June is amended; July
    # stays untouched. (The reference spike dropped this amendment.)
    entry = ledger.record_estimate(
        adapter="a",
        device="d",
        amount=Decimal("0.00"),
        currency="USD",
        qpu_seconds=10.0,
        now=JUNE_30,
    )
    ledger.record_actuals(entry, qpu_seconds=42.0, now=JULY)
    assert ledger.monthly_totals(JUNE_30).qpu_seconds == 42.0
    assert ledger.monthly_totals(JULY).entries == 0


def test_released_returns_the_charge(ledger: SpendLedger) -> None:
    entry = ledger.record_estimate(
        adapter="a",
        device="d",
        amount=Decimal("3.00"),
        currency="USD",
        qpu_seconds=30.0,
        now=JUNE,
    )
    ledger.record_released(entry, reason="provider rejected submit", now=JUNE)
    totals = ledger.monthly_totals(JUNE)
    assert totals.monetary == Decimal("0")
    assert totals.qpu_seconds == 0.0


def test_file_is_append_only_jsonl_with_string_money(ledger: SpendLedger) -> None:
    entry = ledger.record_estimate(
        adapter="a",
        device="d",
        amount=Decimal("1.25"),
        currency="USD",
        qpu_seconds=7.5,
        heuristic="per_circuit_1s_per_shot_1ms",
        now=JUNE,
    )
    ledger.record_actuals(entry, qpu_seconds=4.0, now=JUNE)
    lines = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert [line["kind"] for line in lines] == ["estimate", "actuals"]
    assert lines[0]["amount"] == "1.25"  # decimal string, never a float
    assert lines[0]["heuristic"] == "per_circuit_1s_per_shot_1ms"
    assert lines[1]["ref"] == entry


def test_corrupt_line_refuses_totals(ledger: SpendLedger) -> None:
    ledger.record_estimate(
        adapter="a",
        device="d",
        amount=Decimal("1.00"),
        currency="USD",
        qpu_seconds=1.0,
        now=JUNE,
    )
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    with pytest.raises(LedgerError, match="corrupt ledger line"):
        ledger.monthly_totals(JUNE)


def test_unknown_entry_kind_refuses_totals(ledger: SpendLedger) -> None:
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "discount", "timestamp": JUNE.isoformat()}) + "\n")
    with pytest.raises(LedgerError, match="unknown entry kind"):
        ledger.monthly_totals(JUNE)


def test_lock_is_reentrant_within_an_instance(ledger: SpendLedger) -> None:
    with ledger.lock():
        # record_estimate locks internally; must not deadlock.
        ledger.record_estimate(
            adapter="a",
            device="d",
            amount=Decimal("0.00"),
            currency="USD",
            qpu_seconds=1.0,
            now=JUNE,
        )
    assert ledger.monthly_totals(JUNE).entries == 1


def test_contended_lock_times_out_with_typed_error(tmp_path: Path) -> None:
    holder = SpendLedger(tmp_path / "ledger.jsonl", lock_timeout=2.0)
    contender = SpendLedger(tmp_path / "ledger.jsonl", lock_timeout=0.2)
    with holder.lock(), pytest.raises(LedgerError, match="could not lock"), contender.lock():
        pass  # pragma: no cover - must not be reached


def test_no_locking_primitive_refuses(ledger: SpendLedger, monkeypatch: pytest.MonkeyPatch) -> None:
    # Amendment: lock-or-refuse on ALL platforms — no unlocked fallback.
    monkeypatch.setattr(ledger_module, "LOCK_BACKEND", None)
    with pytest.raises(LedgerError, match="no file-locking primitive"), ledger.lock():
        pass  # pragma: no cover - must not be reached
