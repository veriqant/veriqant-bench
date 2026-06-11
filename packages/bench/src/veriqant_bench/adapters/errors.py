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


class LiveRefusedError(SubmissionError):
    """Live execution was refused. The message lists exactly which opt-in
    layer is missing (--live flag, credentials, plan, or the cost gate);
    no environment variable can enable live mode on its own."""


class CostGateError(LiveRefusedError):
    """The pre-submit cost gate refused: estimate over a budget cap, monthly
    ledger exhausted, cost unknown without explicit override, or the ledger
    itself unusable (the gate fails closed)."""


class LedgerError(CostGateError):
    """The spend ledger could not be read or locked. There is no unlocked or
    best-effort fallback: a gate that cannot trust its own bookkeeping
    refuses."""


class CredentialError(LiveRefusedError):
    """Provider credentials are absent, invalid, or expired. For an expiry
    mid-poll, re-authenticate and resume from the persisted handle file."""
