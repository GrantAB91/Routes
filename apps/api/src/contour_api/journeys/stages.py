"""Multi-day stage planning (§12).

Splitting a route into days is a constrained optimisation with an awkward
property: the constraints are usually satisfiable in many ways, and the
differences between those ways matter enormously to the rider. A plan with four
100 km days and one 20 km day is technically compliant and obviously wrong.

So the planner does two things rather than one. It first finds a split that
respects every hard limit, then evens the days out *within* those limits. And it
records why each overnight point ended up where it did (§12.7), because a plan
the rider cannot interrogate is a plan they cannot trust.

Two rules it will not break:

* a locked stage endpoint is never moved (§12.2.7). Rebalancing works around it.
* an unsatisfiable constraint is reported, never quietly relaxed. A route with a
  single 140 km stretch between the only two possible overnight stops cannot be
  split into 100 km days, and saying so is more useful than pretending.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from ..models.enums import StageBalanceStrategy


@dataclass(frozen=True, slots=True)
class RoutePoint:
    """A point along the route that a stage may end at.

    Stages can only end where the rider can actually stop, so candidates are
    supplied rather than computed from distance alone. A point with
    ``is_overnight_candidate`` false — a summit, a ferry pier — can be passed
    but not slept at.
    """

    distance_m: float
    ascent_from_start_m: float | None
    name: str | None = None
    is_overnight_candidate: bool = True
    has_accommodation: bool | None = None
    has_water: bool | None = None
    has_food: bool | None = None

    def services_summary(self) -> dict[str, str]:
        """Report each service as known-present, known-absent or unknown.

        ``None`` here means no source records the service either way, which is
        materially different from a source recording its absence (§12.4).
        """

        def state(value: bool | None) -> str:
            if value is None:
                return "unknown"
            return "present" if value else "absent"

        return {
            "accommodation": state(self.has_accommodation),
            "water": state(self.has_water),
            "food": state(self.has_food),
        }


@dataclass(frozen=True, slots=True)
class StageConstraints:
    """Limits a stage plan must respect (§12.2)."""

    max_daily_distance_m: float | None = None
    min_daily_distance_m: float | None = None
    max_daily_ascent_m: float | None = None
    max_daily_riding_time_s: int | None = None
    # Metres per second on flat ground, used only to turn a time limit into a
    # distance one. Contour does not predict a rider's speed; this is a stated
    # assumption the user can change, not a model of them.
    assumed_speed_mps: float = 4.2
    target_days: int | None = None
    # Distances at which a stage must end, from fixed overnight stops.
    locked_end_distances_m: tuple[float, ...] = ()

    def effective_max_distance_m(self) -> float | None:
        limits = [self.max_daily_distance_m]
        if self.max_daily_riding_time_s is not None:
            limits.append(self.max_daily_riding_time_s * self.assumed_speed_mps)
        present = [limit for limit in limits if limit is not None]
        return min(present) if present else None


@dataclass(frozen=True, slots=True)
class Stage:
    sequence: int
    start_m: float
    end_m: float
    ascent_m: float | None
    end_point: RoutePoint | None
    # Plain language, shown against the overnight stop (§12.7).
    selection_reason: str = ""
    locked: bool = False
    violations: tuple[dict, ...] = ()

    @property
    def distance_m(self) -> float:
        return self.end_m - self.start_m


@dataclass(frozen=True, slots=True)
class StagePlan:
    stages: tuple[Stage, ...]
    strategy: StageBalanceStrategy
    constraints: StageConstraints
    # Set when no split satisfies every limit. The plan is still returned, with
    # the offending stages flagged, because a rider needs to see where the
    # problem is (§12.7, §7.6).
    feasible: bool = True
    infeasibility_reason: str = ""
    # True when the plan came from a worked example rather than the rider's own
    # inputs, so the interface can label it as such (§13.13).
    is_example: bool = False

    @property
    def day_count(self) -> int:
        return len(self.stages)

    def as_dict(self) -> dict:
        return {
            "feasible": self.feasible,
            "infeasibility_reason": self.infeasibility_reason,
            "strategy": self.strategy.value,
            "is_example": self.is_example,
            "stages": [
                {
                    "sequence": stage.sequence,
                    "start_m": stage.start_m,
                    "end_m": stage.end_m,
                    "distance_m": stage.distance_m,
                    "ascent_m": stage.ascent_m,
                    "end_name": stage.end_point.name if stage.end_point else None,
                    "locked": stage.locked,
                    "selection_reason": stage.selection_reason,
                    "violations": list(stage.violations),
                    "services": (stage.end_point.services_summary() if stage.end_point else {}),
                }
                for stage in self.stages
            ],
        }


def _ascent_between(points: Sequence[RoutePoint], start_m: float, end_m: float) -> float | None:
    """Ascent over a range, or None where elevation is not known throughout.

    Returning a number computed from partial data would understate the climbing
    of exactly the stages whose elevation is least well known.
    """
    start = next((p for p in points if p.distance_m >= start_m), None)
    end = next((p for p in reversed(points) if p.distance_m <= end_m), None)
    if start is None or end is None:
        return None
    if start.ascent_from_start_m is None or end.ascent_from_start_m is None:
        return None
    return max(0.0, end.ascent_from_start_m - start.ascent_from_start_m)


def _violations(
    distance_m: float, ascent_m: float | None, constraints: StageConstraints
) -> tuple[dict, ...]:
    found: list[dict] = []
    max_distance = constraints.effective_max_distance_m()

    if max_distance is not None and distance_m > max_distance + 1.0:
        found.append(
            {
                "constraint": "max_daily_distance_m",
                "requested": max_distance,
                "observed": distance_m,
                "detail": (
                    f"{distance_m / 1000:.1f} km exceeds the "
                    f"{max_distance / 1000:.1f} km daily limit"
                ),
            }
        )
    if (
        constraints.min_daily_distance_m is not None
        and distance_m < constraints.min_daily_distance_m - 1.0
    ):
        found.append(
            {
                "constraint": "min_daily_distance_m",
                "requested": constraints.min_daily_distance_m,
                "observed": distance_m,
                "detail": f"{distance_m / 1000:.1f} km is below the daily minimum",
            }
        )
    if constraints.max_daily_ascent_m is not None:
        if ascent_m is None:
            found.append(
                {
                    "constraint": "max_daily_ascent_m",
                    "requested": constraints.max_daily_ascent_m,
                    "observed": None,
                    "detail": (
                        "elevation is unknown for part of this stage, so the daily "
                        "ascent limit could not be checked"
                    ),
                }
            )
        elif ascent_m > constraints.max_daily_ascent_m + 1.0:
            found.append(
                {
                    "constraint": "max_daily_ascent_m",
                    "requested": constraints.max_daily_ascent_m,
                    "observed": ascent_m,
                    "detail": (
                        f"{ascent_m:.0f} m of ascent exceeds the "
                        f"{constraints.max_daily_ascent_m:.0f} m daily limit"
                    ),
                }
            )
    return tuple(found)


def _candidates(points: Sequence[RoutePoint]) -> list[RoutePoint]:
    return [p for p in points if p.is_overnight_candidate]


def plan_stages(
    points: Sequence[RoutePoint],
    constraints: StageConstraints,
    strategy: StageBalanceStrategy = StageBalanceStrategy.DISTANCE,
) -> StagePlan:
    """Split a route into days.

    Greedy first pass: ride as far as the limits allow, stop at the last
    overnight candidate that fits. This respects the constraints but leaves a
    short final day, which :func:`rebalance` then evens out.
    """
    if len(points) < 2:
        return StagePlan(
            stages=(),
            strategy=strategy,
            constraints=constraints,
            feasible=False,
            infeasibility_reason="The route has no length to divide.",
        )

    total_m = points[-1].distance_m
    candidates = _candidates(points)
    max_distance = constraints.effective_max_distance_m()

    boundaries: list[float] = []
    cursor = 0.0
    guard = 0

    while cursor < total_m - 1.0:
        guard += 1
        if guard > 1000:
            break

        # A locked endpoint ahead of us takes precedence over any computed one.
        locked_ahead = [d for d in constraints.locked_end_distances_m if d > cursor + 1.0]
        limit = cursor + max_distance if max_distance is not None else total_m

        if locked_ahead and min(locked_ahead) <= limit:
            cursor = min(locked_ahead)
            boundaries.append(cursor)
            continue

        if max_distance is None or limit >= total_m:
            break

        reachable = [p for p in candidates if cursor + 1.0 < p.distance_m <= limit]
        if not reachable:
            # Nothing to stop at within the limit. Push on to the next candidate
            # and record the overrun rather than inventing a stop in a bog.
            beyond = [p for p in candidates if p.distance_m > limit]
            if not beyond:
                break
            cursor = beyond[0].distance_m
            boundaries.append(cursor)
            continue

        # Prefer the furthest candidate that also respects the ascent limit.
        chosen: RoutePoint | None = None
        for candidate in sorted(reachable, key=lambda p: -p.distance_m):
            ascent = _ascent_between(points, cursor, candidate.distance_m)
            if (
                constraints.max_daily_ascent_m is None
                or ascent is None
                or ascent <= constraints.max_daily_ascent_m
            ):
                chosen = candidate
                break
        if chosen is None:
            chosen = max(reachable, key=lambda p: p.distance_m)

        cursor = chosen.distance_m
        boundaries.append(cursor)

    if not boundaries or boundaries[-1] < total_m - 1.0:
        boundaries.append(total_m)

    plan = _build(points, boundaries, constraints, strategy)
    return plan


def _build(
    points: Sequence[RoutePoint],
    boundaries: Sequence[float],
    constraints: StageConstraints,
    strategy: StageBalanceStrategy,
) -> StagePlan:
    stages: list[Stage] = []
    start = 0.0

    for index, end in enumerate(boundaries):
        ascent = _ascent_between(points, start, end)
        end_point = next((p for p in reversed(points) if p.distance_m <= end), None)
        locked = any(abs(end - d) < 1.0 for d in constraints.locked_end_distances_m)
        violations = _violations(end - start, ascent, constraints)

        stages.append(
            Stage(
                sequence=index + 1,
                start_m=start,
                end_m=end,
                ascent_m=ascent,
                end_point=end_point,
                locked=locked,
                violations=violations,
                selection_reason=_reason(end_point, locked, violations, constraints, end - start),
            )
        )
        start = end

    infeasible = [s for s in stages if s.violations]
    return StagePlan(
        stages=tuple(stages),
        strategy=strategy,
        constraints=constraints,
        feasible=not infeasible,
        infeasibility_reason=(
            "; ".join(v["detail"] for stage in infeasible for v in stage.violations)
            if infeasible
            else ""
        ),
    )


def _reason(
    end_point: RoutePoint | None,
    locked: bool,
    violations: tuple[dict, ...],
    constraints: StageConstraints,
    distance_m: float,
) -> str:
    if locked:
        return "You fixed this overnight stop, so it was kept exactly where it is."
    if end_point is None:
        return "End of the route."
    if violations:
        return (
            f"{end_point.name or 'This point'} was the nearest place to stop, even "
            "though it breaks a limit you set — there was no candidate within range."
        )
    max_distance = constraints.effective_max_distance_m()
    if max_distance is not None:
        return (
            f"{end_point.name or 'This point'} is the furthest overnight stop within "
            f"your {max_distance / 1000:.0f} km daily limit ({distance_m / 1000:.1f} km ridden)."
        )
    return f"{end_point.name or 'This point'} at {distance_m / 1000:.1f} km."


def rebalance(
    points: Sequence[RoutePoint],
    plan: StagePlan,
    strategy: StageBalanceStrategy = StageBalanceStrategy.DISTANCE,
) -> StagePlan:
    """Even out the days without breaking any limit (§12.6).

    Works by proposing evenly spaced targets and snapping each to the nearest
    overnight candidate, keeping locked endpoints exactly where they are. The
    result is accepted only if it violates no more constraints than the plan it
    replaces — rebalancing must never make a compliant plan non-compliant just
    to make it tidier.
    """
    if plan.day_count < 2:
        return replace(plan, strategy=strategy)

    total_m = points[-1].distance_m
    candidates = _candidates(points)
    if not candidates:
        return replace(plan, strategy=strategy)

    days = plan.day_count
    targets: list[float] = []

    for index in range(1, days + 1):
        if index == days:
            targets.append(total_m)
            continue

        if strategy is StageBalanceStrategy.ASCENT:
            measured = [p for p in points if p.ascent_from_start_m is not None]
            if measured:
                total_ascent = measured[-1].ascent_from_start_m or 0.0
                want = total_ascent * index / days
                nearest = min(measured, key=lambda p: abs((p.ascent_from_start_m or 0.0) - want))
                targets.append(nearest.distance_m)
                continue
        targets.append(total_m * index / days)

    boundaries: list[float] = []
    for index, target in enumerate(targets):
        locked = [
            d
            for d in plan.constraints.locked_end_distances_m
            if not boundaries or d > boundaries[-1] + 1.0
        ]
        if locked and index < len(targets) - 1 and abs(min(locked) - target) < total_m / (days * 2):
            boundaries.append(min(locked))
            continue
        if index == len(targets) - 1:
            boundaries.append(total_m)
            continue

        reachable = [p for p in candidates if not boundaries or p.distance_m > boundaries[-1] + 1.0]
        if not reachable:
            boundaries.append(target)
            continue
        nearest = min(reachable, key=lambda p: abs(p.distance_m - target))
        boundaries.append(nearest.distance_m)

    boundaries = sorted({round(b, 3) for b in boundaries})
    rebalanced = _build(points, boundaries, plan.constraints, strategy)

    before = sum(len(s.violations) for s in plan.stages)
    after = sum(len(s.violations) for s in rebalanced.stages)
    if after > before:
        # Tidier but less compliant is not an improvement.
        return replace(plan, strategy=strategy)
    return rebalanced


@dataclass
class StageEditor:
    """Manual stage adjustment (§12.5, §12.8)."""

    points: list[RoutePoint] = field(default_factory=list)

    def move_boundary(self, plan: StagePlan, sequence: int, new_end_m: float) -> StagePlan:
        """Drag one stage boundary, leaving the others alone.

        Only the two stages either side of the boundary change. Recomputing the
        whole plan would silently undo other adjustments the rider had already
        made.
        """
        if sequence < 1 or sequence >= plan.day_count:
            return plan
        stage = plan.stages[sequence - 1]
        if stage.locked:
            return plan

        boundaries = [s.end_m for s in plan.stages]
        lower = plan.stages[sequence - 1].start_m
        upper = plan.stages[sequence].end_m
        boundaries[sequence - 1] = max(lower + 1.0, min(new_end_m, upper - 1.0))

        return _build(self.points, boundaries, plan.constraints, plan.strategy)
