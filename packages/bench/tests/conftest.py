"""Shared fixtures: the deterministic reference QPR and a fake adapter."""

from __future__ import annotations

import re
from typing import Any

import pytest

from veriqore_bench.adapters import (
    CalibrationSnapshot,
    CostEstimate,
    DeviceCapabilities,
    JobSpec,
    LocalAdapterBase,
)
from veriqore_bench.qpr import QuantumPerformanceRecord, content_sha256
from veriqore_bench.qpr.example import example_record


def reseal_document(document: dict[str, Any]) -> dict[str, Any]:
    """Recompute a raw document's content hash after a deliberate mutation,
    so tests can isolate non-integrity verification checks."""
    document["integrity"]["content_sha256"] = content_sha256(document)
    return document


class StaticAdapter(LocalAdapterBase):
    """Deterministic fake backend: every circuit always measures all-zeros."""

    name = "static_test"
    adapter_version = "1.2.3"

    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            device_name="static_device",
            num_qubits=4,
            native_gates=["h", "cx"],
            is_simulator=True,
        )

    def calibration_snapshot(self) -> CalibrationSnapshot | None:
        return None

    def estimate_cost(self, spec: JobSpec) -> CostEstimate:
        return CostEstimate.free()

    def _prepare(self, spec: JobSpec) -> Any:
        return spec.circuits

    def _execute(self, prepared: Any, spec: JobSpec) -> tuple[list[dict[str, int]], dict[str, Any]]:
        counts = []
        for source in prepared:
            match = re.search(r"qubit\[(\d+)\]", source)
            width = int(match.group(1)) if match else 1
            counts.append({"0" * width: spec.shots})
        return counts, {"sdk_versions": {"static": "1.2.3"}}


@pytest.fixture
def record() -> QuantumPerformanceRecord:
    return example_record()
