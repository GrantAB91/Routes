"""Route validation and the feasibility verdict (§7.10-7.12).

Validation answers one question: may this route be presented to the user as
meeting what they asked for? The answer has four values, not two, and the
distinction that carries the most weight is between *proven compliant* and
*not disproved*. A route across 40 km of unsurveyed lanes has not been shown
to be legal, paved or rideable; saying so plainly is the whole point.

Checks that cannot be performed are recorded as unevaluated with the reason,
never quietly passed. A validation report that lists ten passing checks when
three of them could not run is worse than no report.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..models.enums import FeasibilityVerdict, KnowledgeStatus
from ..providers.routing import LatLon
from .constraints import ConstraintResult, ConstraintSet, Outcome
from .model import RouteView

EARTH_RADIUS_M = 6_371_008.8

# Two consecutive segments are treated as joined within this distance. Route
# geometry is stored at six decimal places (~0.1 m), and engines commonly repeat
# or drop a shared vertex between edges, so an exact-match test produces
# constant false gaps.
CONNECTIVITY_TOLERANCE_M = 25.0

# How far a route may start or finish from the requested point before it is a
# different journey. Snapping to the nearest road legitimately moves an endpoint
# by tens of metres, and more in rural Ireland where a house may sit well back
# from the network.
ENDPOINT_TOLERANCE_M = 250.0


def haversine_m(a: LatLon, b: LatLon) -> float:
    """Great-circle distance in metres.

    Used for connectivity and endpoint checks, where the distances are small
    and the error from ignoring the ellipsoid is far below the tolerances above.
    Route length itself comes from the routing engine, not from this.
    """
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, h)))


@dataclass(frozen=True, slots=True)
class Check:
    """One structural check from §7.10."""

    name: str
    outcome: Outcome
    detail: str = ""
    segment_indices: tuple[int, ...] = ()

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.SATISFIED


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    verdict: FeasibilityVerdict
    checks: tuple[Check, ...]
    constraint_results: tuple[ConstraintResult, ...]
    summary: str
    resolve_attempts: int = 1
    exhausted_attempts: bool = False

    @property
    def violations(self) -> tuple[ConstraintResult, ...]:
        return tuple(r for r in self.constraint_results if r.outcome is Outcome.VIOLATED)

    @property
    def unevaluated(self) -> tuple[Check | ConstraintResult, ...]:
        """Checks this route's own data was insufficient for."""
        return tuple(
            item
            for item in (*self.checks, *self.constraint_results)
            if item.outcome is Outcome.UNEVALUABLE
        )

    @property
    def unavailable_checks(self) -> tuple[Check, ...]:
        """Checks this deployment cannot perform for any route.

        Shown as standing limitations alongside the result, so a clean verdict
        never implies Contour checked something it has no source for.
        """
        return tuple(c for c in self.checks if c.outcome is Outcome.NOT_AVAILABLE)

    @property
    def is_structurally_valid(self) -> bool:
        return all(c.outcome is not Outcome.VIOLATED for c in self.checks)

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "checks": [
                {
                    "check": c.name,
                    "outcome": c.outcome.value,
                    "detail": c.detail,
                    "segment_indices": list(c.segment_indices),
                }
                for c in self.checks
            ],
            "violations": [
                {
                    "constraint_key": r.key,
                    "stated_as": r.stated_as,
                    "requested": r.requested,
                    "observed": r.observed,
                    "segment_indices": list(r.segment_indices),
                    "distance_m": r.affected_distance_m,
                    "detail": r.detail,
                }
                for r in self.violations
            ],
            "unevaluated": [
                {
                    "key": getattr(item, "key", None) or getattr(item, "name", ""),
                    "detail": item.detail,
                }
                for item in self.unevaluated
            ],
            "unavailable_checks": [
                {"check": c.name, "detail": c.detail} for c in self.unavailable_checks
            ],
            "resolve_attempts": self.resolve_attempts,
            "exhausted_attempts": self.exhausted_attempts,
            "summary": self.summary,
        }


# --- structural checks -------------------------------------------------------


def check_connected_geometry(route: RouteView) -> Check:
    """§7.10.1 and §7.10.4: the route must be one continuous line.

    A gap is only acceptable where a ferry explains it. An unexplained jump
    means the engine returned disjoint legs, which renders every distance and
    ascent figure downstream meaningless.
    """
    gaps: list[int] = []
    worst = 0.0

    for previous, current in zip(route.segments, route.segments[1:], strict=False):
        if not previous.coordinates or not current.coordinates:
            continue
        separation = haversine_m(previous.coordinates[-1], current.coordinates[0])
        if separation <= CONNECTIVITY_TOLERANCE_M:
            continue
        # A ferry on either side of the join explains it.
        if previous.is_ferry or current.is_ferry:
            continue
        gaps.append(current.index)
        worst = max(worst, separation)

    if gaps:
        return Check(
            name="connected_geometry",
            outcome=Outcome.VIOLATED,
            detail=f"{len(gaps)} unexplained gap(s), largest {worst:.0f} m",
            segment_indices=tuple(gaps),
        )
    return Check(name="connected_geometry", outcome=Outcome.SATISFIED)


def check_endpoints(route: RouteView) -> Check:
    """§7.10.3: the route must actually start and finish where asked."""
    if not route.segments or not route.segments[0].coordinates:
        return Check(
            name="correct_start_and_finish",
            outcome=Outcome.UNEVALUABLE,
            detail="route has no geometry to compare against the request",
        )

    start_gap = haversine_m(route.segments[0].coordinates[0], route.origin)
    end_gap = haversine_m(route.segments[-1].coordinates[-1], route.destination)

    problems = []
    if start_gap > ENDPOINT_TOLERANCE_M:
        problems.append(f"starts {start_gap:.0f} m from the requested origin")
    if end_gap > ENDPOINT_TOLERANCE_M:
        problems.append(f"finishes {end_gap:.0f} m from the requested destination")

    if problems:
        return Check(
            name="correct_start_and_finish",
            outcome=Outcome.VIOLATED,
            detail="; ".join(problems),
        )
    return Check(name="correct_start_and_finish", outcome=Outcome.SATISFIED)


def check_direction_restrictions(route: RouteView) -> Check:
    """§7.10.7: no segment ridden against a one-way restriction."""
    offending = [s.index for s in route.segments if s.traversed_against_oneway]
    if offending:
        return Check(
            name="direction_restrictions",
            outcome=Outcome.VIOLATED,
            detail=f"{len(offending)} segment(s) ridden against a one-way restriction",
            segment_indices=tuple(offending),
        )

    unknown = sum(1 for s in route.segments if s.oneway_bicycle is None and not s.is_ferry)
    if unknown == len(route.segments) and route.segments:
        return Check(
            name="direction_restrictions",
            outcome=Outcome.UNEVALUABLE,
            detail="no direction data on any segment",
        )
    return Check(name="direction_restrictions", outcome=Outcome.SATISFIED)


def check_ferry_links(route: RouteView) -> Check:
    """§7.10.8: ferry segments must be declared as ferries.

    Contour holds no ferry timetable, so this checks that a water crossing is
    identified as a ferry link — not that a sailing exists on any given day.
    Schedules are shown with their source and timestamp elsewhere and are never
    baked into stored geometry (§12.10).
    """
    ferries = [s for s in route.segments if s.is_ferry]
    if not ferries:
        return Check(name="valid_ferry_links", outcome=Outcome.SATISFIED)

    if not route.ferry_segments_declared:
        return Check(
            name="valid_ferry_links",
            outcome=Outcome.UNEVALUABLE,
            detail="ferry segments present but the source did not declare them as ferry links",
            segment_indices=tuple(s.index for s in ferries),
        )
    return Check(
        name="valid_ferry_links",
        outcome=Outcome.SATISFIED,
        detail=f"{len(ferries)} declared ferry link(s)",
    )


def check_route_length(route: RouteView) -> Check:
    """§7.10.9: a route with no length is not a route."""
    if route.distance_m <= 0:
        return Check(
            name="valid_route_length",
            outcome=Outcome.VIOLATED,
            detail="route has zero length",
        )
    return Check(name="valid_route_length", outcome=Outcome.SATISFIED)


def check_elevation_coverage(route: RouteView) -> Check:
    """§7.10.10: how much of the route has elevation at all.

    Never a failure on its own — a route without elevation is still a route.
    It is reported so that ascent figures and gradient constraints are read in
    the light of what could actually be measured.
    """
    total = route.distance_m
    if total <= 0:
        return Check(
            name="elevation_coverage", outcome=Outcome.UNEVALUABLE, detail="no route length"
        )

    measured = route.distance_where(lambda s: s.elevation_status is KnowledgeStatus.KNOWN)
    if measured == 0:
        return Check(
            name="elevation_coverage",
            outcome=Outcome.UNEVALUABLE,
            detail="no elevation data for any segment",
        )
    if measured < total:
        return Check(
            name="elevation_coverage",
            outcome=Outcome.UNEVALUABLE,
            detail=(
                f"{(total - measured) / 1000:.1f} km of {total / 1000:.1f} km has no elevation data"
            ),
        )
    return Check(name="elevation_coverage", outcome=Outcome.SATISFIED)


def check_water_crossings(route: RouteView) -> Check:
    """§7.10.5: no unexplained water crossing.

    An unexplained water crossing shows up in one of two ways: as a jump in the
    line with no ferry to explain it, which :func:`check_connected_geometry`
    detects; or as continuous geometry crossing a water body without a bridge or
    ferry, which needs a hydrography layer Contour does not currently connect.

    The second form is reported as NOT_AVAILABLE rather than UNEVALUABLE. The
    distinction is deliberate: this is a standing limitation of the deployment,
    identical for every route, not a fact missing about this particular one.
    Charging it against each route would make every result in the system read
    "satisfied with unknown data", and a warning that fires always is a warning
    nobody reads. It is surfaced instead on the Coverage screen as a capability
    Contour lacks.
    """
    return Check(
        name="no_unexplained_water_crossing",
        outcome=Outcome.NOT_AVAILABLE,
        detail=(
            "no hydrography source is connected, so geometry crossing water without "
            "a bridge or ferry cannot be detected; crossings that appear as gaps in "
            "the line are caught by the connectivity check"
        ),
    )


STRUCTURAL_CHECKS = (
    check_connected_geometry,
    check_endpoints,
    check_direction_restrictions,
    check_ferry_links,
    check_route_length,
    check_elevation_coverage,
    check_water_crossings,
)


# --- verdict -----------------------------------------------------------------


def decide_verdict(
    checks: Sequence[Check],
    constraint_results: Sequence[ConstraintResult],
    *,
    exhausted_attempts: bool = False,
) -> FeasibilityVerdict:
    """Combine checks and constraints into one verdict (§7.11).

    Order matters. A structurally broken route is ``NOT_FEASIBLE`` regardless of
    its constraints, because its metrics cannot be trusted at all. A route that
    is sound but breaks a hard constraint is ``PARTIALLY_SATISFIED`` while the
    solver still has attempts left, and ``NOT_FEASIBLE`` once it has run out —
    at which point Contour has established that it cannot meet the request, not
    merely that it has not yet.
    """
    if any(check.outcome is Outcome.VIOLATED for check in checks):
        return FeasibilityVerdict.NOT_FEASIBLE

    blocking = ConstraintSet.blocking(constraint_results)
    if blocking:
        return (
            FeasibilityVerdict.NOT_FEASIBLE
            if exhausted_attempts
            else FeasibilityVerdict.PARTIALLY_SATISFIED
        )

    unknown_hard = ConstraintSet.unevaluable_hard(constraint_results)
    # NOT_AVAILABLE deliberately excluded: see check_water_crossings.
    unknown_checks = [c for c in checks if c.outcome is Outcome.UNEVALUABLE]
    if unknown_hard or unknown_checks:
        return FeasibilityVerdict.SATISFIED_WITH_UNKNOWN_DATA

    return FeasibilityVerdict.FULLY_SATISFIED


def summarise(
    verdict: FeasibilityVerdict,
    checks: Sequence[Check],
    constraint_results: Sequence[ConstraintResult],
) -> str:
    """Plain-language summary, in the user's own terms where available."""
    violations = [r for r in constraint_results if r.outcome is Outcome.VIOLATED]
    failed_checks = [c for c in checks if c.outcome is Outcome.VIOLATED]

    if verdict is FeasibilityVerdict.FULLY_SATISFIED:
        return "Every requirement was checked against available data and met."

    parts: list[str] = []
    if failed_checks:
        parts.append(
            "This route is not usable as returned: "
            + "; ".join(c.detail or c.name for c in failed_checks)
        )
    for violation in violations:
        stated = violation.stated_as or violation.key.replace("_", " ")
        parts.append(f"{stated}: {violation.detail}" if violation.detail else stated)

    if verdict is FeasibilityVerdict.SATISFIED_WITH_UNKNOWN_DATA:
        unknown = [
            item for item in (*checks, *constraint_results) if item.outcome is Outcome.UNEVALUABLE
        ]
        details = "; ".join(item.detail for item in unknown if item.detail)
        return (
            "No requirement was found to be broken, but some could not be checked "
            f"because the data does not exist: {details}"
            if details
            else "Some requirements could not be checked because the data does not exist."
        )

    return " ".join(parts) if parts else "Route did not meet all requirements."


@dataclass
class RouteValidator:
    """Runs the structural checks and a constraint set over a route."""

    constraint_set: ConstraintSet = field(default_factory=ConstraintSet)

    def validate(
        self,
        route: RouteView,
        *,
        resolve_attempts: int = 1,
        exhausted_attempts: bool = False,
    ) -> ValidationOutcome:
        checks = tuple(check(route) for check in STRUCTURAL_CHECKS)
        results = tuple(self.constraint_set.evaluate(route))
        verdict = decide_verdict(checks, results, exhausted_attempts=exhausted_attempts)
        return ValidationOutcome(
            verdict=verdict,
            checks=checks,
            constraint_results=results,
            summary=summarise(verdict, checks, results),
            resolve_attempts=resolve_attempts,
            exhausted_attempts=exhausted_attempts,
        )
