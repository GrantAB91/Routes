"""Elevation profile analysis.

The whole module exists to make one number defensible: total ascent. Naively
summing every positive difference between consecutive elevation samples produces
a figure that is dominated by the noise of the elevation model rather than by
the terrain, and that grows without limit as the sampling interval shrinks. Two
riders comparing "1,400 m of climbing" from two tools are usually comparing two
different noise-handling policies, not two different routes.

Contour's policy is stated here and in ``docs/elevation_method.md``:

* sample no finer than the source's own resolution (§11.3);
* smooth before differencing, with the window derived from that resolution;
* ignore rises below a minimum-gain threshold, because a DEM cannot resolve
  them;
* never interpolate across a coverage gap — accumulate around it and report the
  distance that was unmeasurable (§11.5.18).

Every function takes its parameters explicitly and returns them alongside the
result, so a stored figure can always be reproduced and two routes are only
ever compared under identical parameters.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

# Grade analysis windows in metres (§11.6). A grade figure is meaningless
# without the distance it was measured over: 20% over 25 m is a driveway lip,
# 20% over 1 km is a mountain pass.
DEFAULT_GRADE_WINDOWS_M: tuple[float, ...] = (25.0, 100.0, 500.0, 1000.0)


@dataclass(frozen=True, slots=True)
class ElevationPoint:
    """One sample along a route.

    ``elevation_m`` is ``None`` where the source has no coverage. That is a
    measurement outcome, not a missing field, and it is preserved all the way
    through the analysis.
    """

    distance_m: float
    elevation_m: float | None


@dataclass(frozen=True, slots=True)
class AnalysisParameters:
    """Everything that affects the numbers, recorded with them.

    ``source_resolution_m`` is the DEM's native horizontal resolution. It drives
    the smoothing window rather than being a free choice: smoothing over less
    than one cell cannot remove sampling noise, and smoothing over many cells
    starts erasing real terrain.
    """

    source_resolution_m: float = 30.0
    sample_interval_m: float = 30.0
    smoothing_window_m: float = 90.0
    # Rises below this are treated as unresolvable by the elevation model rather
    # than as climbing. Copernicus GLO-30 publishes a vertical accuracy well
    # above 1 m, so counting sub-metre wobble as ascent is counting noise.
    min_gain_threshold_m: float = 3.0
    grade_windows_m: tuple[float, ...] = DEFAULT_GRADE_WINDOWS_M
    # Grades beyond this are rejected as artefacts. Rideable roads do not exceed
    # it; values above usually mean two samples landed on opposite sides of a
    # cliff, a bridge deck, or a DEM seam.
    max_plausible_grade_percent: float = 40.0
    # Populated by validated(); windows the source cannot support.
    dropped_windows_m: tuple[float, ...] = ()

    def validated(self) -> AnalysisParameters:
        """Return parameters corrected to what the source can actually support.

        Requesting a 10 m sampling interval from a 30 m DEM does not produce
        10 m of detail; it produces interpolation presented as measurement
        (§11.3). The interval is raised to the source resolution instead, and
        the smoothing window is kept to at least three samples so it can
        actually suppress single-sample noise.
        """
        interval = max(self.sample_interval_m, self.source_resolution_m)
        smoothing = max(self.smoothing_window_m, interval * 3.0)
        windows = tuple(w for w in self.grade_windows_m if w >= interval)
        return replace(
            self,
            sample_interval_m=interval,
            smoothing_window_m=smoothing,
            grade_windows_m=windows or (interval,),
            # Recorded at the moment of filtering. Recomputing this later cannot
            # work: by then grade_windows_m no longer contains the windows that
            # were removed, so the comparison finds nothing and the omission is
            # reported as "none dropped". Running against a real 30.9 m DEM is
            # what exposed that — the 25 m window vanished silently.
            dropped_windows_m=tuple(w for w in self.grade_windows_m if w < interval),
        )

    def dropped_windows(self) -> tuple[float, ...]:
        """Windows the source is too coarse to support, for honest reporting.

        Only meaningful on validated parameters; unvalidated ones have not yet
        dropped anything.
        """
        return self.dropped_windows_m


@dataclass(frozen=True, slots=True)
class ElevationSpan:
    """A contiguous run of samples that all have elevation.

    Analysis happens per span. A route with a DEM hole becomes several spans,
    and nothing is ever computed across the hole.
    """

    points: tuple[ElevationPoint, ...]

    def __post_init__(self) -> None:
        # The invariant is enforced here rather than asserted at each use site.
        # Assertions are stripped under `python -O`, which would turn a data
        # error into arithmetic on None in exactly the deployment where it is
        # hardest to diagnose.
        if any(point.elevation_m is None for point in self.points):
            raise ValueError("ElevationSpan cannot contain samples without elevation")

    @property
    def elevations(self) -> tuple[float, ...]:
        """Elevations, known non-null by construction."""
        return tuple(point.elevation_m for point in self.points)  # type: ignore[misc]

    @property
    def start_m(self) -> float:
        return self.points[0].distance_m

    @property
    def end_m(self) -> float:
        return self.points[-1].distance_m

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m


@dataclass(frozen=True, slots=True)
class ElevationProfile:
    """Result of analysing one route's elevation (§11.5)."""

    parameters: AnalysisParameters
    total_distance_m: float
    ascent_m: float | None
    descent_m: float | None
    min_elevation_m: float | None
    max_elevation_m: float | None
    net_elevation_change_m: float | None
    # Distance with no elevation coverage. Reported next to ascent so the
    # figure is never read as covering the whole route when it does not.
    gap_distance_m: float
    coverage_ratio: float
    max_grade_percent: float | None
    # {window_m: steepest sustained grade over that window}
    sustained_grade_percent: dict[float, float] = field(default_factory=dict)
    # {"0-3": metres, "3-6": metres, ...} across the whole route.
    grade_distribution_m: dict[str, float] = field(default_factory=dict)
    discarded_grade_count: int = 0
    # Samples replaced by the local median as implausible artefacts.
    filtered_sample_count: int = 0
    smoothed: tuple[ElevationPoint, ...] = ()
    spans: tuple[ElevationSpan, ...] = ()

    @property
    def has_complete_coverage(self) -> bool:
        return self.gap_distance_m == 0.0


def split_into_spans(points: Sequence[ElevationPoint]) -> tuple[ElevationSpan, ...]:
    """Group samples into runs that have elevation, discarding gaps.

    Gaps are not bridged. A route crossing a DEM hole yields two spans, and the
    elevation change across the hole is unknown rather than zero — bridging it
    would invent either a climb or a flat.
    """
    spans: list[ElevationSpan] = []
    current: list[ElevationPoint] = []

    for point in points:
        if point.elevation_m is None:
            if len(current) >= 2:
                spans.append(ElevationSpan(points=tuple(current)))
            current = []
        else:
            current.append(point)

    if len(current) >= 2:
        spans.append(ElevationSpan(points=tuple(current)))
    return tuple(spans)


def gap_distance(points: Sequence[ElevationPoint]) -> float:
    """Total route distance not covered by elevation data.

    A gap runs from the last known sample to the next one, so the unmeasured
    distance includes the approach on both sides rather than only the null
    samples themselves.
    """
    if len(points) < 2:
        return 0.0

    total = 0.0
    last_known_index: int | None = None

    for index, point in enumerate(points):
        if point.elevation_m is not None:
            if last_known_index is not None and index - last_known_index > 1:
                total += points[index].distance_m - points[last_known_index].distance_m
            last_known_index = index

    # Leading and trailing gaps.
    first_known = next((i for i, p in enumerate(points) if p.elevation_m is not None), None)
    if first_known is None:
        return points[-1].distance_m - points[0].distance_m
    if first_known > 0:
        total += points[first_known].distance_m - points[0].distance_m

    last_known = next(
        (i for i in range(len(points) - 1, -1, -1) if points[i].elevation_m is not None),
        None,
    )
    if last_known is not None and last_known < len(points) - 1:
        total += points[-1].distance_m - points[last_known].distance_m

    return total


def reject_spikes(
    span: ElevationSpan, window_m: float, max_plausible_grade_percent: float
) -> tuple[ElevationSpan, int]:
    """Replace isolated implausible samples with the local median.

    This is noise filtering, and it is a separate step from smoothing (§11.2.7
    versus §11.2.8) because the two defend against different faults and only one
    of them works here.

    Smoothing is a weighted *mean*, and a mean is not robust: a single sample
    390 m out — the signature of a DEM seam, a bridge deck, or a void fill —
    is not removed by averaging, it is spread across the whole window. In
    testing, one such sample on an otherwise flat 150 m of road still produced
    235 m of phantom ascent after smoothing. A median ignores the outlier
    entirely.

    A sample is judged implausible when it differs from the median of its
    neighbourhood by more than the steepest real terrain could account for over
    that distance. Substituting the local median is filtering an erroneous
    reading, not inventing a missing one: the replacement is derived from
    neighbouring measurements, and the number of substitutions is returned so it
    can be reported rather than hidden.
    """
    points = span.points
    if len(points) < 3 or window_m <= 0:
        return span, 0

    half = window_m / 2.0
    # The largest elevation difference real terrain could produce across half
    # the window. Anything beyond it is an artefact, not a hill.
    plausible_deviation_m = max_plausible_grade_percent / 100.0 * half

    elevations = span.elevations
    filtered: list[ElevationPoint] = []
    replaced = 0

    for index, point in enumerate(points):
        low = point.distance_m - half
        high = point.distance_m + half
        # The centre sample is part of its own window, as in a standard median
        # filter. Excluding it lets a spike dominate the median of each
        # *neighbouring* window, so the filter replaces the good samples on
        # either side of an artefact instead of the artefact itself.
        neighbourhood = [
            elevations[i] for i in range(len(points)) if low <= points[i].distance_m <= high
        ]
        if len(neighbourhood) < 3:
            filtered.append(point)
            continue

        neighbourhood.sort()
        middle = len(neighbourhood) // 2
        median = (
            neighbourhood[middle]
            if len(neighbourhood) % 2
            else (neighbourhood[middle - 1] + neighbourhood[middle]) / 2.0
        )

        if abs(elevations[index] - median) > plausible_deviation_m:
            filtered.append(ElevationPoint(distance_m=point.distance_m, elevation_m=median))
            replaced += 1
        else:
            filtered.append(point)

    return ElevationSpan(points=tuple(filtered)), replaced


def smooth_span(span: ElevationSpan, window_m: float) -> ElevationSpan:
    """Distance-weighted moving average over ``window_m``.

    Averaging by distance rather than by sample count matters because route
    samples are not evenly spaced: they cluster at bends where the geometry has
    more vertices. A count-based window would smooth hard through hairpins and
    barely at all on straights, which shows up as phantom climbing exactly where
    mountain roads switchback.
    """
    if window_m <= 0 or len(span.points) < 3:
        return span

    half = window_m / 2.0
    points = span.points
    smoothed: list[ElevationPoint] = []

    for index, point in enumerate(points):
        low = point.distance_m - half
        high = point.distance_m + half

        weighted_sum = 0.0
        weight_total = 0.0
        cursor = index
        while cursor >= 0 and points[cursor].distance_m >= low:
            cursor -= 1
        cursor += 1

        while cursor < len(points) and points[cursor].distance_m <= high:
            neighbour = points[cursor]
            # Triangular weighting: nearer samples count for more, so the filter
            # does not shift peaks the way a boxcar does.
            weight = 1.0 - abs(neighbour.distance_m - point.distance_m) / half
            weight = max(weight, 1e-6)
            weighted_sum += neighbour.elevation_m * weight  # type: ignore[operator]
            weight_total += weight
            cursor += 1

        value = weighted_sum / weight_total if weight_total else point.elevation_m
        smoothed.append(ElevationPoint(distance_m=point.distance_m, elevation_m=value))

    return ElevationSpan(points=tuple(smoothed))


def accumulate_relief(span: ElevationSpan, min_gain_threshold_m: float) -> tuple[float, float]:
    """Cumulative ascent and descent over one span, in metres.

    Uses hysteresis rather than summing every positive difference. A run of
    rises only becomes ascent once it exceeds ``min_gain_threshold_m``, so DEM
    wobble below the model's own vertical accuracy is not counted. Without this
    the total grows steadily as sampling gets denser, which is the classic
    symptom of measuring noise rather than terrain.
    """
    if len(span.points) < 2:
        return 0.0, 0.0

    ascent = 0.0
    descent = 0.0
    elevations = span.elevations
    # Elevation of the last confirmed turning point.
    anchor = elevations[0]
    extreme = anchor
    rising: bool | None = None

    for elevation in elevations[1:]:
        if rising is None:
            if abs(elevation - anchor) >= min_gain_threshold_m:
                rising = elevation > anchor
                extreme = elevation
            continue

        if rising:
            if elevation > extreme:
                extreme = elevation
            elif extreme - elevation >= min_gain_threshold_m:
                # Confirmed reversal: bank the climb and turn around.
                ascent += extreme - anchor
                anchor = extreme
                extreme = elevation
                rising = False
        else:
            if elevation < extreme:
                extreme = elevation
            elif elevation - extreme >= min_gain_threshold_m:
                descent += anchor - extreme
                anchor = extreme
                extreme = elevation
                rising = True

    # Bank whatever the final run was doing.
    if rising is True:
        ascent += extreme - anchor
    elif rising is False:
        descent += anchor - extreme

    return ascent, descent


def sustained_grade(
    span: ElevationSpan, window_m: float, max_plausible_grade_percent: float
) -> tuple[float | None, int]:
    """Steepest average grade sustained over ``window_m`` within one span.

    Returns the grade and how many candidate windows were rejected as
    implausible. Rejections are counted rather than hidden: a route with many of
    them is one whose elevation data should be distrusted, and that is worth
    surfacing.
    """
    points = span.points
    if len(points) < 2 or span.length_m < window_m:
        return None, 0

    steepest: float | None = None
    discarded = 0
    end = 0

    for start in range(len(points)):
        target = points[start].distance_m + window_m
        if end < start:
            end = start
        while end < len(points) and points[end].distance_m < target:
            end += 1
        if end >= len(points):
            break

        run = points[end].distance_m - points[start].distance_m
        if run <= 0:
            continue
        rise = points[end].elevation_m - points[start].elevation_m  # type: ignore[operator]
        grade = rise / run * 100.0

        if abs(grade) > max_plausible_grade_percent:
            discarded += 1
            continue
        if steepest is None or grade > steepest:
            steepest = grade

    return steepest, discarded


def grade_distribution(
    span: ElevationSpan, buckets: Sequence[tuple[float, float]], window_m: float
) -> dict[str, float]:
    """Distance spent in each grade band, measured over ``window_m``.

    The window is part of the answer, not an implementation detail: the same
    road yields very different distributions at 25 m and 500 m.
    """
    result: dict[str, float] = {f"{low:g}-{high:g}": 0.0 for low, high in buckets}
    points = span.points
    if len(points) < 2:
        return result

    end = 0
    for start in range(len(points) - 1):
        target = points[start].distance_m + window_m
        if end < start:
            end = start
        while end < len(points) and points[end].distance_m < target:
            end += 1
        if end >= len(points):
            end = len(points) - 1

        run = points[end].distance_m - points[start].distance_m
        if run <= 0:
            continue
        rise = points[end].elevation_m - points[start].elevation_m  # type: ignore[operator]
        grade = abs(rise / run * 100.0)
        step = points[start + 1].distance_m - points[start].distance_m

        for low, high in buckets:
            if low <= grade < high:
                result[f"{low:g}-{high:g}"] += step
                break

    return result


DEFAULT_GRADE_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 3.0),
    (3.0, 6.0),
    (6.0, 9.0),
    (9.0, 12.0),
    (12.0, 15.0),
    (15.0, math.inf),
)


def analyse(
    points: Sequence[ElevationPoint],
    parameters: AnalysisParameters | None = None,
) -> ElevationProfile:
    """Full elevation analysis of one route.

    Returns ``None`` for every derived figure when no span has coverage, rather
    than zero. Zero ascent is a claim about the terrain; ``None`` is the honest
    statement that nothing is known.
    """
    params = (parameters or AnalysisParameters()).validated()

    total_distance = points[-1].distance_m - points[0].distance_m if len(points) >= 2 else 0.0
    gaps = gap_distance(points)
    spans = split_into_spans(points)

    if not spans:
        return ElevationProfile(
            parameters=params,
            total_distance_m=total_distance,
            ascent_m=None,
            descent_m=None,
            min_elevation_m=None,
            max_elevation_m=None,
            net_elevation_change_m=None,
            gap_distance_m=gaps,
            coverage_ratio=0.0,
            max_grade_percent=None,
        )

    # Noise filtering first, then smoothing. A mean filter cannot remove an
    # outlier, only spread it, so the order matters (see reject_spikes).
    filtered_spans = []
    filtered_sample_count = 0
    for span in spans:
        cleaned, replaced = reject_spikes(
            span, params.smoothing_window_m, params.max_plausible_grade_percent
        )
        filtered_spans.append(cleaned)
        filtered_sample_count += replaced

    smoothed_spans = tuple(smooth_span(span, params.smoothing_window_m) for span in filtered_spans)

    ascent = 0.0
    descent = 0.0
    for span in smoothed_spans:
        span_ascent, span_descent = accumulate_relief(span, params.min_gain_threshold_m)
        ascent += span_ascent
        descent += span_descent

    # Extremes come from the noise-filtered samples but before smoothing:
    # smoothing would clip a genuine summit, while an unfiltered artefact would
    # become one.
    elevations = [e for span in filtered_spans for e in span.elevations]
    minimum = min(elevations)
    maximum = max(elevations)

    sustained: dict[float, float] = {}
    discarded_total = 0
    for window in params.grade_windows_m:
        best: float | None = None
        for span in smoothed_spans:
            value, discarded = sustained_grade(span, window, params.max_plausible_grade_percent)
            discarded_total += discarded
            if value is not None and (best is None or value > best):
                best = value
        if best is not None:
            sustained[window] = best

    # The headline "maximum gradient" is the shortest window's figure, since
    # that is the steepest thing a rider actually meets.
    shortest_window = min(params.grade_windows_m) if params.grade_windows_m else None
    max_grade = sustained.get(shortest_window) if shortest_window is not None else None

    distribution: dict[str, float] = {
        f"{low:g}-{high:g}": 0.0 for low, high in DEFAULT_GRADE_BUCKETS
    }
    for span in smoothed_spans:
        span_distribution = grade_distribution(
            span, DEFAULT_GRADE_BUCKETS, shortest_window or params.sample_interval_m
        )
        for key, value in span_distribution.items():
            distribution[key] += value

    measured = sum(span.length_m for span in spans)
    coverage = measured / total_distance if total_distance > 0 else 0.0

    return ElevationProfile(
        parameters=params,
        total_distance_m=total_distance,
        ascent_m=ascent,
        descent_m=descent,
        min_elevation_m=minimum,
        max_elevation_m=maximum,
        net_elevation_change_m=(
            spans[-1].points[-1].elevation_m - spans[0].points[0].elevation_m  # type: ignore[operator]
            if len(spans) == 1
            # Across a gap the net change is unknowable, because the elevation
            # at the gap edges says nothing about what happened inside it.
            else None
        ),
        gap_distance_m=gaps,
        coverage_ratio=min(coverage, 1.0),
        max_grade_percent=max_grade,
        sustained_grade_percent=sustained,
        grade_distribution_m=distribution,
        discarded_grade_count=discarded_total,
        filtered_sample_count=filtered_sample_count,
        smoothed=tuple(p for span in smoothed_spans for p in span.points),
        spans=spans,
    )
