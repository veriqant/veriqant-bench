"""The pre-submit cost gate. Runs before EVERY live submission, no bypass.

Decision procedure (refusals raise CostGateError naming the failing rule,
the limits source, and the ledger):

a. Cost confidence "unknown" or missing qpu_seconds -> refuse, unless
   limits.allow_unknown_cost (a config-file-only override, documented as
   dangerous).
b. Estimate of exactly zero on BOTH budgets -> treated as unknown (a live
   job that claims to cost literally nothing is an estimator bug, not a
   free job), so rule (a) applies. This makes "cap 0 blocks everything" an
   invariant rather than an accident of the estimators.
c. Estimate currency differs from the budget currency (and money is at
   stake) -> refuse; we never guess an exchange rate.
d. Month-to-date monetary spend + estimate > monetary cap -> refuse.
e. Month-to-date QPU seconds + estimate > quota cap -> refuse.

Reading the month-to-date totals and committing the estimate happen under
one exclusive ledger lock, so two concurrent invocations cannot both pass a
cap with room for only one. On pass, the estimate is committed to the
ledger and the entry id returned for inclusion in the QPR.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from veriqant_bench.adapters.errors import CostGateError
from veriqant_bench.adapters.types import CostEstimate

from .ledger import SpendLedger
from .limits import SpendLimits

# Static price tables go stale. Past WARN the gate still passes but warns
# loudly; past REFUSE the price counts as UNKNOWN and the gate refuses (only
# the dangerous allow_unknown_cost overrides). Rationale: stale data gets
# the same treatment as missing data — a price nobody has verified in six
# months cannot be charged against a budget any more honestly than no price
# at all.
PRICE_TABLE_WARN_AFTER_DAYS = 90
PRICE_TABLE_REFUSE_AFTER_DAYS = 180


def classify_price_table_age(
    verified_on: date, now: datetime | None = None
) -> Literal["fresh", "warn", "stale"]:
    """Staleness class of a price table last verified on *verified_on*:
    'fresh' (≤90 days), 'warn' (≤180 days, pass with a loud warning), or
    'stale' (>180 days, treat the price as unknown)."""
    moment = (now or datetime.now(tz=UTC)).astimezone(UTC).date()
    age_days = (moment - verified_on).days
    if age_days > PRICE_TABLE_REFUSE_AFTER_DAYS:
        return "stale"
    if age_days > PRICE_TABLE_WARN_AFTER_DAYS:
        return "warn"
    return "fresh"


def check_cost_gate(
    estimate: CostEstimate,
    *,
    limits: SpendLimits,
    ledger: SpendLedger,
    adapter: str,
    device: str,
    now: datetime | None = None,
) -> str:
    """Gate a live submission; returns the committed ledger entry id on pass."""
    unknown_reason: str | None = None
    if estimate.confidence == "unknown":
        unknown_reason = "cost confidence is 'unknown'"
    elif estimate.qpu_seconds is None:
        unknown_reason = "the estimate carries no qpu_seconds"
    elif estimate.amount == 0 and estimate.qpu_seconds == 0:
        unknown_reason = (
            "the estimate claims zero cost on both budgets — for a live device "
            "that is an estimator bug, not a free job"
        )
    if unknown_reason is not None and not limits.allow_unknown_cost:
        raise CostGateError(
            f"cost gate: cannot charge this job on '{device}' against any budget: "
            f"{unknown_reason}. Refusing by default; to proceed anyway set "
            f"allow_unknown_cost = true in your limits file ({limits.source}) — "
            "this is dangerous, see docs/LIVE.md"
        )
    if estimate.currency != limits.currency and estimate.amount > 0:
        raise CostGateError(
            f"cost gate: estimate currency {estimate.currency} does not match the "
            f"configured budget currency {limits.currency} ({limits.source}); "
            "refusing rather than guessing an exchange rate"
        )
    estimated_seconds = estimate.qpu_seconds or 0.0
    # With no user-level limits file the caps are the all-zero defaults
    # (a repo-local file can only tighten, so "defaults, tightened by ..."
    # is the same situation): tell the user what to create, not to raise a
    # cap in a file that does not exist.
    if limits.source.startswith("defaults"):
        cap_hint = (
            "No user-level limits file is configured, and live runs require one: "
            "create ~/.config/veriqant/limits.toml granting budget — see docs/LIVE.md."
        )
    else:
        cap_hint = "Raise the cap in the limits file if intended."
    with ledger.lock():
        totals = ledger.monthly_totals(now)
        projected_monetary = totals.monetary + estimate.amount
        if projected_monetary > limits.monthly_monetary_cap:
            raise CostGateError(
                f"cost gate: estimated {estimate.amount} {estimate.currency} would "
                f"bring this month's total to {projected_monetary} {limits.currency}, "
                f"over the cap of {limits.monthly_monetary_cap} ({limits.source}; "
                f"ledger: {ledger.path}, {totals.entries} entries this month). "
                f"{cap_hint}"
            )
        projected_seconds = totals.qpu_seconds + estimated_seconds
        if projected_seconds > limits.monthly_qpu_seconds_cap:
            raise CostGateError(
                f"cost gate: estimated {estimated_seconds:.1f} QPU-seconds would "
                f"bring this month's total to {projected_seconds:.1f}s, over the "
                f"quota cap of {limits.monthly_qpu_seconds_cap:.1f}s ({limits.source}; "
                f"ledger: {ledger.path}, {totals.entries} entries this month). "
                f"{cap_hint}"
            )
        return ledger.record_estimate(
            adapter=adapter,
            device=device,
            amount=estimate.amount,
            currency=estimate.currency,
            qpu_seconds=estimated_seconds,
            heuristic=estimate.heuristic,
            now=now,
        )
