"""Property test: arbitrary valid NoiseSpecs survive serialization."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from veriqant_bench.adapters import NoiseSpec

RATE = st.floats(min_value=0.0, max_value=0.99, allow_nan=False)
READOUT = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
GATE_TIME = st.floats(min_value=1.0, max_value=10_000.0, allow_nan=False)


@st.composite
def noise_specs(draw: st.DrawFn) -> NoiseSpec:
    t1: float | None = None
    t2: float | None = None
    if draw(st.booleans()):
        t1 = draw(st.floats(min_value=0.1, max_value=10_000.0, allow_nan=False))
        # Stay within the physical bound t2 <= 2*t1.
        t2 = t1 * draw(st.floats(min_value=0.01, max_value=2.0, allow_nan=False))
    return NoiseSpec(
        depolarizing_1q=draw(RATE),
        depolarizing_2q=draw(RATE),
        readout_error_0to1=draw(READOUT),
        readout_error_1to0=draw(READOUT),
        t1_us=t1,
        t2_us=t2,
        gate_time_1q_ns=draw(GATE_TIME),
        gate_time_2q_ns=draw(GATE_TIME),
    )


@given(spec=noise_specs())
def test_noise_spec_round_trips_through_json(spec: NoiseSpec) -> None:
    restored = NoiseSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec
    assert restored.model_dump_json() == spec.model_dump_json()
