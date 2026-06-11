def test_unknown_criteria_profile_fails_at_generate_time() -> None:
    """generate() runs before execution and before a live cost gate; a
    typo'd profile id must refuse there, never after shots are spent."""
    import pytest

    from veriqant_bench.benchmarks.qec.criteria.framework import ProfileUnavailableError
    from veriqant_bench.benchmarks.qec.memory import (
        RepetitionMemory,
        RepetitionParams,
        SurfaceMemory,
        SurfaceParams,
    )

    with pytest.raises(ProfileUnavailableError, match="no-such-profile"):
        RepetitionMemory().generate(
            RepetitionParams(distances=[3], rounds=3, criteria="no-such-profile"), seed=1
        )
    with pytest.raises(ProfileUnavailableError, match="no-such-profile"):
        SurfaceMemory().generate(
            SurfaceParams(distance=3, rounds=3, criteria="no-such-profile"), seed=1
        )
