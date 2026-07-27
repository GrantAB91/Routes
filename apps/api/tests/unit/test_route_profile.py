"""Binding a measured elevation profile to route segments (§7.4.2, §11.3, §2.6).

This is the join that makes the gradient constraint evaluable, and the place
where an unmeasured segment most easily becomes a compliant one. The tests
below are mostly about that failure.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from contour_api.analysis.route_profile import (
    MIN_SAMPLE_INTERVAL_M,
    attach_elevation,
    resolve_interval,
    sample_positions,
)
from contour_api.models.enums import KnowledgeStatus
from contour_api.providers.elevation import HealthReport, ProviderHealth
from contour_api.providers.routing import LatLon
from contour_api.routing.constraints import MaxGradient, Outcome
from contour_api.routing.model import RouteView, SegmentView

# Two kilometres of straight coast road, as four vertices.
SHAPE = tuple(LatLon(lat=53.7600 + i * 0.006, lon=-9.8000) for i in range(4))


class FakeElevation:
    """A DEM that can be told where it has no coverage, and at what resolution."""

    name = "fake-dem"

    def __init__(
        self,
        *,
        resolution_m: float | None = 30.0,
        gap_after: float | None = None,
        rise_per_metre: float = 0.0,
    ) -> None:
        self._resolution = resolution_m
        self._gap_after = gap_after
        self._rise = rise_per_metre
        self.sampled_count = 0

    async def health(self) -> HealthReport:
        return HealthReport(status=ProviderHealth.HEALTHY)

    async def sample(self, points: tuple[LatLon, ...]) -> list[float | None]:
        self.sampled_count = len(points)
        values: list[float | None] = []
        origin = points[0].lat if points else 0.0
        for point in points:
            # Latitude stands in for distance; ~111 km per degree.
            metres = (point.lat - origin) * 111_000.0
            if self._gap_after is not None and metres > self._gap_after:
                values.append(None)
            else:
                values.append(10.0 + metres * self._rise)
        return values

    def resolution_m(self, at: LatLon) -> float | None:
        return self._resolution


def a_route(segment_count: int = 2) -> RouteView:
    """A route whose segments tile SHAPE end to end."""
    from contour_api.analysis.route_profile import haversine_m

    total = sum(haversine_m(a, b) for a, b in pairwise(SHAPE))
    per_segment = total / segment_count
    vertices_per = max(2, (len(SHAPE) + segment_count - 1) // segment_count)

    segments = []
    for index in range(segment_count):
        start = index * (len(SHAPE) - 1) // segment_count
        end = (index + 1) * (len(SHAPE) - 1) // segment_count
        segments.append(
            SegmentView(
                index=index,
                start_distance_m=index * per_segment,
                distance_m=per_segment,
                coordinates=SHAPE[start : end + 1] or SHAPE[:vertices_per],
            )
        )
    return RouteView(segments=tuple(segments), origin=SHAPE[0], destination=SHAPE[-1])


class TestSampling:
    def test_original_vertices_are_kept(self) -> None:
        """A regular grid alone can cut the apex off a hairpin."""
        sampled = sample_positions(SHAPE, 200.0)

        for vertex in SHAPE:
            assert vertex in sampled.points

    async def test_distances_are_monotonic_and_start_at_zero(self) -> None:
        sampled = sample_positions(SHAPE, 100.0)

        assert sampled.distances_m[0] == 0.0
        assert all(b >= a for a, b in pairwise(sampled.distances_m))

    async def test_spacing_is_about_the_requested_interval(self) -> None:
        sampled = sample_positions(SHAPE, 100.0)
        gaps = [b - a for a, b in pairwise(sampled.distances_m)]

        assert max(gaps) <= 100.0 + 1e-6

    async def test_a_degenerate_shape_does_not_crash(self) -> None:
        assert len(sample_positions((), 30.0)) == 0
        assert len(sample_positions((SHAPE[0],), 30.0)) == 1


class TestInterval:
    async def test_sampling_is_widened_to_the_source_resolution(self) -> None:
        """Ten metres over a 30 m DEM measures interpolation, not terrain."""
        interval, note = resolve_interval(FakeElevation(resolution_m=30.9), SHAPE[0], 10.0)

        assert interval == pytest.approx(30.9)
        assert note is not None and "30.9" in note

    async def test_a_finer_source_is_not_forced_coarser(self) -> None:
        interval, note = resolve_interval(FakeElevation(resolution_m=5.0), SHAPE[0], 30.0)

        assert interval == 30.0
        assert note is None

    async def test_a_source_that_cannot_say_produces_a_stated_assumption(self) -> None:
        """§11.3: an unqualified figure would imply knowledge nobody has."""
        interval, note = resolve_interval(FakeElevation(resolution_m=None), SHAPE[0], 30.0)

        assert interval == 30.0
        assert note is not None and "assumption" in note

    async def test_the_floor_is_never_breached(self) -> None:
        interval, _ = resolve_interval(FakeElevation(resolution_m=1.0), SHAPE[0], 1.0)

        assert interval == MIN_SAMPLE_INTERVAL_M


class TestAttachment:
    async def test_a_measured_segment_gets_a_grade_and_a_known_status(self) -> None:
        result = await attach_elevation(a_route(), FakeElevation(rise_per_metre=0.05))

        for segment in result.route.segments:
            assert segment.elevation_status is KnowledgeStatus.KNOWN
            assert segment.max_grade_percent is not None
            assert segment.max_grade_percent == pytest.approx(5.0, abs=1.0)

    async def test_an_uncovered_segment_is_unknown_not_flat(self) -> None:
        """The failure this whole module is arranged to prevent.

        A grade of zero on unmeasured ground reads as a compliant flat road.
        """
        result = await attach_elevation(a_route(), FakeElevation(gap_after=100.0))

        last = result.route.segments[-1]
        assert last.elevation_status is KnowledgeStatus.UNKNOWN
        assert last.max_grade_percent is None
        assert result.route.elevation_gap_m > 0

    async def test_the_gradient_check_reports_a_gap_rather_than_passing(self) -> None:
        """End to end: an unmeasured segment must not satisfy a hard limit."""
        result = await attach_elevation(a_route(), FakeElevation(gap_after=100.0))

        outcome = MaxGradient(limit_percent=8.0).evaluate(result.route)

        assert outcome.outcome is not Outcome.SATISFIED

    async def test_coverage_is_reported_rather_than_assumed_complete(self) -> None:
        result = await attach_elevation(a_route(), FakeElevation(gap_after=100.0))
        payload = result.as_dict()

        assert payload["coverage"]["complete"] is False
        assert payload["coverage"]["gap_distance_m"] > 0

    async def test_a_route_with_no_coverage_reports_none_not_zero(self) -> None:
        """Zero ascent is a claim about terrain; None is the absence of one."""
        result = await attach_elevation(a_route(), FakeElevation(gap_after=-1.0))
        payload = result.as_dict()

        assert payload["ascent_m"] is None
        assert payload["max_grade_percent"] is None

    async def test_the_method_is_reported_with_the_figures(self) -> None:
        """§11.3: a profile without its parameters cannot be checked."""
        result = await attach_elevation(
            a_route(), FakeElevation(rise_per_metre=0.02), requested_interval_m=10.0
        )
        payload = result.as_dict()

        assert payload["method"]["sample_interval_m"] == pytest.approx(30.0)
        assert payload["notes"], "widening the interval must be stated"

    async def test_the_elevation_source_is_named_on_the_route(self) -> None:
        result = await attach_elevation(a_route(), FakeElevation())

        assert result.route.metadata["elevation_source"] == "fake-dem"
