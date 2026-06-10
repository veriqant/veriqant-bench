"""Decoding against hand-checkable cases."""

from __future__ import annotations

from veriqant_bench.benchmarks.qec.decoding import decode_counts, decoder_info
from veriqant_bench.benchmarks.qec.memory import error_per_round
from veriqant_bench.benchmarks.qec.schedule import repetition_memory, surface3_memory


def bitstring_with(num_clbits: int, ones: set[int]) -> str:
    """QPR-convention bitstring (rightmost char = clbit 0)."""
    return "".join("1" if (num_clbits - 1 - pos) in ones else "0" for pos in range(num_clbits))


def test_no_errors_decodes_clean() -> None:
    schedule = repetition_memory(3, 3)
    counts = {bitstring_with(schedule.num_clbits, set()): 100}
    errors, shots = decode_counts(schedule, counts)
    assert (errors, shots) == (0, 100)


def test_single_final_readout_error_is_corrected() -> None:
    schedule = repetition_memory(3, 3)
    final_base = 3 * 2
    # data 0 final readout flipped: fires only its boundary detector; the
    # decoder must predict the observable flip and cancel it.
    flipped = bitstring_with(schedule.num_clbits, {final_base + 0})
    errors, _ = decode_counts(schedule, {flipped: 50})
    assert errors == 0


def test_mid_run_data_error_is_corrected() -> None:
    schedule = repetition_memory(3, 3)
    # X on data 0 after round 0: syndromes of ancilla 0 flip from round 1 on,
    # and the final data-0 readout is flipped.
    final_base = 3 * 2
    ones = {1 * 2 + 0, 2 * 2 + 0} | {final_base + 0}
    flipped = bitstring_with(schedule.num_clbits, ones)
    errors, _ = decode_counts(schedule, {flipped: 10})
    assert errors == 0


def test_logical_flip_is_an_error() -> None:
    schedule = repetition_memory(3, 3)
    final_base = 3 * 2
    # All data finals flipped = a logical operator: no detector fires, the
    # observable is flipped, and no decoder can (or should) fix it.
    flipped = bitstring_with(schedule.num_clbits, {final_base, final_base + 1, final_base + 2})
    errors, shots = decode_counts(schedule, {flipped: 25})
    assert (errors, shots) == (25, 25)


def test_surface_clean_and_logical_cases() -> None:
    schedule = surface3_memory(3, "z")
    clean = bitstring_with(schedule.num_clbits, set())
    assert decode_counts(schedule, {clean: 40}) == (0, 40)

    final_base = 3 * 8
    # Logical X = X on the left column {0,3,6}: every Z stabilizer overlaps
    # it evenly (silent syndrome), but the Z readout {0,1,2} overlaps it in
    # exactly qubit 0 — an undetectable, uncorrectable logical error.
    logical = bitstring_with(schedule.num_clbits, {final_base + q for q in (0, 3, 6)})
    errors, shots = decode_counts(schedule, {logical: 10, clean: 30})
    assert (errors, shots) == (10, 40)


def test_mixed_counts_are_fully_accounted() -> None:
    schedule = repetition_memory(3, 3)
    clean = bitstring_with(schedule.num_clbits, set())
    final_base = 3 * 2
    logical = bitstring_with(schedule.num_clbits, {final_base, final_base + 1, final_base + 2})
    errors, shots = decode_counts(schedule, {clean: 70, logical: 30})
    assert (errors, shots) == (30, 100)


def test_error_per_round_conversion() -> None:
    assert error_per_round(0.0, 5) == 0.0
    # One round: identity.
    assert abs(error_per_round(0.1, 1) - 0.1) < 1e-12
    # Saturated input is capped, not propagated as nonsense.
    assert error_per_round(0.7, 5) <= 0.5


def test_decoder_identity_is_versioned() -> None:
    info = decoder_info()
    assert info["name"] == "pymatching-mwpm"
    assert info["version"]
    assert "hook" in info["graph"]
