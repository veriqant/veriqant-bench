"""Typed error hierarchy for the QPUAdapter contract.

Adapters never let backend exceptions escape bare: everything a caller can
catch is an AdapterError subclass.
"""

from __future__ import annotations

import builtins


class AdapterError(Exception):
    """Base class for every error raised through the adapter contract."""


class AdapterUnavailableError(AdapterError):
    """The requested adapter is not installed or cannot be constructed."""


class SubmissionError(AdapterError):
    """The job was rejected before execution (e.g. malformed QASM,
    unsupported operations, unknown job handle)."""


class ExecutionError(AdapterError):
    """The job was accepted but failed (or was cancelled) during execution."""


class TimeoutError(AdapterError, builtins.TimeoutError):
    """Waiting on a job exceeded the caller's deadline.

    Deliberately shadows the builtin within this namespace; also inherits
    from it so `except TimeoutError` works either way.
    """


class UnknownJobError(SubmissionError):
    """The job handle does not belong to this adapter instance."""


class UnsupportedCircuitError(SubmissionError):
    """The circuit uses constructs this adapter cannot execute faithfully
    (e.g. mid-circuit measurement or dynamic control flow on a backend that
    samples final-state distributions). Raised instead of returning a
    silently wrong answer."""
