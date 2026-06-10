"""Adapter discovery and construction.

Adapters register through the 'veriqant_bench.adapters' entry-point group —
the built-in ones in this package's own pyproject, third-party ones in
theirs, with no changes to veriqant-bench. An adapter whose dependencies are
missing (extras not installed) is reported as unavailable with an install
hint rather than raising at discovery time.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from .errors import AdapterUnavailableError
from .protocol import QPUAdapter

ENTRY_POINT_GROUP = "veriqant_bench.adapters"

_INSTALL_HINTS = {
    "aer_simulator": "pip install 'veriqant-bench[local]'",
    "braket_local": "pip install 'veriqant-bench[braket]'",
}


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    description: str
    available: bool
    install_hint: str | None = None
    error: str | None = None


def _discover() -> list[EntryPoint]:
    return sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda ep: ep.name)


def _hint_for(name: str) -> str:
    return _INSTALL_HINTS.get(name, f"install the package providing adapter '{name}'")


def list_adapters() -> list[AdapterInfo]:
    """Every registered adapter, with availability status."""
    infos: list[AdapterInfo] = []
    for entry_point in _discover():
        try:
            factory = entry_point.load()
        except ImportError as exc:
            infos.append(
                AdapterInfo(
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
            AdapterInfo(
                name=entry_point.name,
                description=doc[0] if doc else "",
                available=True,
            )
        )
    return infos


def get(name: str, **kwargs: Any) -> QPUAdapter:
    """Instantiate a registered adapter by name.

    Raises AdapterUnavailableError when the name is unknown or its
    dependencies are not installed.
    """
    for entry_point in _discover():
        if entry_point.name != name:
            continue
        try:
            factory = entry_point.load()
        except ImportError as exc:
            raise AdapterUnavailableError(
                f"adapter '{name}' is registered but not installed; {_hint_for(name)}"
            ) from exc
        adapter = factory(**kwargs)
        if not isinstance(adapter, QPUAdapter):
            raise AdapterUnavailableError(
                f"adapter '{name}' does not satisfy the QPUAdapter protocol"
            )
        return adapter
    known = ", ".join(info.name for info in list_adapters()) or "<none>"
    raise AdapterUnavailableError(f"unknown adapter '{name}'; registered adapters: {known}")
