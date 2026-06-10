"""Criteria-profile framework: pluggable, versioned logical-qubit criteria.

Veriqant is the neutral executor of *others'* published norms: a
CriteriaProfile is a named, cited set of Criterion evaluators discovered
through the 'veriqant_bench.criteria_profiles' entry-point group, never a
definition of our own. Verdicts are pass / fail / not_evaluable —
not_evaluable is a first-class outcome with a reason: an honest "this
experiment cannot answer that" beats a forced verdict.

Evidence is a plain data model deliberately decoupled from our run driver,
so externally published datasets can be evaluated later without new code
here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

VerdictStatus = Literal["pass", "fail", "not_evaluable"]

SIMULATED_ISSUE = "simulated_noise_model_not_hardware"

ENTRY_POINT_GROUP = "veriqant_bench.criteria_profiles"


class RateWithCI(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float
    ci_lower: float
    ci_upper: float
    confidence_level: float = 0.95


class DistanceEvidence(BaseModel):
    """Per-distance measurement evidence."""

    model_config = ConfigDict(extra="forbid")

    distance: int
    rounds: int
    shots: int
    logical_errors: int
    logical_error_per_round: RateWithCI


class LambdaStep(BaseModel):
    """Error-suppression factor between consecutive distances."""

    model_config = ConfigDict(extra="forbid")

    from_distance: int
    to_distance: int
    value: float
    ci_lower: float
    ci_upper: float
    resolved: bool = True
    """False when zero errors were observed at both distances: the data
    bounds each rate but cannot resolve their ratio at this shot count."""


class PostSelectionAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shots_submitted: int
    shots_analyzed: int

    @property
    def fraction_discarded(self) -> float:
        if self.shots_submitted == 0:
            return 0.0
        return 1.0 - self.shots_analyzed / self.shots_submitted


class QECEvidence(BaseModel):
    """Everything a criteria profile may judge. Need not come from our own
    run driver — external datasets map onto this same model."""

    model_config = ConfigDict(extra="forbid")

    code: str
    basis: str
    distances: list[DistanceEvidence] = Field(min_length=1)
    lambda_steps: list[LambdaStep] = Field(default_factory=list)
    physical_baseline: dict[str, Any] | None = None
    """PhysicalBaseline dump: error_per_round, baseline_type, detail."""
    post_selection: PostSelectionAccounting
    simulated: bool
    """True when the data comes from a simulated noise model. Verdicts then
    carry the SIMULATED_ISSUE flag — never confusable with hardware claims."""
    noise_summary: dict[str, Any] | None = None


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str
    status: VerdictStatus
    reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    """The numbers, CIs, and thresholds that produced this verdict."""


class Criterion(ABC):
    """One evaluator within a profile."""

    id: ClassVar[str]
    description: ClassVar[str]

    @abstractmethod
    def evaluate(self, evidence: QECEvidence) -> Verdict: ...

    def not_evaluable(self, reason: str, **extra: Any) -> Verdict:
        return Verdict(criterion=self.id, status="not_evaluable", reason=reason, evidence=extra)


class CriteriaProfile(ABC):
    """A named, versioned, cited set of criteria."""

    id: ClassVar[str]
    version: ClassVar[str]
    citation: ClassVar[str]
    criteria: ClassVar[list[type[Criterion]]]
    dependencies: ClassVar[dict[str, list[str]]] = {}
    """criterion id -> prerequisite criterion ids. When a prerequisite
    fails, the dependent verdict becomes not_evaluable."""

    def evaluate(self, evidence: QECEvidence) -> list[Verdict]:
        verdicts: dict[str, Verdict] = {}
        for criterion_cls in self.criteria:
            criterion = criterion_cls()
            failed_prerequisites = [
                prerequisite
                for prerequisite in self.dependencies.get(criterion.id, [])
                if verdicts.get(prerequisite) is not None
                and verdicts[prerequisite].status == "fail"
            ]
            if failed_prerequisites:
                verdicts[criterion.id] = criterion.not_evaluable(
                    "prerequisite criterion failed: "
                    + ", ".join(failed_prerequisites)
                    + " (error rates from this experiment are not valid evidence)",
                    failed_prerequisites=failed_prerequisites,
                )
                continue
            verdicts[criterion.id] = criterion.evaluate(evidence)
        return [verdicts[criterion_cls.id] for criterion_cls in self.criteria]


@dataclass(frozen=True)
class ProfileInfo:
    id: str
    description: str
    available: bool
    error: str | None = None


class ProfileUnavailableError(RuntimeError):
    """The requested criteria profile is unknown or failed to load."""


def _discover() -> list[EntryPoint]:
    return sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda ep: ep.name)


def list_profiles() -> list[ProfileInfo]:
    infos: list[ProfileInfo] = []
    for entry_point in _discover():
        try:
            profile_cls = entry_point.load()
        except ImportError as exc:
            infos.append(ProfileInfo(entry_point.name, "", available=False, error=str(exc)))
            continue
        doc = (profile_cls.__doc__ or "").strip().splitlines()
        infos.append(ProfileInfo(entry_point.name, doc[0] if doc else "", available=True))
    return infos


def get_profile(profile_id: str) -> CriteriaProfile:
    for entry_point in _discover():
        if entry_point.name != profile_id:
            continue
        try:
            profile_cls = entry_point.load()
        except ImportError as exc:
            raise ProfileUnavailableError(
                f"criteria profile '{profile_id}' is registered but failed to load: {exc}"
            ) from exc
        profile = profile_cls()
        if not isinstance(profile, CriteriaProfile):
            raise ProfileUnavailableError(f"'{profile_id}' does not implement CriteriaProfile")
        return profile
    known = ", ".join(info.id for info in list_profiles()) or "<none>"
    raise ProfileUnavailableError(
        f"unknown criteria profile '{profile_id}'; registered profiles: {known}"
    )
