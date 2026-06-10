"""Benchmark discovery via the 'veriqant_bench.benchmarks' entry-point group.

Same dogfooding pattern as the adapter registry: built-in benchmarks register
through entry points in this package's own pyproject, third-party benchmarks
through theirs.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from .base import Benchmark

ENTRY_POINT_GROUP = "veriqant_bench.benchmarks"

_INSTALL_HINTS = {
    "rb": "pip install 'veriqant-bench[local]'",
    "mirror": "pip install 'veriqant-bench[local]'",
    "qv": "pip install 'veriqant-bench[local]'",
    "throughput": "pip install 'veriqant-bench[local]'",
    "qec_repetition": "pip install 'veriqant-bench[local,qec]'",
    "qec_surface": "pip install 'veriqant-bench[local,qec]'",
}


class BenchmarkUnavailableError(RuntimeError):
    """The requested benchmark is unknown or its dependencies are missing."""


@dataclass(frozen=True)
class BenchmarkInfo:
    name: str
    description: str
    available: bool
    install_hint: str | None = None
    error: str | None = None


def _discover() -> list[EntryPoint]:
    return sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda ep: ep.name)


def _hint_for(name: str) -> str:
    return _INSTALL_HINTS.get(name, f"install the package providing benchmark '{name}'")


def list_benchmarks() -> list[BenchmarkInfo]:
    """Every registered benchmark, with availability status."""
    infos: list[BenchmarkInfo] = []
    for entry_point in _discover():
        try:
            factory = entry_point.load()
        except ImportError as exc:
            infos.append(
                BenchmarkInfo(
                    name=entry_point.name,
                    description="",
                    available=False,
                    install_hint=_hint_for(entry_point.name),
                    error=str(exc),
                )
            )
            continue
        doc = (factory.__doc__ or "").strip().splitlines()
        infos.append(
            BenchmarkInfo(name=entry_point.name, description=doc[0] if doc else "", available=True)
        )
    return infos


def get(name: str, **kwargs: Any) -> Benchmark[Any]:
    """Instantiate a registered benchmark by name."""
    for entry_point in _discover():
        if entry_point.name != name:
            continue
        try:
            factory = entry_point.load()
        except ImportError as exc:
            raise BenchmarkUnavailableError(
                f"benchmark '{name}' is registered but not installed; {_hint_for(name)}"
            ) from exc
        benchmark = factory(**kwargs)
        if not isinstance(benchmark, Benchmark):
            raise BenchmarkUnavailableError(
                f"benchmark '{name}' does not implement the Benchmark base class"
            )
        return benchmark
    known = ", ".join(info.name for info in list_benchmarks()) or "<none>"
    raise BenchmarkUnavailableError(f"unknown benchmark '{name}'; registered benchmarks: {known}")
