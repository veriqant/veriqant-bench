"""veriqant-bench — standardized, reproducible benchmark suites for quantum processors."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("veriqant-bench")
except PackageNotFoundError:  # pragma: no cover - only hit on broken installs
    __version__ = "0.0.0"
