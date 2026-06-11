"""Live-execution guardrails: spending limits, the append-only spend
ledger, the pre-submit cost gate, and the shared live-adapter lifecycle.

Everything here is provider-independent. The concrete live adapters
(veriqant_bench.adapters.ibm, veriqant_bench.adapters.braket_aws) build on
LiveAdapterBase; the guardrail pieces are importable on a core install with
no provider SDKs present.
"""

from .base import (
    DEFAULT_JOBS_DIR,
    DEFAULT_LIVE_TIMEOUT_SECONDS,
    LiveAdapterBase,
)
from .gate import (
    PRICE_TABLE_REFUSE_AFTER_DAYS,
    PRICE_TABLE_WARN_AFTER_DAYS,
    check_cost_gate,
    classify_price_table_age,
)
from .ledger import DEFAULT_LEDGER_PATH, LOCK_BACKEND, MonthlyTotals, SpendLedger
from .limits import (
    LOCAL_LIMITS_NAME,
    USER_CONFIG_DIR,
    USER_LIMITS_PATH,
    LimitsFileError,
    SpendLimits,
    load_limits,
)

__all__ = [
    "DEFAULT_JOBS_DIR",
    "DEFAULT_LEDGER_PATH",
    "DEFAULT_LIVE_TIMEOUT_SECONDS",
    "LOCAL_LIMITS_NAME",
    "LOCK_BACKEND",
    "PRICE_TABLE_REFUSE_AFTER_DAYS",
    "PRICE_TABLE_WARN_AFTER_DAYS",
    "USER_CONFIG_DIR",
    "USER_LIMITS_PATH",
    "LimitsFileError",
    "LiveAdapterBase",
    "MonthlyTotals",
    "SpendLedger",
    "SpendLimits",
    "check_cost_gate",
    "classify_price_table_age",
    "load_limits",
]
