"""MWPM decoding of memory-experiment outcomes (PyMatching).

decode_counts() is a pure function from measured counts to logical error
counts. The decoder's identity, version, and graph construction are
reported via decoder_info() and recorded verbatim in every QEC QPR — a
logical error rate is a property of (device x decoder), and longitudinal
comparability breaks if the decoder changes invisibly.
"""

from __future__ import annotations

from importlib.metadata import version
from typing import Any

import numpy as np
from pymatching import Matching

from .schedule import MemorySchedule

DECODER_NAME = "pymatching-mwpm"
GRAPH_DESCRIPTION = "phenomenological, uniform weights (hook errors not modeled)"


def decoder_info() -> dict[str, Any]:
    """Decoder identity for QPR provenance."""
    return {
        "name": DECODER_NAME,
        "version": version("pymatching"),
        "graph": GRAPH_DESCRIPTION,
    }


def build_matching(schedule: MemorySchedule) -> Matching:
    # Parallel edges occur when two boundary data qubits feed the same
    # stabilizer; in these schedules parallel edges always carry identical
    # fault flags, so smallest-weight merging loses nothing.
    matching = Matching()
    for edge in schedule.edges:
        fault_ids = {0} if edge.flips_observable else set()
        if edge.detector_b is None:
            matching.add_boundary_edge(
                edge.detector_a,
                fault_ids=fault_ids,
                weight=1.0,
                merge_strategy="smallest-weight",
            )
        else:
            matching.add_edge(
                edge.detector_a,
                edge.detector_b,
                fault_ids=fault_ids,
                weight=1.0,
                merge_strategy="smallest-weight",
            )
    return matching


def detection_matrix(schedule: MemorySchedule) -> np.ndarray:
    """(n_detectors, n_clbits) parity-check map from raw bits to detectors."""
    matrix = np.zeros((len(schedule.detectors), schedule.num_clbits), dtype=np.uint8)
    for index, clbits in enumerate(schedule.detectors):
        for clbit in clbits:
            matrix[index, clbit] ^= 1
    return matrix


def bitstrings_to_array(bitstrings: list[str], num_clbits: int) -> np.ndarray:
    """QPR bitstrings (rightmost char = clbit 0) -> (n, n_clbits) uint8."""
    rows = np.zeros((len(bitstrings), num_clbits), dtype=np.uint8)
    for row, bitstring in enumerate(bitstrings):
        padded = bitstring.zfill(num_clbits)
        for clbit in range(num_clbits):
            if padded[num_clbits - 1 - clbit] == "1":
                rows[row, clbit] = 1
    return rows


def decode_bits(
    schedule: MemorySchedule, bits: np.ndarray, matching: Matching | None = None
) -> np.ndarray:
    """Per-row logical-error flags for raw measurement rows."""
    matching = matching or build_matching(schedule)
    events = (bits @ detection_matrix(schedule).T) % 2
    predicted = matching.decode_batch(events.astype(np.uint8))
    predicted_flips = np.asarray(predicted, dtype=np.uint8).reshape(len(bits), -1)[:, 0]
    observed = bits[:, schedule.observable_clbits].sum(axis=1) % 2
    return np.asarray(observed.astype(np.uint8) ^ predicted_flips, dtype=bool)


def decode_counts(schedule: MemorySchedule, counts: dict[str, int]) -> tuple[int, int]:
    """(logical errors, total shots) for measured counts."""
    bitstrings = sorted(counts)
    weights = np.array([counts[bitstring] for bitstring in bitstrings], dtype=np.int64)
    errors = decode_bits(schedule, bitstrings_to_array(bitstrings, schedule.num_clbits))
    return int(weights[errors].sum()), int(weights.sum())
