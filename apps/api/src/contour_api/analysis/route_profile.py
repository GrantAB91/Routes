"""Attaching a measured elevation profile to an attributed route.

The gradient constraint (§7.4.2) cannot be evaluated from geometry alone, and
the elevation analysis cannot be run from attributes alone. This module is the
join: it samples the route's own shape, runs the analysis in
:mod:`contour_api.analysis.elevation`, and writes the result back onto each
segment so the validator can read it.

Two decisions here carry the honesty requirements through:

* sampling never runs finer than the source can support. The provider is asked
  for its own resolution, and where it can say, that is what bounds the sample
  interval — a 5 m interval over a 30 m DEM would produce grades that are
  interpolation artefacts and nothing else (§11.3);
* a segment the DEM does not cover gets ``elevation_status`` of ``UNKNOWN`` and
  no grade. It is not given a grade of zero, and the gap is added to the route's
  ``elevation_gap_m`` so the gradient check reports it as unevaluated rather
  than passing over it silently (§2.6, §7.6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from itertools import pairwise

from ..models.enums import KnowledgeStatus
from ..providers.elevation import ElevationProvider
from ..providers.routing import LatLon
from ..routing.model import RouteView, SegmentView
from ..routing.validation import haversine_m
from .elevation import (
    AnalysisParameters,
    ElevationPoint,
    ElevationProfile,
    analyse,
)

logger = logging.getLogger(__name__)

# Sampling below this never buys accuracy from any elevation source Contour
# supports, and it multiplies the number of points on a 2,500 km route into
# the millions.
MIN_SAMPLE_INTERVAL_M = 10.0


@dataclass(frozen=True, slots=True)
class SampledRoute:
    """Points along a route with the distance each sits at."""

    points: tuple[LatLon, ...]
    distances_m: tuple[float, ...]

    def __len__(self) -> int:
        return len(self.points)


def sample_positions(
    coordinates: tuple[LatLon, ...],
    interval_m: float,
) -> SampledRoute:
    """Place samples along a polyline at a fixed spacing.

    Every original vertex is kept in addition to the interpolated points. A
    vertex is where the road actually changes direction, and dropping one in
    favour of a regular grid can cut the top off a hairpin — which is to say,
    the steepest part of the climb.
    """
    if not coordinates:
        return SampledRoute((), ())
    if len(coordinates) == 1:
        return SampledRoute(coordinates, (0.0,))

    points: list[LatLon] = [coordinates[0]]
    distances: list[float] = [0.0]
    travelled = 0.0

    for start, end in pairwise(coordinates):
        leg = haversine_m(start, end)
        if leg <= 0:
            continue

        # Interpolate along this leg wherever a sample is due.
        next_sample = (int(travelled / interval_m) + 1) * interval_m
        while next_sample < travelled + leg:
            fraction = (next_sample - travelled) / leg
            points.append(
                LatLon(
                    lat=start.lat + (end.lat - start.lat) * fraction,
                    lon=start.lon + (end.lon - start.lon) * fraction,
                )
            )
            distances.append(next_sample)
            next_sample += interval_m

        travelled += leg
        points.append(end)
        distances.append(travelled)

    return SampledRoute(tuple(points), tuple(distances))


def resolve_interval(
    provider: ElevationProvider,
    at: LatLon,
    requested_m: float,
) -> tuple[float, str | None]:
    """Clamp the sample interval to what the source can actually resolve.

    Returns the interval and, when it was raised, a note recording why. The note
    reaches the user: a profile computed at 30 m when 10 m was asked for is a
    different measurement, and saying so is the difference between a figure and
    a claim.
    """
    native = provider.resolution_m(at)
    floor = max(MIN_SAMPLE_INTERVAL_M, requested_m)

    if native is None:
        return floor, (
            "The elevation source does not report its own resolution, so the "
            f"{floor:g} m sample interval is Contour's assumption rather than a "
            "property of the data."
        )

    if native > floor:
        return native, (
            f"Sampling was widened from {floor:g} m to {native:g} m to match the "
            "elevation source's own resolution. A finer interval would report "
            "interpolation as terrain."
        )

    return floor, None


@dataclass(frozen=True, slots=True)
class RouteElevation:
    """A route's profile, and the enriched route the validator should see."""

    route: RouteView
    profile: ElevationProfile
    sample_interval_m: float
    notes: tuple[str, ...] = ()

    @property
    def gradient_window_m(self) -> float:
        """The window every gradient here was measured over.

        One value for the route and for each of its segments. A figure without
        it is not comparable with anything (§11.4).
        """
        windows = self.profile.parameters.grade_windows_m
        return min(windows) if windows else self.sample_interval_m

    def as_dict(self) -> dict:
        """The profile as the API reports it.

        ``None`` is used throughout for "not known". No figure here is ever
        filled with zero to make the shape uniform.
        """
        return {
            "distance_m": round(self.profile.total_distance_m, 1),
            "ascent_m": _rounded(self.profile.ascent_m),
            "descent_m": _rounded(self.profile.descent_m),
            "min_elevation_m": _rounded(self.profile.min_elevation_m),
            "max_elevation_m": _rounded(self.profile.max_elevation_m),
            "net_elevation_change_m": _rounded(self.profile.net_elevation_change_m),
            "max_grade_percent": _rounded(self.profile.max_grade_percent, 1),
            "sustained_grade_percent": {
                f"{window:g}": round(value, 1)
                for window, value in self.profile.sustained_grade_percent.items()
            },
            "grade_distribution_m": {
                band: round(value, 1) for band, value in self.profile.grade_distribution_m.items()
            },
            "coverage": {
                "ratio": round(self.profile.coverage_ratio, 4),
                "gap_distance_m": round(self.profile.gap_distance_m, 1),
                "complete": self.profile.has_complete_coverage,
            },
            "method": {
                "sample_interval_m": self.sample_interval_m,
                "gradient_window_m": self.gradient_window_m,
                "smoothing_window_m": self.profile.parameters.smoothing_window_m,
                "min_gain_threshold_m": self.profile.parameters.min_gain_threshold_m,
                "grade_windows_m": list(self.profile.parameters.grade_windows_m),
                "dropped_grade_windows_m": list(self.profile.parameters.dropped_windows_m),
                "samples_filtered_as_artefacts": self.profile.filtered_sample_count,
                "grades_discarded_as_implausible": self.profile.discarded_grade_count,
            },
            "notes": list(self.notes),
        }


def _rounded(value: float | None, digits: int = 0) -> float | None:
    if value is None:
        return None
    return round(value, digits) if digits else round(value)


def _segment_grade(
    smoothed: tuple[ElevationPoint, ...],
    start_m: float,
    end_m: float,
    window_m: float,
) -> tuple[float | None, float]:
    """Steepest grade over ``window_m`` anywhere on this segment, and its coverage.

    The window is the point of this function. Measuring sample-to-sample instead
    — which is what an earlier version of this did — produces a *different*
    measurement from the route-level figure, and running it against a real
    30.9 m DEM showed how badly: the route reported a 10.2% maximum while
    individual segments claimed 18.6%. Both were arithmetically right and the
    pair was unusable, because a violation could not be explained against the
    number the rider had been shown. §11.4 requires a gradient to travel with
    its window; it follows that two gradients shown together must share one.

    The window is allowed to extend past the segment's own ends. A segment is an
    artefact of where the routing graph happens to split a road — often 75 m,
    shorter than the window — and the rider still climbs whatever the road does
    there. Confining the window inside the segment would report almost every
    segment as unmeasurable and leave the gradient constraint permanently
    unevaluable, which reads as missing data rather than as the arrangement of
    edges it actually is.

    Returns the steepest grade and how much of the segment had elevation
    coverage. Both are needed: the grade is what the constraint checks, and the
    coverage is what distinguishes flat ground from ground nobody measured.
    """
    measured = [p for p in smoothed if p.elevation_m is not None]
    if len(measured) < 2:
        return None, 0.0

    covered = 0.0
    for a, b in pairwise(measured):
        overlap = min(b.distance_m, end_m) - max(a.distance_m, start_m)
        if overlap > 0:
            covered += overlap

    if covered <= 0:
        return None, 0.0

    steepest: float | None = None
    end_index = 0
    for start_index, origin in enumerate(measured):
        # Every window that overlaps the segment counts, including one starting
        # just before it and one ending just after.
        if origin.distance_m > end_m:
            break

        target = origin.distance_m + window_m
        if end_index < start_index:
            end_index = start_index
        while end_index < len(measured) and measured[end_index].distance_m < target:
            end_index += 1
        if end_index >= len(measured):
            break

        far = measured[end_index]
        if far.distance_m < start_m:
            continue

        run = far.distance_m - origin.distance_m
        if run <= 0:
            continue
        grade = abs((far.elevation_m - origin.elevation_m) / run * 100.0)  # type: ignore[operator]
        if steepest is None or grade > steepest:
            steepest = grade

    if steepest is None:
        # The route is shorter than one window. Measuring end to end is the
        # only honest reading, and it is still reported over a stated distance.
        run = measured[-1].distance_m - measured[0].distance_m
        if run <= 0:
            return None, covered
        steepest = abs(
            (measured[-1].elevation_m - measured[0].elevation_m) / run * 100.0  # type: ignore[operator]
        )

    return steepest, covered


async def attach_elevation(
    route: RouteView,
    provider: ElevationProvider,
    *,
    parameters: AnalysisParameters | None = None,
    requested_interval_m: float = 30.0,
) -> RouteElevation:
    """Sample, analyse, and write the result back onto the route's segments.

    The returned route is a new value; nothing is mutated. Segments whose part of
    the route the source does not cover keep ``elevation_status`` of ``UNKNOWN``,
    which is what makes the gradient constraint report them as unevaluated
    instead of compliant.
    """
    coordinates = tuple(point for segment in route.segments for point in segment.coordinates)
    if len(coordinates) < 2:
        coordinates = (route.origin, route.destination)

    interval_m, note = resolve_interval(provider, coordinates[0], requested_interval_m)
    notes = (note,) if note else ()

    sampled = sample_positions(coordinates, interval_m)
    elevations = await provider.sample(sampled.points)
    points = tuple(
        ElevationPoint(distance_m=distance, elevation_m=elevation)
        for distance, elevation in zip(sampled.distances_m, elevations, strict=True)
    )

    params = parameters or AnalysisParameters(sample_interval_m=interval_m)
    profile = analyse(points, params)

    # Grades are read from the smoothed series the analysis produced, not from
    # the raw samples: a single DEM artefact would otherwise put a 40% wall on a
    # segment that the route-level analysis has already rejected as noise.
    smoothed = profile.smoothed or points

    # The same window the route-level maximum uses, so a segment's gradient and
    # the headline figure are the same measurement rather than two.
    window_m = (
        min(profile.parameters.grade_windows_m)
        if profile.parameters.grade_windows_m
        else interval_m
    )

    segments: list[SegmentView] = []
    for segment in route.segments:
        grade, covered_m = _segment_grade(
            smoothed, segment.start_distance_m, segment.end_distance_m, window_m
        )
        # Partial coverage is not coverage. Requiring most of the segment to be
        # measured stops one sampled metre at the edge of a DEM hole from
        # standing in for a kilometre nobody measured.
        measured = grade is not None and covered_m >= 0.5 * segment.distance_m
        segments.append(
            replace(
                segment,
                max_grade_percent=grade if measured else None,
                elevation_status=(KnowledgeStatus.KNOWN if measured else KnowledgeStatus.UNKNOWN),
            )
        )

    enriched = replace(
        route,
        segments=tuple(segments),
        elevation_gap_m=profile.gap_distance_m,
        metadata={
            **route.metadata,
            "elevation_source": provider.name,
        },
    )

    if profile.coverage_ratio < 1.0:
        logger.info(
            "elevation covers %.1f%% of the route; %.0f m has no data",
            profile.coverage_ratio * 100,
            profile.gap_distance_m,
        )

    return RouteElevation(
        route=enriched,
        profile=profile,
        sample_interval_m=interval_m,
        notes=notes,
    )
