"""Shared fixtures built on the package's deterministic reference QPR."""

from __future__ import annotations

from typing import Any

import pytest

from veriqore_bench.qpr import QuantumPerformanceRecord, content_sha256
from veriqore_bench.qpr.example import example_record


def reseal_document(document: dict[str, Any]) -> dict[str, Any]:
    """Recompute a raw document's content hash after a deliberate mutation,
    so tests can isolate non-integrity verification checks."""
    document["integrity"]["content_sha256"] = content_sha256(document)
    return document


@pytest.fixture
def record() -> QuantumPerformanceRecord:
    return example_record()
