"""The gate's check-then-append window under contention: with budget room
for exactly one job, N racers must produce exactly one pass."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from veriqant_bench.adapters.errors import CostGateError
from veriqant_bench.adapters.types import CostEstimate
from veriqant_bench.live import SpendLedger, SpendLimits, check_cost_gate

JUNE = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def test_exactly_one_racer_passes_a_one_job_budget(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    limits = SpendLimits(monthly_qpu_seconds_cap=60.0)
    estimate = CostEstimate(
        amount=Decimal("0"), currency="USD", confidence="estimate", qpu_seconds=50.0
    )

    def race(_: int) -> str | None:
        # Each racer gets its own SpendLedger instance (own lock fd), as two
        # separate CLI invocations would.
        ledger = SpendLedger(path, lock_timeout=10.0)
        try:
            return check_cost_gate(
                estimate, limits=limits, ledger=ledger, adapter="test", device="dev", now=JUNE
            )
        except CostGateError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(race, range(8)))

    passes = [entry_id for entry_id in outcomes if entry_id is not None]
    assert len(passes) == 1, f"expected exactly one pass, got {len(passes)}"
    # And the ledger holds exactly that one committed estimate.
    assert SpendLedger(path).monthly_totals(JUNE).entries == 1
