"""Solve loop and alternative generation tests.

The loop's job is to make constraints the engine cannot express actually bind.
These tests use a fake provider whose behaviour is scripted, so the loop's
decisions — what it excludes, when it stops, what verdict it settles on — are
observable without tiles or a network.
"""

from __future__ import annotations

import pytest

from contour_api.models.enums import FeasibilityVerdict, KnowledgeStatus
from contour_api.providers.routing import (
    CostingPreferences,
    LatLon,
    NoRouteFoundError,
    RouteCandidate,
    RouteLegShape,
    RouteRequest,
    RoutingLocation,
)
from contour_api.routing.constraints import ConstraintSet, MaxGradient
from contour_api.routing.model import RouteView, SegmentView
from contour_api.routing.solver import (
    MAX_EXCLUDE_LOCATIONS,
    AlternativeGenerator,
    AlternativeProfile,
    RouteSolver,
    _exclusion_points,
    wild_atlantic_way_profiles,
)

ORIGIN = LatLon(lat=53.2707, lon=-9.0568)
DESTINATION = LatLon(lat=53.4890, lon=-10.0200)
REQUEST = RouteRequest(
    locations=(
        RoutingLocation(point=ORIGIN),
        RoutingLocation(point=DESTINATION),
    )
)


def make_segments(*specs: tuple[float, str], distance_m: float = 1000.0) -> list[SegmentView]:
    """Build connected segments running from ORIGIN to DESTINATION.

    Consecutive segments must share a vertex: the validator rejects a route with
    unexplained gaps as NOT_FEASIBLE before any constraint is even considered,
    so disconnected fixture geometry silently tests the wrong thing.
    """
    count = len(specs)
    points = [
        LatLon(
            lat=ORIGIN.lat + (DESTINATION.lat - ORIGIN.lat) * i / count,
            lon=ORIGIN.lon + (DESTINATION.lon - ORIGIN.lon) * i / count,
        )
        for i in range(count + 1)
    ]

    return [
        SegmentView(
            index=index,
            start_distance_m=index * distance_m,
            distance_m=distance_m,
            max_grade_percent=grade,
            elevation_status=KnowledgeStatus.KNOWN,
            coordinates=(points[index], points[index + 1]),
            graph_edge_id=edge_id,
        )
        for index, (grade, edge_id) in enumerate(specs)
    ]


class ScriptedProvider:
    """Returns a queued route per call, recording what it was asked."""

    name = "scripted"

    def __init__(self, routes: list[list[SegmentView]]) -> None:
        self._routes = routes
        self.requests: list[RouteRequest] = []

    async def route(self, request: RouteRequest) -> list[RouteCandidate]:
        self.requests.append(request)
        if not self._routes:
            raise NoRouteFoundError()
        segments = self._routes.pop(0)
        self.current = segments
        return [
            RouteCandidate(
                legs=(
                    RouteLegShape(
                        coordinates=(ORIGIN, DESTINATION),
                        distance_m=sum(s.distance_m for s in segments),
                        duration_s=None,
                    ),
                ),
                distance_m=sum(s.distance_m for s in segments),
                duration_s=None,
                provider=self.name,
            )
        ]

    async def health(self):  # pragma: no cover - unused here
        raise NotImplementedError

    async def map_match(self, points, preferences):  # pragma: no cover - unused
        raise NotImplementedError


class ScriptedAttributor:
    """Attributes each candidate with whatever the provider just produced."""

    def __init__(self, provider: ScriptedProvider) -> None:
        self._provider = provider

    async def attribute(self, candidate: RouteCandidate) -> RouteView:
        return RouteView(
            segments=tuple(self._provider.current),
            origin=ORIGIN,
            destination=DESTINATION,
        )


def solver_for(routes: list[list[SegmentView]], max_attempts: int = 4) -> RouteSolver:
    provider = ScriptedProvider(routes)
    return RouteSolver(
        provider=provider, attributor=ScriptedAttributor(provider), max_attempts=max_attempts
    )


class TestSolveLoop:
    async def test_a_compliant_route_is_returned_on_the_first_pass(self) -> None:
        solver = solver_for([make_segments((5.0, "a"), (6.0, "b"))])

        result = await solver.solve(REQUEST, ConstraintSet([MaxGradient(limit_percent=12.0)]))

        assert result.is_compliant
        assert len(result.attempts) == 1

    async def test_a_violating_segment_is_excluded_and_the_route_resolved(self) -> None:
        """The loop changes the question, never the geometry."""
        steep = make_segments((5.0, "a"), (18.0, "b"))
        gentle = make_segments((5.0, "a"), (7.0, "c"))
        solver = solver_for([steep, gentle])

        result = await solver.solve(REQUEST, ConstraintSet([MaxGradient(limit_percent=12.0)]))

        assert result.is_compliant
        assert len(result.attempts) == 2
        # The second request carried an exclusion derived from the steep segment.
        second = solver.provider.requests[1]  # type: ignore[attr-defined]
        assert len(second.exclusions.locations) == 1

    async def test_an_impossible_constraint_returns_the_closest_route(self) -> None:
        """§7.6: never silently ignore a limit that cannot be met."""
        steep = make_segments((5.0, "a"), (18.0, "b"))
        steeper = make_segments((5.0, "a"), (25.0, "c"))
        solver = solver_for([steep, steeper, steep, steeper], max_attempts=4)

        result = await solver.solve(REQUEST, ConstraintSet([MaxGradient(limit_percent=12.0)]))

        assert not result.is_compliant
        assert result.validation.verdict is FeasibilityVerdict.NOT_FEASIBLE
        # The violating segments are named so the user can see what is in the way.
        violation = result.validation.violations[0]
        assert violation.key == "max_gradient_percent"
        assert violation.segment_indices
        # And it kept the least-bad attempt, not the last one tried.
        assert result.route.segments[1].max_grade_percent == pytest.approx(18.0)

    async def test_the_loop_stops_when_exclusions_stop_changing(self) -> None:
        """Re-asking an identical question is theatre, not effort."""
        steep = make_segments((5.0, "a"), (18.0, "b"))
        solver = solver_for([steep, steep, steep, steep], max_attempts=4)

        result = await solver.solve(REQUEST, ConstraintSet([MaxGradient(limit_percent=12.0)]))

        # Second pass returns the same geometry, so there is nothing new to
        # exclude and the loop halts rather than burning its remaining budget.
        assert len(result.attempts) == 2
        assert result.validation.verdict is FeasibilityVerdict.NOT_FEASIBLE

    async def test_exhausting_the_engine_returns_the_best_effort(self) -> None:
        """When exclusions make the request unroutable, show what was found."""
        steep = make_segments((5.0, "a"), (18.0, "b"))
        solver = solver_for([steep])  # second call raises NoRouteFoundError

        result = await solver.solve(REQUEST, ConstraintSet([MaxGradient(limit_percent=12.0)]))

        assert not result.is_compliant
        assert result.validation.verdict is FeasibilityVerdict.NOT_FEASIBLE

    async def test_no_route_at_all_raises(self) -> None:
        solver = solver_for([])

        with pytest.raises(NoRouteFoundError):
            await solver.solve(REQUEST, ConstraintSet())

    async def test_the_final_verdict_reflects_that_the_loop_is_over(self) -> None:
        """A best effort must not read as still in progress."""
        steep = make_segments((5.0, "a"), (18.0, "b"))
        solver = solver_for([steep, steep])

        result = await solver.solve(REQUEST, ConstraintSet([MaxGradient(limit_percent=12.0)]))

        assert result.validation.exhausted_attempts
        assert result.validation.verdict is FeasibilityVerdict.NOT_FEASIBLE


class TestAlternatives:
    @staticmethod
    def _profiles() -> list[AlternativeProfile]:
        return [
            AlternativeProfile(
                slug=slug,
                title=slug,
                selection_reason="",
                preferences=CostingPreferences(),
            )
            for slug in ("one", "two")
        ]

    async def test_near_copies_are_discarded(self) -> None:
        """Two renderings of one route are not two alternatives."""
        route = make_segments((5.0, "a"), (5.0, "b"))
        almost_identical = make_segments((5.0, "a"), (5.0, "b"))
        solver = solver_for([route, almost_identical])

        alternatives = await AlternativeGenerator(solver).generate(
            REQUEST, self._profiles(), lambda _: ConstraintSet()
        )

        assert len(alternatives) == 1

    async def test_genuinely_different_routes_are_both_kept(self) -> None:
        first = make_segments((5.0, "a"), (5.0, "b"))
        second = make_segments((5.0, "x"), (5.0, "y"))
        solver = solver_for([first, second])

        alternatives = await AlternativeGenerator(solver).generate(
            REQUEST, self._profiles(), lambda _: ConstraintSet()
        )

        assert len(alternatives) == 2
        assert alternatives[0].unique_distance_m > 0

    async def test_a_failing_profile_does_not_sink_the_others(self) -> None:
        """One unroutable preference set must not lose the whole comparison."""
        first = make_segments((5.0, "a"), (5.0, "b"))
        solver = solver_for([first])

        alternatives = await AlternativeGenerator(solver).generate(
            REQUEST, self._profiles(), lambda _: ConstraintSet()
        )

        assert len(alternatives) == 1

    async def test_overlap_is_quantified_against_every_other_alternative(self) -> None:
        first = make_segments((5.0, "a"), (5.0, "shared"))
        second = make_segments((5.0, "z"), (5.0, "shared"))
        solver = solver_for([first, second])

        alternatives = await AlternativeGenerator(solver).generate(
            REQUEST, self._profiles(), lambda _: ConstraintSet()
        )

        assert len(alternatives) == 2
        assert alternatives[0].overlap_with["two"] == pytest.approx(1000.0)
        assert alternatives[0].shared_distance_m == pytest.approx(1000.0)
        assert alternatives[0].unique_distance_m == pytest.approx(1000.0)


def test_the_reference_project_defines_four_distinct_profiles() -> None:
    """§13.7 requires four genuinely different alternatives."""
    profiles = wild_atlantic_way_profiles()

    assert len(profiles) == 4
    assert len({p.slug for p in profiles}) == 4
    # Each must explain itself on its route card, in plain language.
    assert all(p.selection_reason for p in profiles)
    # And they must actually differ in what they ask the engine for.
    assert len({repr(p.preferences) for p in profiles}) == 4


def make_route(*, segment_count: int, distances: tuple[float, ...] | None = None) -> RouteView:
    """A connected route of `segment_count` segments, optionally uneven."""
    lengths = distances or tuple(1000.0 for _ in range(segment_count))
    points = [
        LatLon(
            lat=ORIGIN.lat + (DESTINATION.lat - ORIGIN.lat) * i / segment_count,
            lon=ORIGIN.lon + (DESTINATION.lon - ORIGIN.lon) * i / segment_count,
        )
        for i in range(segment_count + 1)
    ]
    cursor = 0.0
    segments = []
    for index in range(segment_count):
        segments.append(
            SegmentView(
                index=index,
                start_distance_m=cursor,
                distance_m=lengths[index],
                coordinates=(points[index], points[index + 1]),
            )
        )
        cursor += lengths[index]
    return RouteView(segments=tuple(segments), origin=ORIGIN, destination=DESTINATION)


class TestExclusionLimit:
    """The engine's ceiling is a hard rejection, not a truncation."""

    def test_more_violations_than_the_engine_accepts_are_capped(self) -> None:
        """A real Irish route produced 297 segments and failed outright.

        Valhalla's `max_exclude_locations` is 200, and exceeding it rejects the
        whole request rather than dropping the extras. The loop has to stay
        under the ceiling rather than discover it.
        """
        route = make_route(segment_count=300)

        points = _exclusion_points(route, set(range(300)))

        assert len(points) == MAX_EXCLUDE_LOCATIONS

    def test_the_longest_violations_are_excluded_first(self) -> None:
        """Each slot should remove as much offending distance as possible."""
        route = make_route(segment_count=4, distances=(10.0, 5000.0, 20.0, 4000.0))

        points = _exclusion_points(route, {0, 1, 2, 3}, limit=2)
        chosen = {(round(p.lat, 5), round(p.lon, 5)) for p in points}
        expected = {
            (round(s.midpoint.lat, 5), round(s.midpoint.lon, 5))
            for s in route.segments
            if s.index in {1, 3}
        }

        assert chosen == expected

    def test_the_order_of_the_excluded_points_follows_the_route(self) -> None:
        """Sorted by distance to choose, restored to route order to send."""
        route = make_route(segment_count=4, distances=(10.0, 5000.0, 20.0, 4000.0))

        points = _exclusion_points(route, {0, 1, 2, 3}, limit=2)

        assert points[0].lat < points[1].lat

    def test_a_route_within_the_limit_is_untouched(self) -> None:
        route = make_route(segment_count=5)

        assert len(_exclusion_points(route, {0, 2, 4})) == 3
