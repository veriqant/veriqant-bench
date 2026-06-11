"""The Module-2 conformance suite against the live adapters' fake
transports (CI path) and, behind `pytest --live-conformance`, against real
devices (manual, quota/money-consuming — see docs/LIVE.md)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest
from conftest import make_braket_adapter, make_ibm_adapter

from veriqant_bench.adapters import QPUAdapter
from veriqant_bench.adapters.conformance import AdapterConformanceSuite


class TestIBMRuntimeFakeConformance(AdapterConformanceSuite):
    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path: Path) -> None:
        self._tmp_path = tmp_path

    def make_adapter(self) -> QPUAdapter:
        adapter, _ = make_ibm_adapter(self._tmp_path)
        return cast(QPUAdapter, adapter)


class TestBraketAwsFakeConformance(AdapterConformanceSuite):
    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path: Path, fake_aws_credentials: None) -> None:
        self._tmp_path = tmp_path

    def make_adapter(self) -> QPUAdapter:
        return cast(QPUAdapter, make_braket_adapter(self._tmp_path))


@pytest.mark.live_conformance
@pytest.mark.slow
@pytest.mark.timeout(14_400)
class TestIBMRuntimeLiveConformance(AdapterConformanceSuite):
    """Runs ONLY with `pytest --live-conformance`: real queue, real quota.

    Requires QISKIT_IBM_TOKEN (or a saved account) and a limits file
    permitting roughly a minute of QPU time. Never run by CI."""

    def make_adapter(self) -> QPUAdapter:
        from veriqant_bench.adapters.ibm import IBMRuntimeAdapter

        return cast(QPUAdapter, IBMRuntimeAdapter(allow_live=True))


@pytest.mark.live_conformance
@pytest.mark.slow
@pytest.mark.timeout(14_400)
class TestBraketLiveConformance(AdapterConformanceSuite):
    """Runs ONLY with `pytest --live-conformance` and a device ARN in
    VERIQANT_LIVE_BRAKET_ARN; spends real money within your limits. The env
    var selects the device only — it cannot enable live mode (allow_live is
    set explicitly here, and the cost gate still applies). This run is what
    confirms the Braket bit-order reversal on real hardware (Q3): no
    Braket-sourced QPR may be published before it has passed."""

    def make_adapter(self) -> QPUAdapter:
        from veriqant_bench.adapters.braket_aws import BraketAdapter

        arn = os.environ.get("VERIQANT_LIVE_BRAKET_ARN")
        if not arn:
            pytest.skip("VERIQANT_LIVE_BRAKET_ARN not set")
        return cast(QPUAdapter, BraketAdapter(arn, allow_live=True))
