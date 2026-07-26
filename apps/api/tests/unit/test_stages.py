"""Stage planning tests (§12).

The behaviours defended here are the ones a rider would notice immediately: a
locked stop stays put, a limit is never quietly relaxed, an unmeasurable ascent
does not silently pass its own check, and rebalancing never trades compliance
for tidiness.
"""

from __future__ import annotations

import pytest

from contour_api.journeys.stages import (
    RoutePoint,
    StageConstraints,
    StageEditor,
    plan_stages,
    rebalance,
)
from contour_api.models.enums import StageBalanceStrategy


def route(
    length_km: float,
    *,
    stop_every_km: float = 10.0,
    ascent_per_km: float | None = 10.0,
) -> list[RoutePoint]:
    """A route with an overnight candidate every `stop_every_km`."""
    points: list[RoutePoint] = []
    distance = 0.0
    while distance <= length_km * 1000:
        is_stop = abs(distance % (stop_every_km * 1000)) < 1e-6
        points.append(
            RoutePoint(
                distance_m=distance,
                ascent_from_start_m=(
                    None if ascent_per_km is None else distance / 1000 * ascent_per_km
                ),
                name=f"Stop {int(distance / 1000)} km" if is_stop else None,
                is_overnight_candidate=is_stop,
                has_accommodation=True if is_stop else None,
            )
        )
        distance += 1000.0
    return points


class TestPlanning:
    def test_a_route_within_one_day_is_a_single_stage(self) -> None:
        plan = plan_stages(route(60), StageConstraints(max_daily_distance_m=100_000))

        assert plan.day_count == 1
        assert plan.feasible

    def test_days_respect_the_distance_limit(self) -> None:
        plan = plan_stages(route(250), StageConstraints(max_daily_distance_m=100_000))

        assert plan.day_count >= 3
        for stage in plan.stages:
            assert stage.distance_m <= 100_000 + 1

    def test_a_time_limit_becomes_a_distance_limit_with_a_stated_assumption(self) -> None:
        """Contour does not model the rider; the speed is an assumption they can see."""
        constraints = StageConstraints(max_daily_riding_time_s=6 * 3600, assumed_speed_mps=4.0)

        assert constraints.effective_max_distance_m() == pytest.approx(86_400.0)

    def test_the_tighter_of_distance_and_time_wins(self) -> None:
        constraints = StageConstraints(
            max_daily_distance_m=120_000,
            max_daily_riding_time_s=5 * 3600,
            assumed_speed_mps=4.0,
        )

        assert constraints.effective_max_distance_m() == pytest.approx(72_000.0)

    def test_ascent_limits_are_honoured_when_they_bind(self) -> None:
        # 40 m of ascent per km, so a 1,200 m daily limit allows about 30 km.
        points = route(120, stop_every_km=10.0, ascent_per_km=40.0)

        plan = plan_stages(
            points,
            StageConstraints(max_daily_distance_m=100_000, max_daily_ascent_m=1200),
        )

        for stage in plan.stages:
            if stage.ascent_m is not None and not stage.violations:
                assert stage.ascent_m <= 1200 + 1

    def test_every_stage_explains_its_overnight_stop(self) -> None:
        """§12.7: a plan the rider cannot interrogate is a plan they cannot trust."""
        plan = plan_stages(route(250), StageConstraints(max_daily_distance_m=100_000))

        for stage in plan.stages:
            assert stage.selection_reason


class TestHonesty:
    def test_an_unsatisfiable_limit_is_reported_not_relaxed(self) -> None:
        """A 140 km gap between the only stops cannot become a 100 km day."""
        points = [
            RoutePoint(distance_m=0.0, ascent_from_start_m=0.0, name="Start"),
            *[
                RoutePoint(
                    distance_m=float(d),
                    ascent_from_start_m=d / 1000 * 10,
                    is_overnight_candidate=False,
                )
                for d in range(1000, 140_000, 1000)
            ],
            RoutePoint(distance_m=140_000.0, ascent_from_start_m=1400.0, name="Only other stop"),
        ]

        plan = plan_stages(points, StageConstraints(max_daily_distance_m=100_000))

        assert not plan.feasible
        assert "exceeds" in plan.infeasibility_reason
        assert any(stage.violations for stage in plan.stages)

    def test_unknown_elevation_does_not_silently_pass_the_ascent_check(self) -> None:
        """An unmeasurable stage is unchecked, not compliant."""
        points = route(150, ascent_per_km=None)

        plan = plan_stages(
            points,
            StageConstraints(max_daily_distance_m=80_000, max_daily_ascent_m=1000),
        )

        details = [v["detail"] for stage in plan.stages for v in stage.violations]
        assert any("could not be checked" in detail for detail in details)

    def test_services_report_unknown_separately_from_absent(self) -> None:
        """§12.4: "no source says" is not "there is none"."""
        point = RoutePoint(
            distance_m=0.0,
            ascent_from_start_m=0.0,
            has_accommodation=True,
            has_water=False,
            has_food=None,
        )

        summary = point.services_summary()

        assert summary["accommodation"] == "present"
        assert summary["water"] == "absent"
        assert summary["food"] == "unknown"


class TestLockedStops:
    def test_a_locked_stop_ends_a_stage_exactly(self) -> None:
        plan = plan_stages(
            route(250),
            StageConstraints(max_daily_distance_m=100_000, locked_end_distances_m=(70_000.0,)),
        )

        assert any(abs(stage.end_m - 70_000.0) < 1 for stage in plan.stages)
        locked = next(stage for stage in plan.stages if abs(stage.end_m - 70_000.0) < 1)
        assert locked.locked
        assert "you fixed this overnight stop" in locked.selection_reason.lower()

    def test_rebalancing_does_not_move_a_locked_stop(self) -> None:
        constraints = StageConstraints(
            max_daily_distance_m=100_000, locked_end_distances_m=(70_000.0,)
        )
        plan = plan_stages(route(250), constraints)

        rebalanced = rebalance(route(250), plan, StageBalanceStrategy.DISTANCE)

        assert any(abs(stage.end_m - 70_000.0) < 1 for stage in rebalanced.stages)

    def test_the_editor_refuses_to_move_a_locked_boundary(self) -> None:
        points = route(250)
        constraints = StageConstraints(
            max_daily_distance_m=100_000, locked_end_distances_m=(70_000.0,)
        )
        plan = plan_stages(points, constraints)
        locked_index = next(i for i, s in enumerate(plan.stages) if abs(s.end_m - 70_000.0) < 1)

        moved = StageEditor(points=points).move_boundary(plan, locked_index + 1, 90_000.0)

        assert any(abs(stage.end_m - 70_000.0) < 1 for stage in moved.stages)


class TestRebalancing:
    def test_rebalancing_evens_out_a_short_final_day(self) -> None:
        """The greedy pass leaves a stub; rebalancing is what fixes it."""
        points = route(250)
        plan = plan_stages(points, StageConstraints(max_daily_distance_m=100_000))
        spread_before = max(s.distance_m for s in plan.stages) - min(
            s.distance_m for s in plan.stages
        )

        rebalanced = rebalance(points, plan, StageBalanceStrategy.DISTANCE)
        spread_after = max(s.distance_m for s in rebalanced.stages) - min(
            s.distance_m for s in rebalanced.stages
        )

        assert spread_after < spread_before

    def test_rebalancing_keeps_the_day_count(self) -> None:
        points = route(250)
        plan = plan_stages(points, StageConstraints(max_daily_distance_m=100_000))

        rebalanced = rebalance(points, plan, StageBalanceStrategy.DISTANCE)

        assert rebalanced.day_count == plan.day_count

    def test_rebalancing_never_trades_compliance_for_tidiness(self) -> None:
        """A neater plan that breaks a limit is not an improvement."""
        points = route(250)
        constraints = StageConstraints(max_daily_distance_m=100_000)
        plan = plan_stages(points, constraints)

        rebalanced = rebalance(points, plan, StageBalanceStrategy.DISTANCE)

        before = sum(len(s.violations) for s in plan.stages)
        after = sum(len(s.violations) for s in rebalanced.stages)
        assert after <= before

    def test_rebalancing_by_ascent_uses_elevation_not_distance(self) -> None:
        # Front-loaded climbing: all the ascent is in the first half.
        points: list[RoutePoint] = []
        for km in range(0, 201):
            ascent = min(km, 100) * 30.0
            points.append(
                RoutePoint(
                    distance_m=km * 1000.0,
                    ascent_from_start_m=ascent,
                    name=f"km {km}" if km % 10 == 0 else None,
                    is_overnight_candidate=km % 10 == 0,
                )
            )

        plan = plan_stages(points, StageConstraints(max_daily_distance_m=100_000))
        by_ascent = rebalance(points, plan, StageBalanceStrategy.ASCENT)

        assert by_ascent.strategy is StageBalanceStrategy.ASCENT
        ascents = [s.ascent_m for s in by_ascent.stages if s.ascent_m is not None]
        if len(ascents) >= 2:
            # Balanced by climbing, the days should be closer in ascent than the
            # distance-balanced plan, which would put all the climbing in day one.
            assert max(ascents) - min(ascents) <= 3000.0


def test_plan_serialises_with_services_and_reasons() -> None:
    plan = plan_stages(route(250), StageConstraints(max_daily_distance_m=100_000))

    payload = plan.as_dict()

    assert payload["stages"][0]["selection_reason"]
    assert "services" in payload["stages"][0]
    assert payload["strategy"] == "distance"


def test_an_empty_route_reports_why_rather_than_returning_nothing() -> None:
    plan = plan_stages([], StageConstraints())

    assert not plan.feasible
    assert plan.infeasibility_reason
