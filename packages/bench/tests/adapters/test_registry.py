from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest

from veriqore_bench.adapters import AdapterUnavailableError, QPUAdapter, get, list_adapters
from veriqore_bench.adapters import registry as registry_module
from veriqore_bench.adapters.aer import AerSimulatorAdapter


def test_builtin_adapters_are_discovered_and_available() -> None:
    by_name = {info.name: info for info in list_adapters()}
    assert by_name["aer_simulator"].available
    assert by_name["braket_local"].available
    assert "Aer" in by_name["aer_simulator"].description


def test_get_constructs_a_protocol_satisfying_adapter() -> None:
    adapter = get("aer_simulator")
    assert isinstance(adapter, AerSimulatorAdapter)
    assert isinstance(adapter, QPUAdapter)


def test_get_unknown_adapter_lists_known_names() -> None:
    with pytest.raises(AdapterUnavailableError, match="aer_simulator"):
        get("does_not_exist")


def test_uninstalled_adapter_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    ghost = EntryPoint(
        name="ghost",
        value="nonexistent_package:GhostAdapter",
        group=registry_module.ENTRY_POINT_GROUP,
    )
    monkeypatch.setattr(registry_module, "_discover", lambda: [ghost])
    infos = list_adapters()
    assert len(infos) == 1
    assert not infos[0].available
    assert infos[0].install_hint is not None
    assert infos[0].error is not None

    with pytest.raises(AdapterUnavailableError, match="not installed"):
        get("ghost")


def test_known_name_with_missing_extra_gets_specific_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = EntryPoint(
        name="aer_simulator",
        value="nonexistent_package:Adapter",
        group=registry_module.ENTRY_POINT_GROUP,
    )
    monkeypatch.setattr(registry_module, "_discover", lambda: [broken])
    info = list_adapters()[0]
    assert info.install_hint == "pip install 'veriqore-bench[local]'"


def test_factory_not_satisfying_protocol_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    impostor = EntryPoint(
        name="impostor", value="builtins:dict", group=registry_module.ENTRY_POINT_GROUP
    )
    monkeypatch.setattr(registry_module, "_discover", lambda: [impostor])
    with pytest.raises(AdapterUnavailableError, match="does not satisfy"):
        get("impostor")
