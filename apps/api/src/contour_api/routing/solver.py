"""The solve loop and alternative generation.

Contour routes in a cycle: ask the engine for geometry, check that geometry
against what the user actually asked for, and if it falls short, tell the engine
what to avoid and ask again. The loop exists because no routing engine expresses
the constraints in §7.4 — Valhalla has no maximum gradient, no unpaved-distance
budget, and no notion of unknown data at all — so those can only be enforced
against geometry that already exists.

Two rules govern the loop:

* it never edits geometry. Only the engine produces geometry; the loop changes
  the *question*, by growing an exclusion set (§7.1);
* it never gives up silently. When no compliant route can be found, the closest
  attempt is returned with the exact violating segments and a ``NOT_FEASIBLE``
  verdict, because §7.6 forbids quietly dropping a requirement.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from ..models.enums import FeasibilityVerdict
from ..providers.routing import (
    CostingPreferences,
    ExclusionSet,
    LatLon,
    NoRouteFoundError,
    RouteCandidate,
    RouteRequest,
    RoutingProvider,
)
from .constraints import ConstraintSet, Outcome
from .model import RouteView, SegmentView, shared_distance_m
from .validation import RouteValidator, ValidationOutcome


class SegmentAttributor(Protocol):
    """Turns engine geometry into attributed segments.

    In production this reads surface, access and road class from PostGIS for the
    edges the engine used. It is a seam so that the loop can be tested without a
    database, and so that attribution stays the responsibility of Contour's own
    data rather than of whatever the engine happened to report.
    """

    def attribute(self, candidate: RouteCandidate) -> RouteView: ...


@dataclass(frozen=True, slots=True)
class SolveAttempt:
    """One pass through the loop, kept for the audit trail."""

    attempt: int
    distance_m: float
    verdict: FeasibilityVerdict
    violating_segment_count: int
    violating_distance_m: float
    excluded_locations: int


@dataclass(frozen=True, slots=True)
class SolveResult:
    route: RouteView
    candidate: RouteCandidate
    validation: ValidationOutcome
    attempts: tuple[SolveAttempt, ...]

    @property
    def is_compliant(self) -> bool:
        return self.validation.verdict in {
            FeasibilityVerdict.FULLY_SATISFIED,
            FeasibilityVerdict.SATISFIED_WITH_UNKNOWN_DATA,
        }


def _violating_distance(validation: ValidationOutcome) -> float:
    return sum(
        result.affected_distance_m
        for result in validation.constraint_results
        if result.outcome is Outcome.VIOLATED
    )


def _exclusion_points(route: RouteView, indices: set[int]) -> tuple[LatLon, ...]:
    """Pick one point per violating segment to exclude.

    Upstream documents ``exclude_locations`` as "much more efficient" than
    polygons for avoiding a handful of specific roads, and each location is
    snapped to the roads nearest it. Using a segment's midpoint rather than an
    endpoint matters: endpoints are shared with the neighbouring segments, so
    excluding one would take out roads the route legitimately needs.
    """
    points: list[LatLon] = []
    for segment in route.segments:
        if segment.index in indices:
            midpoint = segment.midpoint
            if midpoint is not None:
                points.append(midpoint)
    return tuple(points)


@dataclass
class RouteSolver:
    provider: RoutingProvider
    attributor: SegmentAttributor
    max_attempts: int = 4

    async def solve(
        self,
        request: RouteRequest,
        constraint_set: ConstraintSet,
    ) -> SolveResult:
        """Route, validate, and re-solve until compliant or out of attempts."""
        validator = RouteValidator(constraint_set)
        exclusions = request.exclusions
        attempts: list[SolveAttempt] = []

        best: SolveResult | None = None
        best_violating_m = float("inf")
        # Excluding the same segment twice is wasted work and, worse, hides
        # that the loop has stopped making progress.
        already_excluded: set[tuple[float, float]] = set()

        for attempt in range(1, self.max_attempts + 1):
            attempt_request = replace(request, exclusions=exclusions)
            try:
                candidates = await self.provider.route(attempt_request)
            except NoRouteFoundError:
                # The exclusions have made the request impossible. The best
                # non-compliant attempt so far is a more useful answer than an
                # error, because it shows the user what is in the way.
                if best is not None:
                    return self._finalise(best, attempts, validator)
                raise

            candidate = candidates[0]
            route = self.attributor.attribute(candidate)
            exhausted = attempt >= self.max_attempts
            validation = validator.validate(
                route, resolve_attempts=attempt, exhausted_attempts=exhausted
            )

            violating = ConstraintSet.violating_segments(validation.constraint_results)
            violating_m = _violating_distance(validation)
            attempts.append(
                SolveAttempt(
                    attempt=attempt,
                    distance_m=route.distance_m,
                    verdict=validation.verdict,
                    violating_segment_count=len(violating),
                    violating_distance_m=violating_m,
                    excluded_locations=len(exclusions.locations),
                )
            )

            result = SolveResult(
                route=route,
                candidate=candidate,
                validation=validation,
                attempts=tuple(attempts),
            )
            if result.is_compliant:
                return result

            # Keep the closest attempt by how much of the route breaks the
            # rules, which is what §7.6 means by "the closest result".
            if violating_m < best_violating_m:
                best, best_violating_m = result, violating_m

            if not violating or exhausted:
                break

            new_points = tuple(
                point
                for point in _exclusion_points(route, violating)
                if (round(point.lat, 6), round(point.lon, 6)) not in already_excluded
            )
            if not new_points:
                # Nothing new to exclude: another pass would return the same
                # route. Stopping here is honest; looping is theatre.
                break
            already_excluded.update((round(p.lat, 6), round(p.lon, 6)) for p in new_points)
            exclusions = ExclusionSet(
                polygons=exclusions.polygons,
                locations=exclusions.locations + new_points,
                edge_ids=exclusions.edge_ids,
            )

        if best is None:
            raise NoRouteFoundError("no candidate route could be produced")
        return self._finalise(best, attempts, validator)

    @staticmethod
    def _finalise(
        best: SolveResult,
        attempts: Sequence[SolveAttempt],
        validator: RouteValidator,
    ) -> SolveResult:
        """Re-validate the best attempt as final, so its verdict is honest.

        The stored verdict must reflect that the loop is over. Without this the
        best attempt keeps the ``PARTIALLY_SATISFIED`` it received mid-loop,
        which reads as "still working on it" rather than "this cannot be done".
        """
        final = validator.validate(
            best.route,
            resolve_attempts=len(attempts),
            exhausted_attempts=True,
        )
        return SolveResult(
            route=best.route,
            candidate=best.candidate,
            validation=final,
            attempts=tuple(attempts),
        )


@dataclass(frozen=True, slots=True)
class AlternativeProfile:
    """One way of asking for the same journey (§13.7)."""

    slug: str
    title: str
    selection_reason: str
    preferences: CostingPreferences


@dataclass(frozen=True, slots=True)
class Alternative:
    profile: AlternativeProfile
    result: SolveResult
    shared_distance_m: float = 0.0
    unique_distance_m: float = 0.0
    overlap_with: dict[str, float] = field(default_factory=dict)


@dataclass
class AlternativeGenerator:
    """Produces meaningfully different alternatives (§7.8).

    Engine-provided alternates are not usable for Contour's reference journey:
    Valhalla documents ``alternates`` as unsupported on multipoint routes, and
    the Wild Atlantic Way is routed with corridor anchor points throughout. So
    alternatives come from solving the same journey under genuinely different
    preference profiles, then discarding any that turn out to be near-copies of
    one already accepted.

    The similarity test is on shared distance by edge identity. Two routes that
    differ only in which side of a village they pass are not two alternatives,
    and offering them as such wastes the user's attention.
    """

    solver: RouteSolver
    # Above this fraction of shared distance, a candidate is a near-copy.
    max_similarity: float = 0.85

    async def generate(
        self,
        request: RouteRequest,
        profiles: Sequence[AlternativeProfile],
        constraint_set_for: Callable[[AlternativeProfile], ConstraintSet],
    ) -> list[Alternative]:
        accepted: list[Alternative] = []

        for profile in profiles:
            profile_request = replace(request, preferences=profile.preferences)
            try:
                result = await self.solver.solve(profile_request, constraint_set_for(profile))
            except NoRouteFoundError:
                # One profile failing does not invalidate the others; the
                # Coverage of alternatives is reported from what succeeded.
                continue

            if self._is_near_copy(result.route, accepted):
                continue

            accepted.append(Alternative(profile=profile, result=result))

        return self._quantify(accepted)

    def _is_near_copy(self, route: RouteView, accepted: Sequence[Alternative]) -> bool:
        if not route.distance_m:
            return True
        for existing in accepted:
            shared = shared_distance_m(route.segments, existing.result.route.segments)
            if shared / route.distance_m > self.max_similarity:
                return True
        return False

    @staticmethod
    def _quantify(alternatives: list[Alternative]) -> list[Alternative]:
        """Fill in the difference measures the comparison screen ranks on."""
        quantified: list[Alternative] = []

        for alternative in alternatives:
            segments = alternative.result.route.segments
            overlap: dict[str, float] = {}
            shared_total = 0.0

            for other in alternatives:
                if other is alternative:
                    continue
                shared = shared_distance_m(segments, other.result.route.segments)
                overlap[other.profile.slug] = shared
                shared_total = max(shared_total, shared)

            distance = alternative.result.route.distance_m
            quantified.append(
                replace(
                    alternative,
                    shared_distance_m=shared_total,
                    unique_distance_m=max(0.0, distance - shared_total),
                    overlap_with=overlap,
                )
            )

        return quantified


def wild_atlantic_way_profiles() -> list[AlternativeProfile]:
    """The four alternatives §13.7 requires.

    Each optimises a different axis so the four are structurally different
    routes rather than four renderings of one. The reasons are the text shown
    on each route card, in the user's language rather than engine terms.
    """
    return [
        AlternativeProfile(
            slug="closest-to-corridor",
            title="Closest to the official route",
            selection_reason=(
                "Stays as near the official Wild Atlantic Way line as legal cycling "
                "roads allow, accepting more climbing and more main road to do it."
            ),
            preferences=CostingPreferences(use_roads=0.5, avoid_hills=0.1),
        ),
        AlternativeProfile(
            slug="lowest-ascent",
            title="Lowest ascent",
            selection_reason=(
                "Trades coastal proximity for flatter ground, cutting total climbing "
                "at the cost of leaving the corridor more often."
            ),
            preferences=CostingPreferences(avoid_hills=1.0, use_roads=0.4),
        ),
        AlternativeProfile(
            slug="most-cycle-infrastructure",
            title="Most cycle infrastructure",
            selection_reason=(
                "Favours segments with verified cycle infrastructure, which can mean "
                "a longer route and more time away from the coast."
            ),
            preferences=CostingPreferences(prefer_cycle_infrastructure=1.0, avoid_hills=0.4),
        ),
        AlternativeProfile(
            slug="balanced",
            title="Balanced",
            selection_reason=(
                "A compromise across coast proximity, directness, climbing, surface "
                "and cycle infrastructure, with no single axis pushed hard."
            ),
            preferences=CostingPreferences(use_roads=0.3, avoid_hills=0.5, avoid_bad_surfaces=0.4),
        ),
    ]


def build_segment_views(
    candidate: RouteCandidate,
    attributes_for_edge: Callable[[int, dict], dict],
) -> tuple[SegmentView, ...]:
    """Helper for attributors: lay engine edges out as segments.

    Distances accumulate here so that every segment's ``start_distance_m`` is
    consistent with the route's own length, which the elevation profile and the
    stage boundaries both index into.
    """
    segments: list[SegmentView] = []
    cursor = 0.0
    index = 0

    for leg in candidate.legs:
        for edge in leg.edges or ():
            distance_m = float(edge.get("length", 0.0)) * 1000.0
            attributes = attributes_for_edge(index, edge)
            segments.append(
                SegmentView(
                    index=index,
                    start_distance_m=cursor,
                    distance_m=distance_m,
                    **attributes,
                )
            )
            cursor += distance_m
            index += 1

    return tuple(segments)
