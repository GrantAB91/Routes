"""Constraint definitions and evaluation.

The distinction this module exists to preserve is between three outcomes, not
two:

``SATISFIED``
    The route was checked against real data and complies.

``VIOLATED``
    The route was checked against real data and does not comply. The offending
    segments are named so the user can see and act on them (§7.12).

``UNEVALUABLE``
    The data needed to check was not available. This is *not* compliance. A
    route whose surface is unknown for 40% of its length has not been shown to
    meet "paved only"; reporting it as satisfied would be the exact failure
    §2.6 forbids, dressed up as a routing result.

Hard constraints that end ``UNEVALUABLE`` drive the route's verdict to
``SATISFIED_WITH_UNKNOWN_DATA`` rather than ``FULLY_SATISFIED`` (§7.11).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from ..models.enums import BicycleAccess, KnowledgeStatus, RoadClass, SurfaceFamily
from .model import RouteView, SegmentView


class Outcome(StrEnum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    # This route's data was insufficient: the check applies, but the facts
    # needed to run it are missing for these segments. Downgrades the verdict.
    UNEVALUABLE = "unevaluable"
    # The deployment cannot perform this check at all, for any route, because a
    # source is not connected. Reported as a standing limitation rather than
    # charged against each route: otherwise a missing hydrography layer makes
    # every route in the system report unknown data, which trains users to
    # ignore the one signal that should mean something.
    NOT_AVAILABLE = "not_available"


@dataclass(frozen=True, slots=True)
class ConstraintResult:
    key: str
    hard: bool
    outcome: Outcome
    # The user's own words for this requirement, echoed back in explanations.
    stated_as: str | None = None
    requested: float | str | None = None
    observed: float | str | None = None
    # Segments at fault, by index, so the map can highlight them.
    segment_indices: tuple[int, ...] = ()
    affected_distance_m: float = 0.0
    detail: str = ""

    @property
    def is_blocking(self) -> bool:
        """A hard constraint proven violated makes a route infeasible."""
        return self.hard and self.outcome is Outcome.VIOLATED


class Constraint(Protocol):
    key: str
    hard: bool
    stated_as: str | None

    def evaluate(self, route: RouteView) -> ConstraintResult: ...


@dataclass(frozen=True, slots=True)
class MaxGradient:
    """Maximum gradient anywhere on the route (§7.6).

    Segments with no elevation coverage cannot be checked. They are counted and
    reported as unevaluated rather than assumed compliant, because an unmeasured
    segment is exactly where a wall is most likely to hide.
    """

    limit_percent: float
    hard: bool = True
    stated_as: str | None = None
    key: str = "max_gradient_percent"

    def evaluate(self, route: RouteView) -> ConstraintResult:
        violating: list[SegmentView] = []
        unmeasured_m = 0.0
        steepest: float | None = None

        for segment in route.segments:
            if (
                segment.elevation_status is not KnowledgeStatus.KNOWN
                or segment.max_grade_percent is None
            ):
                unmeasured_m += segment.distance_m
                continue
            if steepest is None or segment.max_grade_percent > steepest:
                steepest = segment.max_grade_percent
            if segment.max_grade_percent > self.limit_percent:
                violating.append(segment)

        if violating:
            return ConstraintResult(
                key=self.key,
                hard=self.hard,
                outcome=Outcome.VIOLATED,
                stated_as=self.stated_as,
                requested=self.limit_percent,
                observed=steepest,
                segment_indices=tuple(s.index for s in violating),
                affected_distance_m=sum(s.distance_m for s in violating),
                detail=(
                    f"{len(violating)} segment(s) exceed {self.limit_percent:g}%, "
                    f"steepest {steepest:.1f}%"
                    if steepest is not None
                    else "gradient limit exceeded"
                ),
            )

        if unmeasured_m > 0:
            return ConstraintResult(
                key=self.key,
                hard=self.hard,
                outcome=Outcome.UNEVALUABLE,
                stated_as=self.stated_as,
                requested=self.limit_percent,
                observed=steepest,
                affected_distance_m=unmeasured_m,
                detail=(
                    f"{unmeasured_m / 1000:.1f} km has no elevation data, so the "
                    f"{self.limit_percent:g}% limit could not be checked there"
                ),
            )

        return ConstraintResult(
            key=self.key,
            hard=self.hard,
            outcome=Outcome.SATISFIED,
            stated_as=self.stated_as,
            requested=self.limit_percent,
            observed=steepest,
        )


@dataclass(frozen=True, slots=True)
class MaxSurfaceDistance:
    """Limit on distance over a given surface family (§7.4.5).

    Used for "no more than 20 km unpaved". Distance whose surface is unknown is
    *not* counted toward the limit — it is reported separately, because
    counting it either way is a claim about data that does not exist.
    """

    family: SurfaceFamily
    limit_m: float
    hard: bool = True
    stated_as: str | None = None
    key: str = "max_surface_distance_m"

    def evaluate(self, route: RouteView) -> ConstraintResult:
        matching = [
            s
            for s in route.segments
            if s.surface_status is KnowledgeStatus.KNOWN and s.surface_family is self.family
        ]
        observed = sum(s.distance_m for s in matching)
        unknown_m = route.distance_where(lambda s: s.surface_status is not KnowledgeStatus.KNOWN)

        if observed > self.limit_m:
            return ConstraintResult(
                key=f"max_{self.family.value}_distance_m",
                hard=self.hard,
                outcome=Outcome.VIOLATED,
                stated_as=self.stated_as,
                requested=self.limit_m,
                observed=observed,
                segment_indices=tuple(s.index for s in matching),
                affected_distance_m=observed,
                detail=(
                    f"{observed / 1000:.1f} km of {self.family.value} exceeds the "
                    f"{self.limit_m / 1000:.1f} km limit"
                ),
            )

        # Enough unknown surface could push it over the limit, so compliance is
        # not established.
        if unknown_m > 0 and observed + unknown_m > self.limit_m:
            return ConstraintResult(
                key=f"max_{self.family.value}_distance_m",
                hard=self.hard,
                outcome=Outcome.UNEVALUABLE,
                stated_as=self.stated_as,
                requested=self.limit_m,
                observed=observed,
                affected_distance_m=unknown_m,
                detail=(
                    f"{observed / 1000:.1f} km is known {self.family.value}, but "
                    f"{unknown_m / 1000:.1f} km has no surface data and could take "
                    f"it past the {self.limit_m / 1000:.1f} km limit"
                ),
            )

        return ConstraintResult(
            key=f"max_{self.family.value}_distance_m",
            hard=self.hard,
            outcome=Outcome.SATISFIED,
            stated_as=self.stated_as,
            requested=self.limit_m,
            observed=observed,
        )


@dataclass(frozen=True, slots=True)
class MaxUnknownSurfaceDistance:
    """Limit on how much route may have no surface data at all (§7.4.6).

    This is the constraint that lets a rider say "I will accept some gravel,
    but not a mystery". It is always evaluable: how much is unknown is itself
    known.
    """

    limit_m: float
    hard: bool = True
    stated_as: str | None = None
    key: str = "max_unknown_surface_distance_m"

    def evaluate(self, route: RouteView) -> ConstraintResult:
        unknown = [s for s in route.segments if s.surface_status is not KnowledgeStatus.KNOWN]
        observed = sum(s.distance_m for s in unknown)

        outcome = Outcome.VIOLATED if observed > self.limit_m else Outcome.SATISFIED
        return ConstraintResult(
            key=self.key,
            hard=self.hard,
            outcome=outcome,
            stated_as=self.stated_as,
            requested=self.limit_m,
            observed=observed,
            segment_indices=tuple(s.index for s in unknown) if outcome is Outcome.VIOLATED else (),
            affected_distance_m=observed,
            detail=(
                f"{observed / 1000:.1f} km has no surface data, above the "
                f"{self.limit_m / 1000:.1f} km tolerance"
                if outcome is Outcome.VIOLATED
                else ""
            ),
        )


@dataclass(frozen=True, slots=True)
class LegalBicycleAccess:
    """Every segment must permit bicycles (§7.4.1).

    Access is never inferred. A segment whose access is unknown is unevaluated,
    not permitted — Contour will not tell a rider a road is legal because
    nobody recorded that it is not.
    """

    hard: bool = True
    stated_as: str | None = None
    key: str = "legal_bicycle_access"

    FORBIDDEN = (BicycleAccess.NO, BicycleAccess.PRIVATE)

    def evaluate(self, route: RouteView) -> ConstraintResult:
        forbidden: list[SegmentView] = []
        unknown_m = 0.0

        for segment in route.segments:
            if segment.bicycle_access_status is not KnowledgeStatus.KNOWN:
                unknown_m += segment.distance_m
                continue
            if segment.bicycle_access in self.FORBIDDEN:
                forbidden.append(segment)

        if forbidden:
            return ConstraintResult(
                key=self.key,
                hard=self.hard,
                outcome=Outcome.VIOLATED,
                stated_as=self.stated_as,
                segment_indices=tuple(s.index for s in forbidden),
                affected_distance_m=sum(s.distance_m for s in forbidden),
                detail=f"{len(forbidden)} segment(s) do not permit bicycles",
            )

        if unknown_m > 0:
            return ConstraintResult(
                key=self.key,
                hard=self.hard,
                outcome=Outcome.UNEVALUABLE,
                stated_as=self.stated_as,
                affected_distance_m=unknown_m,
                detail=(
                    f"{unknown_m / 1000:.1f} km has no recorded bicycle access; "
                    "legality could not be confirmed there"
                ),
            )

        return ConstraintResult(key=self.key, hard=self.hard, outcome=Outcome.SATISFIED)


@dataclass(frozen=True, slots=True)
class ProhibitedRoadClasses:
    """Road classes the route must not use (§7.4.11).

    Defaults to motorways, which prohibit bicycles across the jurisdictions
    Contour covers, but the list is explicit rather than assumed so it can be
    corrected per region without touching the evaluator.
    """

    classes: frozenset[RoadClass] = frozenset({RoadClass.MOTORWAY})
    hard: bool = True
    stated_as: str | None = None
    key: str = "prohibited_road_classes"

    def evaluate(self, route: RouteView) -> ConstraintResult:
        offending = [
            s
            for s in route.segments
            if s.road_class_status is KnowledgeStatus.KNOWN and s.road_class in self.classes
        ]
        if offending:
            return ConstraintResult(
                key=self.key,
                hard=self.hard,
                outcome=Outcome.VIOLATED,
                stated_as=self.stated_as,
                segment_indices=tuple(s.index for s in offending),
                affected_distance_m=sum(s.distance_m for s in offending),
                detail=("route uses " + ", ".join(sorted({s.road_class.value for s in offending}))),
            )
        return ConstraintResult(key=self.key, hard=self.hard, outcome=Outcome.SATISFIED)


@dataclass(frozen=True, slots=True)
class FerryUse:
    """Ferries required, forbidden, or unconstrained (§7.4.4)."""

    allowed: bool = True
    required: bool = False
    hard: bool = True
    stated_as: str | None = None
    key: str = "ferry_use"

    def evaluate(self, route: RouteView) -> ConstraintResult:
        ferries = [s for s in route.segments if s.is_ferry]
        ferry_distance = sum(s.distance_m for s in ferries)

        if not self.allowed and ferries:
            return ConstraintResult(
                key=self.key,
                hard=self.hard,
                outcome=Outcome.VIOLATED,
                stated_as=self.stated_as,
                requested="no ferries",
                observed=ferry_distance,
                segment_indices=tuple(s.index for s in ferries),
                affected_distance_m=ferry_distance,
                detail=f"route uses {len(ferries)} ferry crossing(s)",
            )

        if self.required and not ferries:
            return ConstraintResult(
                key=self.key,
                hard=self.hard,
                outcome=Outcome.VIOLATED,
                stated_as=self.stated_as,
                requested="ferry required",
                observed=0.0,
                detail="route uses no ferry",
            )

        return ConstraintResult(
            key=self.key,
            hard=self.hard,
            outcome=Outcome.SATISFIED,
            stated_as=self.stated_as,
            observed=ferry_distance,
        )


@dataclass(frozen=True, slots=True)
class MaxTotalDistance:
    """Upper bound on route length (§7.4.7)."""

    limit_m: float
    hard: bool = True
    stated_as: str | None = None
    key: str = "max_total_distance_m"

    def evaluate(self, route: RouteView) -> ConstraintResult:
        observed = route.distance_m
        outcome = Outcome.VIOLATED if observed > self.limit_m else Outcome.SATISFIED
        return ConstraintResult(
            key=self.key,
            hard=self.hard,
            outcome=outcome,
            stated_as=self.stated_as,
            requested=self.limit_m,
            observed=observed,
            detail=(
                f"{observed / 1000:.1f} km exceeds the {self.limit_m / 1000:.1f} km limit"
                if outcome is Outcome.VIOLATED
                else ""
            ),
        )


@dataclass(frozen=True, slots=True)
class MaxDetour:
    """Bound on how far a route may exceed a reference length (§7.4.8)."""

    reference_distance_m: float
    max_ratio: float
    hard: bool = False
    stated_as: str | None = None
    key: str = "max_detour_ratio"

    def evaluate(self, route: RouteView) -> ConstraintResult:
        if self.reference_distance_m <= 0:
            return ConstraintResult(
                key=self.key,
                hard=self.hard,
                outcome=Outcome.UNEVALUABLE,
                stated_as=self.stated_as,
                detail="no reference distance to compare against",
            )

        ratio = route.distance_m / self.reference_distance_m
        outcome = Outcome.VIOLATED if ratio > self.max_ratio else Outcome.SATISFIED
        return ConstraintResult(
            key=self.key,
            hard=self.hard,
            outcome=outcome,
            stated_as=self.stated_as,
            requested=self.max_ratio,
            observed=ratio,
            detail=(
                f"route is {(ratio - 1) * 100:.0f}% longer than the reference, "
                f"above the {(self.max_ratio - 1) * 100:.0f}% allowance"
                if outcome is Outcome.VIOLATED
                else ""
            ),
        )


@dataclass
class ConstraintSet:
    """The constraints a route must meet, evaluated together."""

    constraints: list[Constraint] = field(default_factory=list)

    def evaluate(self, route: RouteView) -> list[ConstraintResult]:
        return [constraint.evaluate(route) for constraint in self.constraints]

    @staticmethod
    def blocking(results: Sequence[ConstraintResult]) -> list[ConstraintResult]:
        return [r for r in results if r.is_blocking]

    @staticmethod
    def unevaluable_hard(results: Sequence[ConstraintResult]) -> list[ConstraintResult]:
        return [r for r in results if r.hard and r.outcome is Outcome.UNEVALUABLE]

    @staticmethod
    def violating_segments(results: Sequence[ConstraintResult]) -> set[int]:
        """Segment indices to exclude on the next solve pass."""
        indices: set[int] = set()
        for result in results:
            if result.outcome is Outcome.VIOLATED:
                indices.update(result.segment_indices)
        return indices
