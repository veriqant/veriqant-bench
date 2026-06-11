"""Local append-only spend ledger (JSONL).

Every gated submission appends an `estimate` entry; provider-reported usage
appends `actuals` amendments and a synchronously rejected submit appends a
`released` amendment — all referencing the original entry id. The file is
never rewritten. Monthly totals drive the cost gate, so caps apply per
calendar month (UTC), not per job; amendments apply to *their estimate's*
month regardless of when they were written, so a job that finishes after
month rollover still amends the month it was charged to.

Concurrency: the gate is check-then-append, so reading totals and appending
the estimate must be mutually exclusive across processes. `lock()` takes an
exclusive OS-level lock on a sidecar lock file (flock on POSIX,
msvcrt.locking on Windows). If the lock cannot be acquired within the
timeout, or no locking primitive exists on the platform, the operation fails
with LedgerError — there is no unlocked fallback path.

This ledger is ADVISORY CLIENT-SIDE BOOKKEEPING: it cannot see jobs
submitted by other tools or other machines, and it is not a substitute for
provider-side billing alarms (see docs/LIVE.md).
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from veriqant_bench.adapters.errors import LedgerError

from .limits import USER_CONFIG_DIR

DEFAULT_LEDGER_PATH = USER_CONFIG_DIR / "ledger.jsonl"
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
_LOCK_RETRY_SECONDS = 0.05

LOCK_BACKEND: str | None
if sys.platform == "win32":  # pragma: no cover - Windows-only branch
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    LOCK_BACKEND = "msvcrt"
else:
    try:
        import fcntl

        def _try_lock(fd: int) -> bool:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return True

        def _unlock(fd: int) -> None:
            fcntl.flock(fd, fcntl.LOCK_UN)

        LOCK_BACKEND = "flock"
    except ImportError:  # pragma: no cover - platform with neither primitive

        def _try_lock(fd: int) -> bool:
            raise AssertionError("unreachable: lock() refuses when LOCK_BACKEND is None")

        def _unlock(fd: int) -> None:
            raise AssertionError("unreachable: lock() refuses when LOCK_BACKEND is None")

        LOCK_BACKEND = None


@dataclass(frozen=True)
class MonthlyTotals:
    monetary: Decimal
    qpu_seconds: float
    entries: int


class SpendLedger:
    """One instance per task; instances are not thread-safe, but distinct
    instances (and distinct processes) on the same path are serialized by
    the file lock."""

    def __init__(
        self, path: Path | None = None, *, lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS
    ) -> None:
        self.path = path or DEFAULT_LEDGER_PATH
        self._lock_timeout = lock_timeout
        self._lock_depth = 0
        self._lock_fd: int | None = None

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Exclusive cross-process lock; reentrant within this instance.
        Raises LedgerError on timeout or when no locking primitive exists."""
        if self._lock_depth > 0:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return
        if LOCK_BACKEND is None:
            raise LedgerError(
                "no file-locking primitive available on this platform; refusing to "
                "use the spend ledger without mutual exclusion (no unlocked fallback)"
            )
        lock_path = self.path.with_name(self.path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        deadline = time.monotonic() + self._lock_timeout
        try:
            while not _try_lock(fd):
                if time.monotonic() >= deadline:
                    raise LedgerError(
                        f"could not lock {lock_path} within {self._lock_timeout:.1f}s — "
                        "another submission is holding the ledger; retry when it finishes"
                    )
                time.sleep(_LOCK_RETRY_SECONDS)
            self._lock_fd = fd
            self._lock_depth = 1
            try:
                yield
            finally:
                self._lock_depth = 0
                self._lock_fd = None
                _unlock(fd)
        finally:
            os.close(fd)

    def record_estimate(
        self,
        *,
        adapter: str,
        device: str,
        amount: Decimal,
        currency: str,
        qpu_seconds: float,
        heuristic: str | None = None,
        now: datetime | None = None,
    ) -> str:
        """Append an estimate entry; returns its id (recorded in the QPR)."""
        entry_id = uuid4().hex
        entry: dict[str, Any] = {
            "kind": "estimate",
            "id": entry_id,
            "timestamp": _timestamp(now),
            "adapter": adapter,
            "device": device,
            "amount": str(amount),
            "currency": currency,
            "qpu_seconds": qpu_seconds,
        }
        if heuristic is not None:
            entry["heuristic"] = heuristic
        with self.lock():
            self._append(entry)
        return entry_id

    def record_actuals(
        self,
        entry_id: str,
        *,
        amount: Decimal | None = None,
        qpu_seconds: float | None = None,
        now: datetime | None = None,
    ) -> None:
        """Append an amendment with provider-reported actuals, where known."""
        entry: dict[str, Any] = {
            "kind": "actuals",
            "ref": entry_id,
            "timestamp": _timestamp(now),
        }
        if amount is not None:
            entry["amount"] = str(amount)
        if qpu_seconds is not None:
            entry["qpu_seconds"] = qpu_seconds
        with self.lock():
            self._append(entry)

    def record_released(self, entry_id: str, *, reason: str, now: datetime | None = None) -> None:
        """Append a release: the provider synchronously rejected the submit,
        so the estimate's charge is returned to the budget."""
        with self.lock():
            self._append(
                {
                    "kind": "released",
                    "ref": entry_id,
                    "timestamp": _timestamp(now),
                    "reason": reason,
                }
            )

    def monthly_totals(self, now: datetime | None = None) -> MonthlyTotals:
        """Committed spend for the given moment's calendar month (UTC).

        Estimates count until amended; an `actuals` amendment contributes
        conservatively as max(estimate, actual) — it can raise the committed
        figure, never lower it. `released` zeroes its estimate. Amendments
        are attributed to their estimate's month via `ref`, regardless of
        the amendment's own timestamp."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        estimates: dict[str, dict[str, Any]] = {}
        actuals: dict[str, dict[str, Any]] = {}
        released: set[str] = set()
        for entry in self._read():
            kind = entry.get("kind")
            if kind == "estimate":
                stamp = datetime.fromisoformat(entry["timestamp"]).astimezone(UTC)
                if (stamp.year, stamp.month) == (moment.year, moment.month):
                    estimates[entry["id"]] = entry
            elif kind == "actuals":
                actuals[entry["ref"]] = entry  # last amendment wins
            elif kind == "released":
                released.add(entry["ref"])
            else:
                raise LedgerError(f"{self.path}: unknown entry kind {kind!r}; refusing to total")
        monetary = Decimal("0")
        qpu_seconds = 0.0
        for entry_id, estimate in estimates.items():
            if entry_id in released:
                continue
            amendment = actuals.get(entry_id, {})
            estimated_amount = Decimal(estimate["amount"])
            actual_amount = amendment.get("amount")
            monetary += (
                max(estimated_amount, Decimal(actual_amount))
                if actual_amount is not None
                else estimated_amount
            )
            estimated_seconds = float(estimate["qpu_seconds"])
            actual_seconds = amendment.get("qpu_seconds")
            qpu_seconds += (
                max(estimated_seconds, float(actual_seconds))
                if actual_seconds is not None
                else estimated_seconds
            )
        return MonthlyTotals(monetary=monetary, qpu_seconds=qpu_seconds, entries=len(estimates))

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as exc:
                    # Fail closed: totals from a file we cannot fully read
                    # would understate committed spend.
                    raise LedgerError(
                        f"{self.path}:{line_number}: corrupt ledger line ({exc}); "
                        "refusing to compute totals from a partially readable ledger"
                    ) from exc
                if not isinstance(parsed, dict):
                    raise LedgerError(
                        f"{self.path}:{line_number}: ledger line is not an object; "
                        "refusing to compute totals"
                    )
                entries.append(parsed)
        return entries


def _timestamp(now: datetime | None) -> str:
    return (now or datetime.now(tz=UTC)).astimezone(UTC).isoformat()
