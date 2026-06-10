"""The Alice & Bob five-criteria logical-qubit profile (June 2026).

Citation: Alice & Bob, "Defining the Logical Qubit: Five Criteria to
Benchmark Logical Qubit Claims", June 2026. Veriqant implements this
profile as a neutral executor of the published criteria; the thresholds and
their interpretation below follow the publication, with every numeric
choice surfaced in verdict evidence.

Dependency rule (criterion 3): an experiment with fewer QEC cycles than the
code distance produces invalid logical error rates, so breakeven,
scalable_parameters, and utility_timescales become not_evaluable when
sufficient_cycles fails — they are not allowed to render verdicts from
invalid evidence.
"""

from __future__ import annotations

from typing import ClassVar

from .framework import CriteriaProfile, Criterion, QECEvidence, Verdict

UTILITY_THRESHOLD_ROUNDS = 1_000_000
"""Rounds of sustained operation regarded as application-relevant in v1.
Memory experiments at current scales sit far below this; the criterion then
reports not_evaluable rather than pretending memory data answers it."""

UTILITY_ERROR_BUDGET = 1e-9
"""Logical error per round consistent with utility-scale workloads."""


class Breakeven(Criterion):
    id = "breakeven"
    description = (
        "Logical error rate beats the best constituent physical qubit "
        "(ci_upper(logical) < physical baseline at the largest distance)."
    )

    def evaluate(self, evidence: QECEvidence) -> Verdict:
        if evidence.physical_baseline is None:
            return self.not_evaluable(
                "no physical baseline available (ideal simulation has no "
                "meaningful physical comparator)"
            )
        best = max(evidence.distances, key=lambda d: d.distance)
        baseline = float(evidence.physical_baseline["error_per_round"])
        logical = best.logical_error_per_round
        passed = logical.ci_upper < baseline
        return Verdict(
            criterion=self.id,
            status="pass" if passed else "fail",
            evidence={
                "distance": best.distance,
                "logical_error_per_round": logical.model_dump(),
                "physical_baseline_per_round": baseline,
                "baseline_type": evidence.physical_baseline["baseline_type"],
                "baseline_detail": evidence.physical_baseline.get("detail", {}),
                "rule": "ci_upper(logical) < baseline",
            },
        )


class ScalableParameters(Criterion):
    id = "scalable_parameters"
    description = (
        "Logical error rate decreases with code distance: every Lambda "
        "suppression step significantly > 1 (ci_lower > 1)."
    )

    def evaluate(self, evidence: QECEvidence) -> Verdict:
        if len(evidence.distances) < 2:
            return self.not_evaluable(
                "needs at least two code distances to assess scaling; "
                f"this experiment ran only d={evidence.distances[0].distance}"
            )
        if not evidence.lambda_steps:
            return self.not_evaluable("no Lambda suppression data provided")
        unresolved = [step for step in evidence.lambda_steps if not step.resolved]
        if unresolved:
            return self.not_evaluable(
                "zero logical errors observed at adjacent distances ("
                + ", ".join(f"d{step.from_distance}->d{step.to_distance}" for step in unresolved)
                + "): suppression is unresolved at this shot count, not demonstrated",
                unresolved_steps=[step.model_dump() for step in unresolved],
            )
        passed = all(step.ci_lower > 1.0 for step in evidence.lambda_steps)
        return Verdict(
            criterion=self.id,
            status="pass" if passed else "fail",
            evidence={
                "lambda_steps": [step.model_dump() for step in evidence.lambda_steps],
                "rule": "ci_lower(Lambda) > 1 for every distance step",
            },
        )


class SufficientCycles(Criterion):
    id = "sufficient_cycles"
    description = "Syndrome-extraction rounds >= code distance (N >= d)."

    def evaluate(self, evidence: QECEvidence) -> Verdict:
        violations = [
            {"distance": d.distance, "rounds": d.rounds}
            for d in evidence.distances
            if d.rounds < d.distance
        ]
        return Verdict(
            criterion=self.id,
            status="fail" if violations else "pass",
            reason=(
                "rounds < distance: logical error rates from this experiment are not valid evidence"
                if violations
                else None
            ),
            evidence={
                "per_distance": [
                    {"distance": d.distance, "rounds": d.rounds} for d in evidence.distances
                ],
                "violations": violations,
                "rule": "rounds >= distance",
            },
        )


class AllRunsCounted(Criterion):
    id = "all_runs_counted"
    description = "No post-selection: every submitted shot is analyzed."

    def evaluate(self, evidence: QECEvidence) -> Verdict:
        accounting = evidence.post_selection
        fraction = accounting.fraction_discarded
        return Verdict(
            criterion=self.id,
            status="pass" if fraction == 0.0 else "fail",
            reason=None if fraction == 0.0 else f"{fraction:.3%} of shots discarded",
            evidence={
                "shots_submitted": accounting.shots_submitted,
                "shots_analyzed": accounting.shots_analyzed,
                "post_selection_fraction": fraction,
                "rule": "post_selection_fraction == 0.0",
            },
        )


class UtilityTimescales(Criterion):
    id = "utility_timescales"
    description = (
        "Sustained operation at application-relevant timescales "
        f"(>= {UTILITY_THRESHOLD_ROUNDS} rounds at error budget "
        f"{UTILITY_ERROR_BUDGET})."
    )

    def evaluate(self, evidence: QECEvidence) -> Verdict:
        max_rounds = max(d.rounds for d in evidence.distances)
        if max_rounds < UTILITY_THRESHOLD_ROUNDS:
            return self.not_evaluable(
                f"memory experiment ran {max_rounds} rounds, far below the "
                f"utility threshold of {UTILITY_THRESHOLD_ROUNDS} rounds; "
                "this experiment cannot speak to application timescales",
                threshold_rounds=UTILITY_THRESHOLD_ROUNDS,
                observed_rounds=max_rounds,
            )
        best = max(evidence.distances, key=lambda d: d.distance)
        passed = best.logical_error_per_round.ci_upper < UTILITY_ERROR_BUDGET
        return Verdict(
            criterion=self.id,
            status="pass" if passed else "fail",
            evidence={
                "threshold_rounds": UTILITY_THRESHOLD_ROUNDS,
                "error_budget": UTILITY_ERROR_BUDGET,
                "observed_rounds": max_rounds,
                "logical_error_per_round": best.logical_error_per_round.model_dump(),
            },
        )


class AbLq2026Profile(CriteriaProfile):
    """Alice & Bob five-criteria logical-qubit framework (June 2026)."""

    id = "ab-lq-2026"
    version = "1.0.0"
    citation = (
        'Alice & Bob, "Defining the Logical Qubit: Five Criteria to '
        'Benchmark Logical Qubit Claims", June 2026.'
    )
    criteria: ClassVar[list[type[Criterion]]] = [
        SufficientCycles,
        AllRunsCounted,
        Breakeven,
        ScalableParameters,
        UtilityTimescales,
    ]
    dependencies: ClassVar[dict[str, list[str]]] = {
        "breakeven": ["sufficient_cycles"],
        "scalable_parameters": ["sufficient_cycles"],
        "utility_timescales": ["sufficient_cycles"],
    }
