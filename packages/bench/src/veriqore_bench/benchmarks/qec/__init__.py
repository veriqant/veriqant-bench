"""QEC diagnostics: memory benchmarks, MWPM decoding, criteria profiles.

Requires the [qec] extra (pymatching for decoding; stim for the validation
oracle). Stim is never a QPUAdapter — it is the calculator we check
ourselves against, not a measurement target.
"""

from .baseline import MeasuredIdleBaseline, PhysicalBaseline, analytic_baseline
from .decoding import decode_counts, decoder_info
from .memory import (
    RepetitionMemory,
    RepetitionParams,
    SurfaceMemory,
    SurfaceParams,
    error_per_round,
)
from .schedule import MemorySchedule, repetition_memory, surface3_memory

__all__ = [
    "MeasuredIdleBaseline",
    "MemorySchedule",
    "PhysicalBaseline",
    "RepetitionMemory",
    "RepetitionParams",
    "SurfaceMemory",
    "SurfaceParams",
    "analytic_baseline",
    "decode_counts",
    "decoder_info",
    "error_per_round",
    "repetition_memory",
    "surface3_memory",
]
