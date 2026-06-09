"""QPU adapters: the neutral execution contract and its implementations.

Concrete adapters (AerSimulatorAdapter, BraketLocalAdapter) are intentionally
not imported here — their SDKs are optional extras. Reach them through the
registry (`get`, `list_adapters`) or import their modules directly.
"""

from .errors import (
    AdapterError,
    AdapterUnavailableError,
    ExecutionError,
    SubmissionError,
    TimeoutError,
    UnknownJobError,
    UnsupportedCircuitError,
)
from .lifecycle import AwaitResultMixin, await_result
from .local import LocalAdapterBase
from .protocol import QPUAdapter
from .registry import ENTRY_POINT_GROUP, AdapterInfo, get, list_adapters
from .types import (
    VALID_TRANSITIONS,
    CalibrationSnapshot,
    CostEstimate,
    DeviceCapabilities,
    JobHandle,
    JobResult,
    JobSpec,
    JobStatus,
    NoiseSpec,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "VALID_TRANSITIONS",
    "AdapterError",
    "AdapterInfo",
    "AdapterUnavailableError",
    "AwaitResultMixin",
    "CalibrationSnapshot",
    "CostEstimate",
    "DeviceCapabilities",
    "ExecutionError",
    "JobHandle",
    "JobResult",
    "JobSpec",
    "JobStatus",
    "LocalAdapterBase",
    "NoiseSpec",
    "QPUAdapter",
    "SubmissionError",
    "TimeoutError",
    "UnknownJobError",
    "UnsupportedCircuitError",
    "await_result",
    "get",
    "list_adapters",
]
