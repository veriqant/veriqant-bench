"""Loader for the committed report fixtures.

The fixture records are static, committed JSON files (tests/report/data/),
quantized and sealed by make_fixtures.py. They are deliberately NOT
computed at test time: scipy/numpy float results differ in the last ulps
across platforms, which changes content seals even when every displayed
value is identical — a confirmed instance of the float-canonicalization
limitation documented in docs/QPR-SPEC.md. Regenerate intentionally with
make_fixtures.py (see its docstring).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from veriqant_bench.qpr import QuantumPerformanceRecord, load_qpr

FIXTURE_DIR = Path(__file__).parent / "data"
FIXTURE_NAMES = ["rb", "mirror", "qv", "qec", "throughput"]
FIXED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def fixture_paths() -> list[Path]:
    return [FIXTURE_DIR / f"{name}.qpr.json" for name in FIXTURE_NAMES]


def load_fixture_records() -> list[QuantumPerformanceRecord]:
    return [load_qpr(path) for path in fixture_paths()]
