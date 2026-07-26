"""Climb detection tests.

The behaviours worth defending are the ones that make climb lists comparable:
a dip inside a col must not fragment it, genuinely separate hills must not
merge, rolling terrain must not become a climb list, and no climb may be
inferred across a coverage gap.
"""

from __future__ import annotations

import math

import pytest

from contour_api.analysis.climbs import (
    ClimbParameters,
    assign_to_stages,
    detect_from_points,
    summarise,
)
from contour_api.analysis.elevation import AnalysisParameters, ElevationPoint

FINE = AnalysisParameters(source_resolution_m=10.0, sample_interval_m=10.0)


def profile_from(pairs: list[tuple[float, float | None]]) -> list[ElevationPoint]:
    return [ElevationPoint(distance_m=d, elevation_m=e) for d, e in pairs]


def steady_climb(
    length_m: float, grade_percent: float, start_m: float = 0.0, start_elevation_m: float = 0.0
) -> list[tuple[float, float]]:
    points = []
    distance = 0.0
    while distance <= length_m:
        points.append(
            (start_m + distance, start_elevation_m + distance * grade_percent / 100.0)
        )
        distance += 10.0
    return points


def flat_run(length_m: float, start_m: float, elevation_m: float) -> list[tuple[float, float]]:
    points = []
    distance = 0.0
    while distance <= length_m:
        points.append((start_m + distance, elevation_m))
        distance += 10.0
    return points


class TestDetection:
    def test_a_steady_climb_is_detected_once(self) -> None:
        points = profile_from(steady_climb(1000.0, 6.0))

        climbs = detect_from_points(points, FINE)

        assert len(climbs) == 1
        climb = climbs[0]
        assert climb.elevation_gain_m == pytest.approx(60.0, rel=0.15)
        assert climb.average_grade_percent == pytest.approx(6.0, rel=0.15)

    def test_flat_route_has_no_climbs(self) -> None:
        points = profile_from(flat_run(3000.0, 0.0, 50.0))

        assert detect_from_points(points, FINE) == []

    def test_shallow_drag_is_not_a_climb(self) -> None:
        """A 1% drag is not a climb, however long, or every route has one."""
        points = profile_from(steady_climb(4000.0, 1.0))

        climbs = detect_from_points(points, FINE)

        assert climbs == []

    def test_small_rise_is_not_a_climb(self) -> None:
        """Below the minimum gain it is rolling terrain."""
        points = profile_from(steady_climb(200.0, 5.0))

        assert detect_from_points(points, FINE) == []

    def test_two_separate_hills_are_two_climbs(self) -> None:
        """A full descent between hills must split them."""
        first = steady_climb(1000.0, 6.0)
        descent = [(1000.0 + i * 10.0, 60.0 - i * 0.6) for i in range(1, 101)]
        second = steady_climb(1000.0, 6.0, start_m=2000.0, start_elevation_m=0.0)

        climbs = detect_from_points(profile_from(first + descent + second), FINE)

        assert len(climbs) == 2

    def test_a_dip_inside_a_col_does_not_fragment_it(self) -> None:
        """The case that matters on Irish coastal roads.

        A col interrupted by a 10 m dip is one climb, not two. Splitting on
        every dip produces a fragmented list nobody can plan against.
        """
        up = steady_climb(800.0, 6.0)
        dip = [(800.0 + i * 10.0, 48.0 - i * 1.0) for i in range(1, 11)]
        recover = [(900.0 + i * 10.0, 38.0 + i * 1.4) for i in range(1, 51)]

        climbs = detect_from_points(
            profile_from(up + dip + recover),
            FINE,
            ClimbParameters(max_internal_descent_m=20.0),
        )

        assert len(climbs) == 1
        assert climbs[0].end_distance_m == pytest.approx(1400.0, abs=60.0)

    def test_a_deep_descent_does_split_the_climb(self) -> None:
        """Same shape, deeper dip: now they are genuinely two hills."""
        up = steady_climb(1000.0, 6.0)
        dip = [(1000.0 + i * 10.0, 60.0 - i * 1.5) for i in range(1, 31)]
        recover = [(1300.0 + i * 10.0, 15.0 + i * 1.2) for i in range(1, 81)]

        climbs = detect_from_points(
            profile_from(up + dip + recover),
            FINE,
            ClimbParameters(max_internal_descent_m=20.0),
        )

        assert len(climbs) == 2

    def test_tolerance_is_recorded_with_each_climb(self) -> None:
        """Climbs found under different parameters are not comparable."""
        points = profile_from(steady_climb(1000.0, 6.0))

        climbs = detect_from_points(
            points, FINE, ClimbParameters(max_internal_descent_m=15.0)
        )

        assert climbs[0].parameters["max_internal_descent_m"] == 15.0
        assert climbs[0].parameters["min_gain_m"] == 30.0

    def test_trailing_descent_is_excluded_from_the_climb(self) -> None:
        """A climb ends at its summit, not where the next descent bottoms out."""
        up = steady_climb(1000.0, 6.0)
        down = [(1000.0 + i * 10.0, 60.0 - i * 0.6) for i in range(1, 101)]

        climbs = detect_from_points(profile_from(up + down), FINE)

        assert len(climbs) == 1
        assert climbs[0].end_distance_m == pytest.approx(1000.0, abs=60.0)

    def test_leading_flat_is_excluded_from_the_climb(self) -> None:
        """A plateau before the rise would understate the average gradient."""
        approach = flat_run(1000.0, 0.0, 0.0)
        up = steady_climb(1000.0, 6.0, start_m=1010.0)

        climbs = detect_from_points(profile_from(approach + up), FINE)

        assert len(climbs) == 1
        assert climbs[0].start_distance_m >= 900.0
        assert climbs[0].average_grade_percent == pytest.approx(6.0, rel=0.2)


class TestCoverageGaps:
    def test_no_climb_is_inferred_across_a_gap(self) -> None:
        """The elevation change inside a hole is unknown, not a climb."""
        before = [(float(i * 10), 0.0) for i in range(50)]
        hole: list[tuple[float, float | None]] = [
            (float(500 + i * 10), None) for i in range(30)
        ]
        after = [(float(800 + i * 10), 200.0) for i in range(50)]

        climbs = detect_from_points(profile_from(before + hole + after), FINE)

        assert climbs == []


class TestSummary:
    def test_superlatives_are_none_when_there_are_no_climbs(self) -> None:
        """"No climb detected" is not "the longest climb is zero"."""
        summary = summarise([])

        assert summary["climb_count"] == 0
        assert summary["longest_climb"] is None
        assert summary["steepest_climb"] is None

    def test_superlatives_identify_distinct_climbs(self) -> None:
        long_shallow = steady_climb(3000.0, 4.0)
        descent = [(3000.0 + i * 10.0, 120.0 - i * 1.0) for i in range(1, 111)]
        short_steep = steady_climb(500.0, 12.0, start_m=4110.0, start_elevation_m=10.0)

        climbs = detect_from_points(profile_from(long_shallow + descent + short_steep), FINE)
        summary = summarise(climbs)

        assert summary["climb_count"] == 2
        assert summary["longest_climb"] != summary["steepest_climb"]


class TestStageAssignment:
    def test_climb_belongs_to_the_stage_it_starts_in(self) -> None:
        """A climb crossing a boundary is met by the rider of the earlier stage."""
        first = steady_climb(1000.0, 6.0)
        descent = [(1000.0 + i * 10.0, 60.0 - i * 0.6) for i in range(1, 101)]
        second = steady_climb(1000.0, 6.0, start_m=2000.0)
        climbs = detect_from_points(profile_from(first + descent + second), FINE)

        assignment = assign_to_stages(climbs, [1500.0, 3500.0])

        assert climbs[0].sequence in assignment[0]
        assert climbs[1].sequence in assignment[1]


def test_rolling_terrain_does_not_produce_dozens_of_climbs() -> None:
    """Gentle rollers must not each be named as a climb."""
    points = profile_from(
        [(i * 10.0, 100.0 + 8.0 * math.sin(i / 12.0)) for i in range(600)]
    )

    climbs = detect_from_points(points, FINE)

    assert len(climbs) <= 2
