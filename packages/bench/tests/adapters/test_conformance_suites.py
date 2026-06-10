"""The shared conformance suite, run against every built-in adapter
(ideal and noisy) — the same suite third-party adapter authors import."""

from __future__ import annotations

from veriqant_bench.adapters import NoiseSpec, QPUAdapter
from veriqant_bench.adapters.aer import AerSimulatorAdapter
from veriqant_bench.adapters.braket_local import BraketLocalAdapter
from veriqant_bench.adapters.conformance import AdapterConformanceSuite


class TestAerConformance(AdapterConformanceSuite):
    def make_adapter(self) -> QPUAdapter:
        return AerSimulatorAdapter()


class TestNoisyAerConformance(AdapterConformanceSuite):
    """A noisy backend must satisfy the identical contract."""

    def make_adapter(self) -> QPUAdapter:
        return AerSimulatorAdapter(
            noise=NoiseSpec(depolarizing_1q=0.01, depolarizing_2q=0.02, readout_error_0to1=0.01)
        )


class TestBraketLocalConformance(AdapterConformanceSuite):
    def make_adapter(self) -> QPUAdapter:
        return BraketLocalAdapter()
