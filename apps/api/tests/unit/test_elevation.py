"""Elevation analysis tests.

The properties in this file are the ones that make a published ascent figure
defensible. Three matter more than the rest:

* noise below the elevation model's resolution must not become ascent;
* sampling more densely must not increase ascent;
* a coverage gap must never be bridged into invented climbing.

If any of those break, every ascent number in the product is wrong in a way no
user could detect.
"""

from __future__ import annotations

import math
import random

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from contour_api.analysis.elevation import (
    AnalysisParameters,
    ElevationPoint,
    ElevationSpan,
    accumulate_relief,
    analyse,
    gap_distance,
    split_into_spans,
)


def ramp(
    count: int, interval_m: float = 30.0, gain_per_step_m: float = 3.0
) -> list[ElevationPoint]:
    return [
        ElevationPoint(distance_m=i * interval_m, elevation_m=i * gain_per_step_m)
        for i in range(count)
    ]


def flat(count: int, interval_m: float = 30.0, elevation_m: float = 100.0):
    return [
        ElevationPoint(distance_m=i * interval_m, elevation_m=elevation_m) for i in range(count)
    ]


class TestNoiseHandling:
    """The headline property: noise is not terrain."""

    def test_flat_route_has_no_ascent(self) -> None:
        profile = analyse(flat(100))

        assert profile.ascent_m == 0.0
        assert profile.descent_m == 0.0

    def test_sub_threshold_noise_does_not_accumulate_as_ascent(self) -> None:
        """A flat road under a noisy DEM must not report hundreds of metres.

        This is the failure that makes naive ascent figures useless: summing
        every positive difference over 100 samples of +/-1 m noise yields tens
        of metres of phantom climbing on a road that does not rise at all.
        """
        rng = random.Random(20260726)
        noisy = [
            ElevationPoint(distance_m=i * 30.0, elevation_m=100.0 + rng.uniform(-1.0, 1.0))
            for i in range(200)
        ]

        naive = sum(
            max(0.0, noisy[i + 1].elevation_m - noisy[i].elevation_m)  # type: ignore[operator]
            for i in range(len(noisy) - 1)
        )
        profile = analyse(noisy)

        # The naive sum is large enough to look like a real hill.
        assert naive > 40.0
        assert profile.ascent_m == 0.0

    def test_denser_sampling_does_not_inflate_ascent(self) -> None:
        """Ascent must be a property of the terrain, not of the sample count.

        The same hill sampled at 30 m and at 10 m must agree. A naive algorithm
        reports steadily more climbing as sampling tightens, which is how two
        tools disagree by 40% on the same GPX file.
        """

        def hill(interval_m: float) -> list[ElevationPoint]:
            rng = random.Random(7)
            points = []
            distance = 0.0
            while distance <= 3000.0:
                # A single 150 m rise over 3 km, plus noise below the threshold.
                base = 150.0 * math.sin(math.pi * distance / 3000.0)
                points.append(
                    ElevationPoint(
                        distance_m=distance,
                        elevation_m=base + rng.uniform(-0.8, 0.8),
                    )
                )
                distance += interval_m
            return points

        coarse = analyse(hill(30.0), AnalysisParameters(source_resolution_m=30.0))
        fine = analyse(hill(10.0), AnalysisParameters(source_resolution_m=30.0))

        assert coarse.ascent_m == pytest.approx(fine.ascent_m, rel=0.10)

    def test_a_real_climb_is_still_measured(self) -> None:
        """Noise suppression must not suppress terrain."""
        profile = analyse(ramp(40))

        # 39 steps of 3 m = 117 m of genuine gain; smoothing trims the ends.
        assert profile.ascent_m == pytest.approx(117.0, rel=0.10)
        assert profile.descent_m == 0.0


class TestCoverageGaps:
    def test_gap_is_not_bridged_into_ascent(self) -> None:
        """Elevation across a hole is unknown, not a straight line.

        Interpolating across the gap here would manufacture 500 m of climbing
        that no source ever measured.
        """
        points = [
            ElevationPoint(distance_m=0.0, elevation_m=0.0),
            ElevationPoint(distance_m=30.0, elevation_m=0.0),
            ElevationPoint(distance_m=60.0, elevation_m=None),
            ElevationPoint(distance_m=90.0, elevation_m=None),
            ElevationPoint(distance_m=120.0, elevation_m=500.0),
            ElevationPoint(distance_m=150.0, elevation_m=500.0),
        ]

        profile = analyse(points)

        assert profile.ascent_m == 0.0
        assert profile.gap_distance_m == pytest.approx(90.0)
        assert not profile.has_complete_coverage

    def test_net_change_is_unknown_across_a_gap(self) -> None:
        """Endpoints say nothing about what happened between them."""
        points = [
            ElevationPoint(distance_m=0.0, elevation_m=10.0),
            ElevationPoint(distance_m=30.0, elevation_m=12.0),
            ElevationPoint(distance_m=60.0, elevation_m=None),
            ElevationPoint(distance_m=90.0, elevation_m=40.0),
            ElevationPoint(distance_m=120.0, elevation_m=42.0),
        ]

        profile = analyse(points)

        assert profile.net_elevation_change_m is None

    def test_no_coverage_reports_none_not_zero(self) -> None:
        """Zero ascent is a claim about terrain; None is the honest answer."""
        points = [ElevationPoint(distance_m=i * 30.0, elevation_m=None) for i in range(10)]

        profile = analyse(points)

        assert profile.ascent_m is None
        assert profile.descent_m is None
        assert profile.max_grade_percent is None
        assert profile.min_elevation_m is None
        assert profile.coverage_ratio == 0.0

    def test_coverage_ratio_reflects_measured_distance(self) -> None:
        points = (
            flat(5)
            + [ElevationPoint(distance_m=150.0, elevation_m=None)]
            + [ElevationPoint(distance_m=i * 30.0, elevation_m=100.0) for i in range(6, 11)]
        )

        profile = analyse(points)

        assert 0.0 < profile.coverage_ratio < 1.0

    def test_span_rejects_null_elevation(self) -> None:
        """The invariant is enforced at construction, not asserted at use."""
        with pytest.raises(ValueError, match="without elevation"):
            ElevationSpan(
                points=(
                    ElevationPoint(distance_m=0.0, elevation_m=1.0),
                    ElevationPoint(distance_m=30.0, elevation_m=None),
                )
            )

    def test_gap_distance_counts_leading_and_trailing_holes(self) -> None:
        points = [
            ElevationPoint(distance_m=0.0, elevation_m=None),
            ElevationPoint(distance_m=30.0, elevation_m=5.0),
            ElevationPoint(distance_m=60.0, elevation_m=5.0),
            ElevationPoint(distance_m=90.0, elevation_m=None),
        ]

        assert gap_distance(points) == pytest.approx(60.0)

    def test_single_sample_spans_are_discarded(self) -> None:
        """One isolated sample supports no gradient and no relief."""
        points = [
            ElevationPoint(distance_m=0.0, elevation_m=None),
            ElevationPoint(distance_m=30.0, elevation_m=100.0),
            ElevationPoint(distance_m=60.0, elevation_m=None),
        ]

        assert split_into_spans(points) == ()


class TestParameterHonesty:
    def test_sampling_cannot_be_finer_than_the_source(self) -> None:
        """Asking a 30 m DEM for 10 m detail yields interpolation, not detail."""
        params = AnalysisParameters(source_resolution_m=30.0, sample_interval_m=10.0).validated()

        assert params.sample_interval_m == 30.0

    def test_windows_below_the_sample_interval_are_dropped(self) -> None:
        """A 25 m grade window is unsupportable on a 30 m DEM, so it is not offered."""
        params = AnalysisParameters(
            source_resolution_m=30.0, grade_windows_m=(25.0, 100.0, 500.0, 1000.0)
        )

        assert 25.0 not in params.validated().grade_windows_m

    def test_dropped_windows_are_reported_from_the_validated_parameters(self) -> None:
        """The omission must be visible on the result, not only before it.

        Found by running against a real 30.9 m Copernicus DEM: the 25 m window
        was correctly dropped but reported as "none dropped", because the check
        ran against the already-filtered window list and so could never find
        what had been removed. A silently narrowed analysis is exactly the kind
        of quiet loss of precision §11.6 asks to be surfaced.
        """
        validated = AnalysisParameters(
            source_resolution_m=30.9,
            sample_interval_m=30.9,
            grade_windows_m=(25.0, 100.0, 500.0, 1000.0),
        ).validated()

        assert validated.dropped_windows() == (25.0,)
        assert 25.0 not in validated.grade_windows_m

    def test_a_fine_source_drops_nothing(self) -> None:
        validated = AnalysisParameters(
            source_resolution_m=5.0,
            sample_interval_m=5.0,
            grade_windows_m=(25.0, 100.0),
        ).validated()

        assert validated.dropped_windows() == ()
        assert 25.0 in validated.grade_windows_m

    def test_fine_source_supports_the_short_window(self) -> None:
        params = AnalysisParameters(
            source_resolution_m=5.0, sample_interval_m=5.0, grade_windows_m=(25.0, 100.0)
        ).validated()

        assert 25.0 in params.grade_windows_m

    def test_parameters_are_returned_with_the_result(self) -> None:
        """A stored figure must be reproducible and comparable."""
        profile = analyse(ramp(30), AnalysisParameters(min_gain_threshold_m=5.0))

        assert profile.parameters.min_gain_threshold_m == 5.0
        assert profile.parameters.sample_interval_m >= profile.parameters.source_resolution_m


class TestGradient:
    def test_sustained_grade_is_reported_per_window(self) -> None:
        """A short ramp inside a long flat must not read as a long steep climb."""
        points = (
            flat(20)
            + [
                ElevationPoint(distance_m=600.0 + i * 30.0, elevation_m=100.0 + i * 6.0)
                for i in range(1, 6)
            ]
            + [ElevationPoint(distance_m=750.0 + i * 30.0, elevation_m=130.0) for i in range(1, 25)]
        )

        profile = analyse(points, AnalysisParameters(source_resolution_m=30.0))

        short = profile.sustained_grade_percent.get(100.0)
        long = profile.sustained_grade_percent.get(500.0)
        assert short is not None and long is not None
        # The steep part is real over 100 m but averages away over 500 m.
        assert short > long

    def test_single_sample_spike_is_filtered_not_smeared(self) -> None:
        """One bad DEM sample must not become a hill.

        This is the case that proves noise filtering has to be a separate step
        from smoothing. Smoothing alone is a weighted mean, and a mean cannot
        remove an outlier — it spreads it. With smoothing only, this profile
        reported 235 m of ascent on a road that rises 6 m, and the artefact
        became the route's highest point.

        With median-based rejection ahead of smoothing, exactly one sample is
        replaced and the real 6 m rise survives.
        """
        points = [
            ElevationPoint(distance_m=0.0, elevation_m=10.0),
            ElevationPoint(distance_m=30.0, elevation_m=12.0),
            ElevationPoint(distance_m=60.0, elevation_m=400.0),
            ElevationPoint(distance_m=90.0, elevation_m=14.0),
            ElevationPoint(distance_m=120.0, elevation_m=16.0),
        ]

        profile = analyse(
            points, AnalysisParameters(source_resolution_m=30.0, max_plausible_grade_percent=40.0)
        )

        assert profile.filtered_sample_count == 1
        assert profile.ascent_m == pytest.approx(5.0, abs=2.0)
        # The artefact must not survive as the summit either.
        assert profile.max_elevation_m == pytest.approx(16.0, abs=1.0)

    def test_filter_replaces_the_artefact_not_its_neighbours(self) -> None:
        """A spike must not drag its good neighbours out with it.

        With the centre sample excluded from its own median window, the spike
        dominates the median of each adjacent window and the filter replaces the
        two *good* samples either side while leaving the artefact untouched.
        """
        points = [
            ElevationPoint(distance_m=0.0, elevation_m=10.0),
            ElevationPoint(distance_m=30.0, elevation_m=12.0),
            ElevationPoint(distance_m=60.0, elevation_m=400.0),
            ElevationPoint(distance_m=90.0, elevation_m=14.0),
            ElevationPoint(distance_m=120.0, elevation_m=16.0),
        ]

        profile = analyse(points, AnalysisParameters(source_resolution_m=30.0))

        assert profile.filtered_sample_count == 1

    def test_sustained_implausible_gradient_is_discarded_and_counted(self) -> None:
        """A cliff wide enough to survive smoothing must be rejected, not ridden.

        No rideable road sustains 100% for 300 m. Counting the rejection rather
        than silently dropping it means a route whose elevation data is riddled
        with artefacts can be identified as such.
        """
        points = [
            ElevationPoint(distance_m=i * 30.0, elevation_m=10.0 + i * 30.0) for i in range(12)
        ]

        profile = analyse(
            points, AnalysisParameters(source_resolution_m=30.0, max_plausible_grade_percent=40.0)
        )

        assert profile.discarded_grade_count > 0

    def test_grade_distribution_sums_within_measured_distance(self) -> None:
        profile = analyse(ramp(50))

        total = sum(profile.grade_distribution_m.values())
        assert total <= profile.total_distance_m + 1e-6
        assert total > 0.0


class TestRelief:
    def test_descent_is_measured_on_a_falling_route(self) -> None:
        points = [
            ElevationPoint(distance_m=i * 30.0, elevation_m=200.0 - i * 3.0) for i in range(40)
        ]

        profile = analyse(points)

        assert profile.descent_m == pytest.approx(117.0, rel=0.10)
        assert profile.ascent_m == 0.0

    def test_rolling_terrain_accumulates_both(self) -> None:
        points = [
            ElevationPoint(distance_m=i * 30.0, elevation_m=100.0 + 40.0 * math.sin(i / 8.0))
            for i in range(200)
        ]

        profile = analyse(points)

        assert profile.ascent_m > 100.0
        assert profile.descent_m > 100.0

    def test_out_and_back_has_matching_ascent_and_descent(self) -> None:
        """A route that returns to its start must climb what it descends."""
        up = [ElevationPoint(distance_m=i * 30.0, elevation_m=i * 4.0) for i in range(60)]
        down = [
            ElevationPoint(distance_m=(60 + i) * 30.0, elevation_m=(59 - i) * 4.0)
            for i in range(60)
        ]

        profile = analyse(up + down)

        assert profile.ascent_m == pytest.approx(profile.descent_m, rel=0.05)


elevation_point = st.builds(
    ElevationPoint,
    distance_m=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    elevation_m=st.floats(
        min_value=-500.0, max_value=9000.0, allow_nan=False, allow_infinity=False
    ),
)


@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(elevation_point, min_size=2, max_size=120))
def test_relief_is_never_negative(points: list[ElevationPoint]) -> None:
    ordered = [
        ElevationPoint(distance_m=i * 30.0, elevation_m=p.elevation_m) for i, p in enumerate(points)
    ]

    ascent, descent = accumulate_relief(ElevationSpan(points=tuple(ordered)), 3.0)

    assert ascent >= 0.0
    assert descent >= 0.0


@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(elevation_point, min_size=2, max_size=120))
def test_relief_difference_matches_net_change(points: list[ElevationPoint]) -> None:
    """ascent - descent must equal the end-to-start difference.

    This is the invariant that catches double counting and dropped segments in
    the hysteresis logic, which no example-based test reliably finds.
    """
    ordered = [
        ElevationPoint(distance_m=i * 30.0, elevation_m=p.elevation_m) for i, p in enumerate(points)
    ]
    span = ElevationSpan(points=tuple(ordered))

    ascent, descent = accumulate_relief(span, 0.0)
    net = span.elevations[-1] - span.elevations[0]

    assert ascent - descent == pytest.approx(net, abs=1e-6)
