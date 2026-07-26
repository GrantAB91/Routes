"""Climb detection.

A "climb" is not a property of the terrain — it is a decision about which rises
are worth naming. Any detector has to answer three questions, and the answers
are what make two tools disagree about whether a route has four climbs or
eleven:

* how much gain makes a rise a climb rather than a rolling section;
* how shallow a rise can be before it stops counting;
* how much descent is allowed *inside* a climb before it becomes two climbs.

The third is the one that matters most on Irish coastal roads, where a col is
routinely interrupted by 10-20 m dips. Splitting on every dip turns one 300 m
climb into six meaningless fragments; never splitting merges a whole day into
a single "climb". Contour makes the tolerance explicit, stores it with each
detected climb, and refuses to compare climbs found under different parameters.

Detection runs on the noise-filtered, smoothed profile from
:mod:`contour_api.analysis.elevation`. Running it on raw samples finds hundreds
of spurious climbs made of DEM noise.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from .elevation import (
    DEFAULT_GRADE_BUCKETS,
    AnalysisParameters,
    ElevationPoint,
    ElevationProfile,
    ElevationSpan,
    grade_distribution,
    sustained_grade,
)


@dataclass(frozen=True, slots=True)
class ClimbParameters:
    """What counts as a climb. Stored with every result."""

    # A rise smaller than this is rolling terrain, not a climb.
    min_gain_m: float = 30.0
    min_length_m: float = 300.0
    # Below this average, a long gentle drag is a false positive on almost any
    # route; the whole Wild Atlantic Way averages under 1%.
    min_average_grade_percent: float = 3.0
    # Descent permitted inside a single climb before it splits in two. Irish
    # coastal cols dip repeatedly; 20 m keeps a col intact without merging
    # genuinely separate hills.
    max_internal_descent_m: float = 20.0
    grade_windows_m: tuple[float, ...] = (100.0, 500.0, 1000.0)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Climb:
    """One detected climb (§11.7)."""

    sequence: int
    start_distance_m: float
    end_distance_m: float
    length_m: float
    elevation_gain_m: float
    start_elevation_m: float
    end_elevation_m: float
    average_grade_percent: float
    max_grade_percent: float | None
    max_sustained_grade_percent: dict[float, float] = field(default_factory=dict)
    gradient_distribution_m: dict[str, float] = field(default_factory=dict)
    # Distance inside the climb with no elevation coverage. A climb spanning a
    # DEM hole is reported with the hole stated, never as fully measured.
    elevation_gap_m: float = 0.0
    parameters: dict = field(default_factory=dict)

    @property
    def is_fully_measured(self) -> bool:
        return self.elevation_gap_m == 0.0


def _candidate_runs(span: ElevationSpan, max_internal_descent_m: float) -> list[tuple[int, int]]:
    """Find maximal rising runs, tolerating dips up to the given depth.

    Walks the span once, tracking the highest point reached so far in the
    current run. A dip only ends the run when it drops more than
    ``max_internal_descent_m`` below that high point; otherwise the run
    continues and the dip is absorbed. When a run does end, it is trimmed back
    to the high point, so the trailing descent is not counted as part of the
    climb.
    """
    elevations = span.elevations
    if len(elevations) < 2:
        return []

    runs: list[tuple[int, int]] = []
    start = 0
    peak_index = 0

    for index in range(1, len(elevations)):
        if elevations[index] >= elevations[peak_index]:
            peak_index = index
            continue

        if elevations[peak_index] - elevations[index] > max_internal_descent_m:
            # Confirmed end of a climb: keep it up to its high point only.
            if peak_index > start:
                runs.append((start, peak_index))
            # The next climb can only begin from the bottom of this descent,
            # which is not yet known, so restart from here and let the walk
            # find it.
            start = index
            peak_index = index

    if peak_index > start:
        runs.append((start, peak_index))

    return runs


def _trim_to_start_of_rise(span: ElevationSpan, start: int, end: int) -> int:
    """Move the start forward to the lowest point before the summit.

    A run can open on a plateau or a shallow descent that precedes the real
    climb. Reporting that as part of the climb understates its average gradient,
    which is the figure riders judge a climb by.

    The comparison is ``<=`` deliberately, so that on a flat approach — where
    every sample ties for lowest — the *last* of them wins. That is where the
    rise actually begins. Taking the first instead leaves the whole plateau
    inside the climb: a 1 km flat before a 1 km climb at 6% averages out to
    2.99%, which falls under the 3% minimum and makes the climb disappear
    entirely rather than merely reading shallow.
    """
    elevations = span.elevations
    low_index = start
    for index in range(start, end + 1):
        if elevations[index] <= elevations[low_index]:
            low_index = index
    return low_index


def detect(
    profile: ElevationProfile,
    parameters: ClimbParameters | None = None,
) -> list[Climb]:
    """Detect climbs on an analysed profile.

    Operates per span, so no climb is ever reported across a coverage gap: the
    elevation change inside a hole is unknown, and a climb inferred across one
    would be invented.
    """
    params = parameters or ClimbParameters()
    if not profile.spans:
        return []

    # Rebuild the filtered, smoothed spans from the analysed profile so the
    # detector sees exactly the data the ascent figures came from.
    smoothed_by_start = _smoothed_spans(profile)

    climbs: list[Climb] = []
    sequence = 0

    for span in smoothed_by_start:
        for raw_start, end in _candidate_runs(span, params.max_internal_descent_m):
            start = _trim_to_start_of_rise(span, raw_start, end)
            if end <= start:
                continue

            elevations = span.elevations
            gain = elevations[end] - elevations[start]
            length = span.points[end].distance_m - span.points[start].distance_m
            if length <= 0:
                continue

            average_grade = gain / length * 100.0
            if (
                gain < params.min_gain_m
                or length < params.min_length_m
                or average_grade < params.min_average_grade_percent
            ):
                continue

            climb_span = ElevationSpan(points=span.points[start : end + 1])

            sustained: dict[float, float] = {}
            for window in params.grade_windows_m:
                value, _ = sustained_grade(
                    climb_span, window, profile.parameters.max_plausible_grade_percent
                )
                if value is not None:
                    sustained[window] = value

            shortest = min(params.grade_windows_m) if params.grade_windows_m else length
            distribution = grade_distribution(
                climb_span, DEFAULT_GRADE_BUCKETS, min(shortest, length)
            )

            sequence += 1
            climbs.append(
                Climb(
                    sequence=sequence,
                    start_distance_m=span.points[start].distance_m,
                    end_distance_m=span.points[end].distance_m,
                    length_m=length,
                    elevation_gain_m=gain,
                    start_elevation_m=elevations[start],
                    end_elevation_m=elevations[end],
                    average_grade_percent=average_grade,
                    max_grade_percent=sustained.get(shortest),
                    max_sustained_grade_percent=sustained,
                    gradient_distribution_m=distribution,
                    elevation_gap_m=0.0,
                    parameters=params.as_dict(),
                )
            )

    return climbs


def _smoothed_spans(profile: ElevationProfile) -> tuple[ElevationSpan, ...]:
    """Recover the smoothed spans that produced this profile's figures."""
    if not profile.smoothed:
        return profile.spans

    by_distance = {point.distance_m: point for point in profile.smoothed}
    spans: list[ElevationSpan] = []
    for span in profile.spans:
        points = tuple(by_distance.get(point.distance_m, point) for point in span.points)
        spans.append(ElevationSpan(points=points))
    return tuple(spans)


def summarise(climbs: Sequence[Climb]) -> dict:
    """Route-level climb figures (§11.5.15-11.5.17).

    Returns ``None`` for each superlative when there are no climbs, rather than
    zero: "no climb longer than 300 m was detected" is different from "the
    longest climb is 0 m".
    """
    if not climbs:
        return {
            "climb_count": 0,
            "longest_climb": None,
            "steepest_climb": None,
            "greatest_gain_climb": None,
            "total_climb_distance_m": 0.0,
        }

    longest = max(climbs, key=lambda c: c.length_m)
    steepest = max(climbs, key=lambda c: c.average_grade_percent)
    greatest = max(climbs, key=lambda c: c.elevation_gain_m)

    return {
        "climb_count": len(climbs),
        "longest_climb": longest.sequence,
        "steepest_climb": steepest.sequence,
        "greatest_gain_climb": greatest.sequence,
        "total_climb_distance_m": sum(c.length_m for c in climbs),
    }


def assign_to_stages(
    climbs: Sequence[Climb], stage_boundaries_m: Sequence[float]
) -> dict[int, list[int]]:
    """Map climbs onto stages by their start distance (§11.7.11).

    A climb crossing a stage boundary belongs to the stage it starts in, which
    is the one whose rider meets it. Boundaries are the cumulative end distance
    of each stage.
    """
    assignment: dict[int, list[int]] = {i: [] for i in range(len(stage_boundaries_m))}
    for climb in climbs:
        for index, boundary in enumerate(stage_boundaries_m):
            if climb.start_distance_m < boundary:
                assignment[index].append(climb.sequence)
                break
        else:
            if stage_boundaries_m:
                assignment[len(stage_boundaries_m) - 1].append(climb.sequence)
    return assignment


def detect_from_points(
    points: Sequence[ElevationPoint],
    elevation_parameters: AnalysisParameters | None = None,
    climb_parameters: ClimbParameters | None = None,
) -> list[Climb]:
    """Convenience wrapper: analyse then detect, with consistent parameters."""
    from .elevation import analyse

    profile = analyse(points, elevation_parameters)
    return detect(profile, climb_parameters)
