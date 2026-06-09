"""Criteria framework + ab-lq-2026 profile against hand-built evidence."""

from __future__ import annotations

import pytest

from veriqore_bench.benchmarks.qec.criteria import (
    DistanceEvidence,
    LambdaStep,
    PostSelectionAccounting,
    ProfileUnavailableError,
    QECEvidence,
    RateWithCI,
    get_profile,
    list_profiles,
)
from veriqore_bench.benchmarks.qec.criteria.ab_lq_2026 import AbLq2026Profile


def rate(value: float, half_width: float = 0.0005) -> RateWithCI:
    return RateWithCI(
        value=value, ci_lower=max(0.0, value - half_width), ci_upper=value + half_width
    )


def distance(d: int, rounds: int, eps: float, shots: int = 10_000) -> DistanceEvidence:
    return DistanceEvidence(
        distance=d,
        rounds=rounds,
        shots=shots,
        logical_errors=int(eps * shots),
        logical_error_per_round=rate(eps),
    )


def evidence(
    *,
    distances: list[DistanceEvidence],
    lambda_steps: list[LambdaStep] | None = None,
    baseline: float | None = 0.01,
    discarded: int = 0,
    simulated: bool = True,
) -> QECEvidence:
    submitted = sum(d.shots for d in distances)
    return QECEvidence(
        code="repetition",
        basis="z",
        distances=distances,
        lambda_steps=lambda_steps or [],
        physical_baseline=(
            None
            if baseline is None
            else {
                "error_per_round": baseline,
                "baseline_type": "analytic_noise_spec",
                "detail": {},
            }
        ),
        post_selection=PostSelectionAccounting(
            shots_submitted=submitted, shots_analyzed=submitted - discarded
        ),
        simulated=simulated,
    )


def step(a: int, b: int, value: float, lower: float, upper: float) -> LambdaStep:
    return LambdaStep(from_distance=a, to_distance=b, value=value, ci_lower=lower, ci_upper=upper)


PROFILE = AbLq2026Profile()


def verdicts_by_id(qec_evidence: QECEvidence) -> dict[str, str]:
    return {v.criterion: v.status for v in PROFILE.evaluate(qec_evidence)}


def test_all_pass_scenario_except_utility() -> None:
    statuses = verdicts_by_id(
        evidence(
            distances=[distance(3, 7, 0.004), distance(5, 7, 0.001)],
            lambda_steps=[step(3, 5, 4.0, 2.5, 6.0)],
        )
    )
    assert statuses["sufficient_cycles"] == "pass"
    assert statuses["all_runs_counted"] == "pass"
    assert statuses["breakeven"] == "pass"
    assert statuses["scalable_parameters"] == "pass"
    assert statuses["utility_timescales"] == "not_evaluable"


def test_breakeven_fails_when_logical_worse_than_physical() -> None:
    statuses = verdicts_by_id(evidence(distances=[distance(3, 7, 0.05)], baseline=0.01))
    assert statuses["breakeven"] == "fail"


def test_breakeven_not_evaluable_without_baseline() -> None:
    verdicts = {
        v.criterion: v
        for v in PROFILE.evaluate(evidence(distances=[distance(3, 7, 0.001)], baseline=None))
    }
    assert verdicts["breakeven"].status == "not_evaluable"
    assert "comparator" in (verdicts["breakeven"].reason or "")


def test_scalable_fails_when_lambda_not_significant() -> None:
    statuses = verdicts_by_id(
        evidence(
            distances=[distance(3, 7, 0.01), distance(5, 7, 0.009)],
            lambda_steps=[step(3, 5, 1.1, 0.8, 1.5)],  # CI straddles 1
        )
    )
    assert statuses["scalable_parameters"] == "fail"


def test_scalable_not_evaluable_with_single_distance() -> None:
    statuses = verdicts_by_id(evidence(distances=[distance(3, 7, 0.01)]))
    assert statuses["scalable_parameters"] == "not_evaluable"


def test_sufficient_cycles_failure_invalidates_dependents() -> None:
    verdicts = {
        v.criterion: v
        for v in PROFILE.evaluate(
            evidence(
                distances=[distance(5, 3, 0.001)],  # rounds < distance
                lambda_steps=[step(3, 5, 4.0, 2.0, 6.0)],
            )
        )
    }
    assert verdicts["sufficient_cycles"].status == "fail"
    for dependent in ("breakeven", "scalable_parameters", "utility_timescales"):
        assert verdicts[dependent].status == "not_evaluable"
        assert "prerequisite" in (verdicts[dependent].reason or "")
    # Independent criteria still get judged on their own merits.
    assert verdicts["all_runs_counted"].status == "pass"


def test_post_selection_fails_all_runs_counted() -> None:
    qec_evidence = evidence(distances=[distance(3, 7, 0.004)], discarded=50)
    verdicts = {v.criterion: v for v in PROFILE.evaluate(qec_evidence)}
    assert verdicts["all_runs_counted"].status == "fail"
    assert verdicts["all_runs_counted"].evidence["post_selection_fraction"] > 0


def test_verdict_evidence_carries_the_numbers() -> None:
    verdicts = {
        v.criterion: v
        for v in PROFILE.evaluate(
            evidence(
                distances=[distance(3, 7, 0.004), distance(5, 7, 0.001)],
                lambda_steps=[step(3, 5, 4.0, 2.5, 6.0)],
            )
        )
    }
    breakeven = verdicts["breakeven"].evidence
    assert breakeven["physical_baseline_per_round"] == 0.01
    assert breakeven["rule"] == "ci_upper(logical) < baseline"
    scalable = verdicts["scalable_parameters"].evidence
    assert scalable["lambda_steps"][0]["ci_lower"] == 2.5


def test_profile_registry() -> None:
    profiles = {info.id: info for info in list_profiles()}
    assert profiles["ab-lq-2026"].available
    profile = get_profile("ab-lq-2026")
    assert isinstance(profile, AbLq2026Profile)
    assert "Alice & Bob" in profile.citation
    with pytest.raises(ProfileUnavailableError, match="ab-lq-2026"):
        get_profile("nonexistent-profile")
